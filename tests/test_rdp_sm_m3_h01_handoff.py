"""Real M1/M3/M2 handoff; only official HTTP responses and clocks are fake."""

from copy import deepcopy
from decimal import Decimal

import httpx
import pytest
from py_clob_client_v2.http_helpers import helpers

from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_sm_execution import SMWindowExecutor, _ExecutionReads
from test_rdp_sm_m1 import START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m2_remediation import external_order
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows, runner
from test_rdp_sm_type2_account import type2


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.fixture
def evidence(monkeypatch):
    """Delegating instrumentation: no fake internal component or result."""
    readers, snapshots, placements = [], [], []
    connect, snapshot, place = (
        _ExecutionReads.connect_account,
        SMWindowExecutor._snapshot,
        ExecutionGateway.place,
    )

    async def observe_connect(self):
        readers.append(self)
        await connect(self)

    async def observe_snapshot(self, **kwargs):
        result = await snapshot(self, **kwargs)
        snapshots.append(deepcopy(result[-1]))
        return result

    async def observe_place(self, *args, **kwargs):
        placements.append(args)
        return await place(self, *args, **kwargs)

    monkeypatch.setattr(_ExecutionReads, "connect_account", observe_connect)
    monkeypatch.setattr(SMWindowExecutor, "_snapshot", observe_snapshot)
    monkeypatch.setattr(ExecutionGateway, "place", observe_place)
    return readers, snapshots, placements


def account(v, balance="6000000", price=".9"):
    v.capital["balance"] = balance
    v.rows["external"] = external_order(price)
    v.books["1"]["bids"][0]["price"] = ".50"  # real NORMAL BUY5 @ .49 = $2.45


def listing_transport(monkeypatch, v, listing):
    original = v.response
    count = 0

    def response(request):
        nonlocal count
        if request.url.path == "/data/orders":
            count += 1
            return httpx.Response(
                200,
                json={"data": deepcopy(listing(count)), "next_cursor": "LTE="},
                request=request,
            )
        return original(request)

    client = httpx.Client(transport=httpx.MockTransport(response))
    monkeypatch.setattr(helpers, "_http_client", client)
    return client


def own_live(v):
    return [r for oid, r in v.rows.items() if oid.startswith("o-") and r["status"] == "LIVE"]


@pytest.mark.parametrize("start,jump", [(60, None), (0, None), (0, 60)])
async def test_original_preflight_risk_survives_waiting_and_missing_listings(
    env, evidence, monkeypatch, start, jump
):
    v, _, _ = type2(env)
    account(v)
    v.time = START + start
    if jump:

        async def advance(venue):
            if venue.time < START + jump:
                venue.mono += START + jump - venue.time
                venue.time = START + jump

        v.on_sleep = advance
    with listing_transport(monkeypatch, v, lambda n: [v.rows["external"]] if n == 1 else []):
        result = await runner(env).run(offline=True)
    readers, snapshots, placements = evidence
    assert not placements and not v.posts and not v.cancels
    assert result.execution_status == "BLOCKED"
    assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert result.total_windows == result.blocked_windows == 1
    assert result.windows[0]["result"]["close_barrier"] == "BLOCKED"
    assert result.windows[0]["elapsed_s"] == (jump or max(start, 15))
    assert snapshots and all(a["execution_reserved_usd"] == 4.5 for a in snapshots)
    assert all(a["basic_spendable_budget_usd"] == 1.5 for a in snapshots)
    # All successive clients retain the same exchange ID, including the two
    # fresh readers constructed by the real M2 factory, not just a local flag.
    assert len(readers) >= 3
    assert all("external" in r.gateway._client.observed_open_rows for r in readers)
    sdk = v.executor.gateway._client
    assert len(sdk.observed_open_rows) == 1
    assert Decimal(sdk.observed_open_rows["external"]["price"]) * Decimal(
        sdk.observed_open_rows["external"]["original_size"]
    ) == Decimal("4.5")
    assert v.rows["external"]["status"] == "LIVE"
    assert not v.executor.confirmed() and v.executor.committed() == 0
    session = next(r for r in journal_records(v.executor.journal.path) if r["kind"] == "sm_session")
    assert session["data"]["payload"]["preflight_observed_order_ids"] == ["external"]


