"""Current REPAIR intent is funded once; other reservations remain intact."""

from dataclasses import replace
from decimal import Decimal as D
from functools import partial
from types import SimpleNamespace as NS

import pytest

from polymaker.domain import Side
from polymaker.rdp_sm_ba import BidAbsorption
from polymaker.rdp_sm_execution import Placement, SMWindowExecutor, WindowQuote
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption, controller_fixture
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_repair_ba_tolerance import book, install, tolerant_setup

# Prices of all 15 ABORTED repair windows in the 2026-10-27 23:10 incident.
INCIDENT_PRICES = [
    ".78",
    ".78",
    ".94",
    ".97",
    ".98",
    ".97",
    ".97",
    ".97",
    ".96",
    ".98",
    ".96",
    ".98",
    ".97",
    ".97",
    ".98",
]


def funding_fixture(price=".78", *, tolerant=True, held="NO"):
    c, f, clock = controller_fixture(
        delta="-5" if held == "NO" else "5", signal_lifetime_sec=3, max_book_age_ms=200
    )
    if tolerant:
        install(c, f)
    e = c.executor
    e.attempts, e.proofs, e.terminal, e.known = [], {}, set(), set()
    e.merged_cost = {}
    fills = []
    # Actual five filled orders before the incident's third-cycle repair.
    for i, (side, p) in enumerate(
        [("NO", ".56"), ("YES", ".61"), ("NO", ".52"), ("YES", ".74"), ("NO", ".21")]
    ):
        if held == "YES":
            side = "YES" if side == "NO" else "NO"
        token = "1" if side == "YES" else "2"
        oid = f"history-{i}"
        q = WindowQuote(f.meta.condition_id, token, side, Side.BUY, float(p), 5, oid)
        e.attempts.append(Placement(oid, q, outcome="ACK", order_id=oid, post_started=True))
        e.proofs[oid] = True
        e.terminal.add(oid)
        e.known.add(oid)
        fills.append(NS(maker_order_id=oid, token_id=token, price=float(p), size=5))
    e.economic_fills = lambda: fills
    e.economic_inventory = lambda: (D(10), D(15)) if held == "NO" else (D(15), D(10))
    e.committed = partial(SMWindowExecutor.committed, e)
    assert e.committed() == D("13.20")
    side, token = ("YES", "1") if held == "NO" else ("NO", "2")
    clock.now = 0.01
    tick = f.books[token].tick
    f.apply(book(f, D(price), D(price) + tick, token))
    absorption(f, token)
    snapshot = f.snapshot()
    direction, signal = c.select(snapshot)
    assert direction == side
    y, n = e.economic_inventory()
    quotes = c.candidates(snapshot, y, n, "funding-case")[side][0]
    claim = c.episodes.claim(signal)
    intent = NS(window_id="funding-case", direction=side, yes=y, no=n, quotes=quotes, claim=claim)
    pending = Placement("current", quotes[0], expected_order_id="signed-current")
    return c, f, snapshot, intent, pending


@pytest.mark.parametrize("price", INCIDENT_PRICES)
@pytest.mark.parametrize("tolerant", [False, True])
def test_all_incident_repairs_match_with_one_current_reservation(price, tolerant):
    c, _, snapshot, intent, pending = funding_fixture(price, tolerant=tolerant)
    e = c.executor
    assert c.matches(intent, snapshot)
    e.attempts.append(pending)
    reserved = e.committed()
    assert reserved == D("13.20") + D(price) * 5
    assert c.matches_for_submit(intent, snapshot, [pending])
    assert e.committed() == reserved  # No global release or ledger mutation.
    assert pending.outcome == "INTENT" and not pending.post_started
    assert not c.episodes.selected.consumed


def test_opposite_direction_uses_same_funding_rule():
    c, _, snapshot, intent, pending = funding_fixture(held="YES")
    c.executor.attempts.append(pending)
    assert c.matches_for_submit(intent, snapshot, [pending])


@pytest.mark.parametrize("extra,allowed", [("2.90", True), ("2.91", False)])
@pytest.mark.parametrize("outcome", ["INTENT", "UNKNOWN", "ACK"])
def test_other_order_risk_still_counts_at_exact_session_limit(extra, allowed, outcome):
    c, _, snapshot, intent, pending = funding_fixture()
    e = c.executor
    other = Placement(
        "other",
        replace(pending.quote, price=float(D(extra) / 5), window_id="other"),
        outcome=outcome,
        expected_order_id="other-signed",
        post_started=outcome != "INTENT",
    )
    e.attempts.extend([other, pending])
    before = e.committed()
    assert c.matches_for_submit(intent, snapshot, [pending]) is allowed
    if not allowed:
        assert c.quote_block_reason == "OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT"
    assert e.committed() == before


@pytest.mark.parametrize("cash,allowed", [("3.90", True), ("3.89", False)])
def test_official_cash_is_not_credited_with_session_reservation(cash, allowed):
    c, _, snapshot, intent, pending = funding_fixture()
    c.executor.attempts.append(pending)
    c.lifecycle.account = {"basic_spendable_budget_usd": cash}
    assert c.matches_for_submit(intent, snapshot, [pending]) is allowed
    if not allowed:
        assert c.quote_block_reason == "OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT"