async def test_verified_no_fill_release_uses_existing_m2_proof(env, evidence, monkeypatch):
    v, _ = env
    account(v)

    def listing(n):
        if n == 1:
            return [v.rows["external"]]
        v.rows["external"]["status"] = "CANCELED"
        return own_live(v)

    on_windows(v, lambda *_: None, stop_after=1)
    with listing_transport(monkeypatch, v, listing):
        result = await runner(env).run(offline=True)
    _, snapshots, placements = evidence
    assert len(placements) == len(v.post_requests) == 1 and len(v.posts) == 2
    assert len(placements[0][0]) == 2
    assert (
        result.execution_status == "COMPLETED" and result.final_close_barrier == "SAFE_TO_CONTINUE"
    )
    assert snapshots[0]["execution_reserved_usd"] == 0
    assert snapshots[0]["basic_spendable_budget_usd"] == 6
    assert result.confirmed_yes == result.confirmed_no == 0
    assert "external" in v.executor.gateway._client.observed_open_rows
    assert "external" not in v.executor.known
    assert all("external" not in ids for ids in v.cancels)
    assert any(path == "/data/order/external" for _, path, _ in v.calls)
    assert any(path == "/data/trades" for _, path, _ in v.calls)
    assert any(
        r["kind"] == "sm_external_no_fill_terminal"
        for r in journal_records(v.executor.journal.path)
    )


async def test_duplicate_evidence_and_batch_sm_commitment_count_once(
    env, evidence, monkeypatch
):
    v, _ = env
    account(v, balance="7400000", price=".49")
    on_windows(v, lambda *_: None, stop_after=1)
    with listing_transport(monkeypatch, v, lambda _: [v.rows["external"], *own_live(v)]):
        result = await runner(env).run(offline=True)
    _, snapshots, placements = evidence
    assert len(placements) == len(v.post_requests) == 1 and len(v.posts) == 2
    assert len(placements[0][0]) == 2
    assert result.execution_status == "COMPLETED"
    assert snapshots[0]["execution_reserved_usd"] == 2.45
    assert snapshots[0]["basic_spendable_budget_usd"] == 4.95
    # No snapshot occurs between the two legs. Both are admitted together
    # against $4.95 available after the retained $2.45 external commitment.
    intents = [r["data"]["payload"] for r in journal_records(v.executor.journal.path)
               if r["kind"] == "sm_placement_intent"]
    assert len(intents) == 2 and all(Decimal(a["capital_committed_usd"]) == Decimal("4.845") for a in intents)
    assert all(a["execution_reserved_usd"] == 2.45 for a in snapshots)
    assert list(v.executor.gateway._client.observed_open_rows).count("external") == 1
    assert v.executor.known == {"o-1", "o-2"}
    assert v.rows["external"]["status"] == "LIVE"


async def test_m2_additional_commitment_does_not_replace_m1_evidence(env, evidence, monkeypatch):
    v, _ = env
    account(v)
    v.rows["external2"] = {**external_order(".1"), "id": "external2", "asset_id": "4"}
    with listing_transport(
        monkeypatch, v, lambda n: [v.rows["external" if n == 1 else "external2"]]
    ):
        result = await runner(env).run(offline=True)
    _, snapshots, placements = evidence
    assert not placements and not v.posts
    assert snapshots[0]["execution_reserved_usd"] == 5
    assert snapshots[0]["basic_spendable_budget_usd"] == 1
    assert {"external", "external2"} <= v.executor.gateway._client.observed_open_rows.keys()
    assert (
        result.execution_status == "BLOCKED"
        and result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    )