@pytest.mark.parametrize(
    "bad",
    [
        "unowned",
        "duplicate",
        "wrong_quote",
        "wrong_window",
        "started",
        "ack",
        "unknown",
        "aborted",
        "order_id",
        "known",
        "unsigned",
    ],
)
def test_credit_requires_exact_owned_unsent_batch(bad):
    c, _, snapshot, intent, pending = funding_fixture()
    e = c.executor
    e.attempts.append(pending)
    batch = [pending]
    if bad == "unowned":
        batch = [replace(pending)]
    elif bad == "duplicate":
        batch *= 2
    elif bad == "wrong_quote":
        pending.quote = replace(pending.quote, size=6)
    elif bad == "wrong_window":
        intent.window_id = "wrong-owner"
    elif bad == "started":
        pending.post_started = True
    elif bad in {"ack", "unknown", "aborted"}:
        pending.outcome = bad.upper()
    elif bad == "order_id":
        pending.order_id = "accepted-id"
    elif bad == "known":
        e.known.add(pending.expected_order_id)
    else:
        pending.expected_order_id = None
    assert not c.matches_for_submit(intent, snapshot, batch)
    assert c.quote_block_reason == "REPAIR_RESERVATION_CONTEXT_INVALID"


def test_empty_context_cannot_release_current_reservation():
    c, _, snapshot, intent, pending = funding_fixture()
    c.executor.attempts.append(pending)
    assert not c.matches_for_submit(intent, snapshot, [])


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("filled", [False, True])
async def test_native_repair_submits_at_high_prior_commitment_and_keeps_reserve(
    env, monkeypatch, tmp_path, cached, filled
):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, f, _, _, wire = setup[:6]
    companion = None
    try:
        e.cfg.rdp_sm.normal_entry_mode = "ba_v1"  # Production 20-USDC session cap.
        f.ba = BidAbsorption(f, e.cfg.rdp_sm.ba)
        assert e.capital_limit == 20
        # Isolate the real SDK/guard path with 11.70 of prior session risk in
        # addition to the fixture's actual 1.50 opening fill. Unit cases above
        # independently reconstruct the incident using the real full ledger.
        committed = e.committed
        monkeypatch.setattr(e, "committed", lambda: committed() + D("11.70"))
        assert e.committed() == D("13.20")
        setup[-2].update(no_bid=".78", no_ask=str(D(".78") + f.books["2"].tick))
        f.apply(book(f, ".78", D(".78") + f.books["2"].tick))
        if cached:
            companion, _ = await attach(setup, tmp_path)
        absorption(f, "2")
        ep = next(iter(c.episodes.opportunities.values()))
        seen = []

        def during_post():
            seen.append(e.committed())
            assert e.committed() == D("17.10")
            assert e.attempts[-1].post_started

        wire.delay = during_post
        if filled:

            def fill_on_cancel(venue):
                oid = e.attempts[-1].expected_order_id
                if not venue.rows[oid]["associate_trades"]:
                    venue.fill(oid, "funding-fixed-fill", 5)
                reflect_positions(venue)

            v.on_cancel = fill_on_cancel
        outcome = await c.repair(*e.economic_inventory(), "high-prior-funding")
        assert outcome is not None, c.lifecycle.wait_reason
        result, sizing = outcome
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2 and seen == [D("17.10")]
        assert result.placement_results[0]["post_started"]
        assert D(str(result.requested_quotes[0].price)) == D(".78")
        assert result.fill_deadline_monotonic == ep.order_deadline and sizing["quote_fixed"]
        assert e.economic_inventory() == (D(5), D(5) if filled else D(0))
        assert e.committed() == (D("17.10") if filled else D("13.20"))
        logs = journal_records(e.journal.path)
        close = next(
            r["data"]["payload"]
            for r in logs
            if r["kind"] == "sm_ba_close_requested"
            and r["data"]["payload"]["window_id"] == "high-prior-funding"
        )
        assert close["reason"] == "ORDER_DEADLINE"
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


async def test_final_native_guard_reports_real_insufficient_funds_reason(env, monkeypatch):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, f = setup[:3]
    try:
        e.cfg.rdp_sm.normal_entry_mode = "ba_v1"
        f.ba = BidAbsorption(f, e.cfg.rdp_sm.ba)
        absorption(f, "2")
        send = e.gateway.send_prepared

        async def drain_cash_before_guard(*args, **kwargs):
            c.lifecycle.account["basic_spendable_budget_usd"] = 0
            return await send(*args, **kwargs)

        monkeypatch.setattr(e.gateway, "send_prepared", drain_cash_before_guard)
        result, _ = await c.repair(*e.economic_inventory(), "real-cash-shortage")
        assert len(v.post_requests) == 1
        assert not result.placement_results[0]["post_started"]
        logs = journal_records(e.journal.path)
        intent = next(
            r["data"]["payload"]["intent"]
            for r in logs
            if r["kind"] == "sm_opposite_ba_final_decision"
        )
        assert intent["aborted_reason"] == "OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT"
    finally:
        await cleanup(setup)