async def test_preflight_pending_fill_cannot_be_released_by_zero_fill_replica(
    env, evidence, monkeypatch
):
    v, _ = env
    account(v)
    v.rows["external"]["size_matched"] = "1"
    v.rows["external"]["associate_trades"] = ["pending"]

    def listing(n):
        if n == 1:
            return [v.rows["external"]]
        v.rows["external"].update(status="CANCELED", size_matched="0", associate_trades=[])
        return []

    with listing_transport(monkeypatch, v, listing):
        result = await runner(env).run(offline=True)
    _, snapshots, placements = evidence
    assert not placements and not v.posts
    assert all(a["execution_reserved_usd"] == 4.5 for a in snapshots)
    assert v.executor.gateway._client.observed_open_rows["external"]["_sm_fill_observed"]
    assert not v.executor.confirmed() and result.confirmed_yes == result.confirmed_no == 0
    assert result.execution_status == "BLOCKED"


async def test_incomplete_preflight_retains_page_and_never_opens_executor(
    env, evidence, monkeypatch
):
    v, _ = env
    account(v)
    original = v.response

    def response(request):
        if request.url.path == "/data/orders":
            if request.url.params.get("next_cursor") == "MA==":
                return httpx.Response(
                    200,
                    json={"data": [v.rows["external"]], "next_cursor": "page2"},
                    request=request,
                )
            raise httpx.ReadTimeout("offline incomplete preflight page2")
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr(helpers, "_http_client", client)
        result = await runner(env).run(offline=True)
    readers, snapshots, placements = evidence
    assert len(readers) == 1 and not snapshots and not placements and not v.posts
    assert readers[0].gateway._client.observed_open_rows["external"]["price"] == ".9"
    assert readers[0].gateway._client.pagination_failure == "OPEN_ORDERS_PAGINATION_INCOMPLETE"
    assert v.executor is None and result.total_windows == 0
    assert result.execution_status == "BLOCKED" and result.final_close_barrier == "BLOCKED"


async def test_waiting_until_cutoff_does_not_report_completed_with_retained_risk(
    env, evidence, monkeypatch
):
    v, _ = env
    account(v)
    v.time = START

    async def advance(venue):
        venue.time = START + 300
        venue.mono = 300

    v.on_sleep = advance
    with listing_transport(monkeypatch, v, lambda n: [v.rows["external"]] if n == 1 else []):
        result = await runner(env).run(offline=True)
    readers, snapshots, placements = evidence
    assert len(readers) == 2 and all(
        "external" in r.gateway._client.observed_open_rows for r in readers
    )
    assert not placements and not snapshots and not v.posts and v.executor is None
    assert result.execution_status == "BLOCKED" and result.final_close_barrier == "BLOCKED"
    assert result.stop_reason == "PREFLIGHT_RISK_UNRESOLVED_AT_CUTOFF"


async def test_unknown_official_state_does_not_clear_handed_off_risk(env, evidence, monkeypatch):
    v, _ = env
    account(v)
    original = v.response

    def response(request):
        if request.url.path == "/data/order/external":
            raise httpx.ReadTimeout("offline unknown external state")
        return original(request)

    monkeypatch.setattr(v, "response", response)
    with listing_transport(monkeypatch, v, lambda n: [v.rows["external"]] if n == 1 else []):
        result = await runner(env).run(offline=True)
    _, snapshots, placements = evidence
    assert not placements and not v.posts
    assert snapshots and all(a["execution_reserved_usd"] == 4.5 for a in snapshots)
    assert (
        result.execution_status == "BLOCKED"
        and result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    )


async def test_account_identity_change_during_wait_cannot_import_old_account_risk(
    env, evidence, monkeypatch
):
    v, cfg = env
    account(v)
    v.time = START

    async def change_account(venue):
        cfg.secrets.pk = "0x" + "02" * 32  # a different public dummy key, no live credentials
        venue.time = START + 60
        venue.mono = 60

    v.on_sleep = change_account
    with listing_transport(monkeypatch, v, lambda n: [v.rows["external"]] if n == 1 else []):
        result = await runner(env).run(offline=True)
    readers, snapshots, placements = evidence
    assert not placements and not snapshots and not v.posts and v.executor is None
    assert "external" in readers[0].gateway._client.observed_open_rows
    assert result.execution_status == "BLOCKED" and result.final_close_barrier == "BLOCKED"
