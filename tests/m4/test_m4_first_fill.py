"""First-fill manual trial: synthetic orders/reads only, no real credentials."""

import asyncio
import json
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest
import test_m4_live_taker as legacy
from m4_helpers import close, op
from test_m4_live_budget import fixture
from test_m4_live_startup import EPOCH, harness
from test_m4_live_task import evidence, serve

from polymaker.m4_live_first_fill import FirstFillMarket, FirstFillTrial, inspect_ledger


async def trial_fixture(tmp_path, monkeypatch, **kwargs):
    original = legacy.M4LiveLedger

    def ledger(path, *args, **kw):
        return original(tmp_path / "ledgers" / f"btc-updown-5m-{EPOCH}.sqlite3", *args, **kw)

    monkeypatch.setattr(legacy, "M4LiveLedger", ledger)
    monkeypatch.setattr(legacy, "M4TakerMarket", FirstFillMarket)
    result = await fixture(tmp_path, monkeypatch, **kwargs)
    trial = FirstFillTrial(tmp_path / "manual-runs/test/campaign.json", tmp_path)
    # Production binds before collecting; synthetic fixture has already collected.
    result[0].ledger.set("first_fill_trial_id", trial.data["trial_id"])
    trial.bind(result[0].ledger, result[1].slug)
    return trial, result


async def test_one_signal_only_and_partial_buy_keeps_original_cash_target(tmp_path, monkeypatch):
    trial, (m, e, f, c, calls, t) = await trial_fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    q = None
    try:
        second = op(999, price=".30", token="2")
        monkeypatch.setattr(f.m1, "evaluate", lambda _: None)
        f.m1.selected = second
        m.collect()
        assert len(m.ledger.rows()) == 1  # including both candidates in one scheduler tick
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        assert D(m.ledger.row(ep.episode_id)["filled"]) == 5
        m.collect()
        assert len(m.ledger.rows()) == 1 and not m.admission_allowed()
        await m.submit(ep)
        assert len(calls) == 2  # same signal receives a budget-limited residual
        assert D(m.ledger.plan(ep.episode_id)["prior_spent_usdc"]) > 0
        assert D(m.ledger.plan(ep.episode_id)["prior_spent_usdc"]) + D(
            m.ledger.plan(ep.episode_id)["planned_cost_usdc"]) <= 5
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


@pytest.mark.parametrize("response", ["ACK", "TIMEOUT", "KILLED"])
async def test_receipt_is_not_first_fill_and_unknown_blocks_next_market(tmp_path, monkeypatch, response):
    trial, (m, e, f, c, calls, t) = await trial_fixture(tmp_path, monkeypatch, response=response)
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        assert inspect_ledger(m.ledger.path)["fills"] == []
        if response == "KILLED":
            await m.submit(ep)
            assert len(calls) == 2  # affirmative zero can retry
        c.now += 300
        m.finish_pending()
        m.complete_window()
        result = trial.finish_market(m.ledger, True)
        if response == "KILLED":
            assert result is None
        else:
            assert result["status"] == "FIRST_FILL_UNRESOLVED"
            assert trial.resume()[0]["status"] == "FIRST_FILL_UNRESOLVED"
            assert len(calls) == 1
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("qty,status", [("5", "CONFIRMED"), ("5", "MINED"), ("0", "CONFIRMED")])
async def test_end_result_and_restart_use_confirmed_fills_not_ack_or_pending(tmp_path, monkeypatch, qty, status):
    trial, (m, e, f, c, calls, t) = await trial_fixture(tmp_path, monkeypatch, price=".30")
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, qty, status=status)
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        c.now += 300
        m.finish_pending()
        m.complete_window()
        result = trial.finish_market(m.ledger, True)
        reloaded = FirstFillTrial(trial.path, tmp_path)
        resumed, recovery = reloaded.resume()
        if qty == "0":
            assert result is resumed is recovery is None
        elif status == "MINED":
            assert result["status"] == resumed["status"] == "FIRST_FILL_UNRESOLVED"
        else:
            assert result["status"] == resumed["status"] == "FIRST_FILL_COMPLETED"
            assert D(resumed["fills"][0]["filled"]) == 5
        assert len(calls) == 1  # no POST after T240 or re-reading a finished trial
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_restart_retains_same_signal_unknown_and_recovery_market(tmp_path, monkeypatch):
    trial, (m, e, f, c, calls, t) = await trial_fixture(tmp_path, monkeypatch, response="TIMEOUT")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        trial.finish_market(m.ledger, False)
        resumed, slug = FirstFillTrial(trial.path, tmp_path).resume()
        assert resumed is None and slug == e.slug
        assert not m.admission_allowed()
        assert len(calls) == 1
        other = FirstFillTrial(tmp_path / "manual-runs/other/campaign.json", tmp_path)
        assert other.resume()[0]["status"] == "PRIOR_OWNED_ORDERS_UNRESOLVED"
    finally:
        await close(m, e)
        t.close()


async def test_filled_dust_does_not_admit_second_signal(tmp_path, monkeypatch):
    trial, (m, e, f, c, calls, t) = await trial_fixture(tmp_path, monkeypatch)
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, m.ledger.plan(ep.episode_id)["quantity"])
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        assert m.ledger.row(ep.episode_id)["state"] == "DUST"
        assert not m.admission_allowed()
        await m.submit(ep)
        assert len(calls) == 1
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


@pytest.mark.parametrize("outcome", ["filled", "unknown", "exception", "retained", "fatal"])
async def test_runner_advances_empty_markets_and_stops_without_third_market(tmp_path, monkeypatch, outcome):
    from polymaker import m4_live_first_fill, m4_live_http, m4_live_startup, m4_run
    from polymaker.config import Config
    from polymaker.rdp_sm import OFFICIAL_HOSTS

    h = harness(tmp_path, monkeypatch)
    h.clock_values[:] = [EPOCH + 1, EPOCH + 2, EPOCH + 301, EPOCH + 302]
    (tmp_path / "config/m4-live").mkdir(parents=True)
    (tmp_path / "config/m4.toml").write_text("")
    (tmp_path / "config/m4-live/execution.toml").write_text('mode="taker"\n')
    config = SimpleNamespace(wallet=SimpleNamespace(**OFFICIAL_HOSTS, chain_id=137),
                             paths=SimpleNamespace(), proxy=None)
    config.model_copy = lambda **_: config
    monkeypatch.setattr(Config, "load", lambda *_: config)
    monkeypatch.setattr(m4_live_startup, "LivePublicAPI", lambda: h.startup.api)
    monkeypatch.setattr(m4_live_startup, "LiveStartup", lambda *args: h.startup)
    monkeypatch.setattr(m4_live_http, "M4LiveReads", h.reads_type)
    runs = []

    async def no_socket(self):
        await asyncio.Event().wait()

    class Market:
        def __init__(self, e, *args, **kwargs):
            self.e = e

        async def run(self):
            runs.append(self.e.slug)
            ledger = self.e.ledger
            if len(runs) == 2:
                if outcome == "exception":
                    raise httpx.ReadTimeout("synthetic active-market failure")
                if outcome == "fatal":
                    self.e.fatal = "SYNTHETIC_FATAL"
                row = ledger.observe(op(1), minimum=5, elapsed=20)
                if outcome == "filled":
                    # Scheduler test only; SDK-level evidence is tested above.
                    with ledger.db:
                        ledger.db.execute("UPDATE opportunities SET filled='1',spent='.7' WHERE id=?", (row["id"],))
                elif outcome == "unknown":
                    ledger.plan_signal(row["id"], D(".01"), 5)
                    ledger.reserve(row["id"], spendable=40)
                    ledger.next_plan(row["id"], minimum=5, spendable=40)
                    plan = ledger.plan(row["id"])
                    ledger.prepare(row["id"], "0x" + "a" * 64, {
                        "market_id": self.e.meta.condition_id, "window_id": row["id"],
                        "outcome": row["outcome"], "side": "BUY", "token_id": row["token"],
                        "price": plan["limit_price"], "size": plan["quantity"]})
                    ledger.post_start(row["id"])
            clean = len(runs) == 1 or outcome != "retained"
            ledger.set("complete", clean)
            return clean

    monkeypatch.setattr(m4_run.M4Feed, "run", no_socket)
    monkeypatch.setattr(m4_live_first_fill, "FirstFillMarket", Market)
    state = h.startup.root / "manual-runs/test/campaign.json"
    if outcome == "exception":
        with pytest.raises(httpx.ReadTimeout):
            await m4_run.run(tmp_path, live=True, until_first_fill=state)
    else:
        result = await m4_run.run(tmp_path, live=True, until_first_fill=state)
        assert result["status"] == {"filled": "FIRST_FILL_COMPLETED",
                                    "unknown": "FIRST_FILL_UNRESOLVED", "retained": "RETAINED",
                                    "fatal": "FIRST_FILL_FATAL"}[outcome]
    assert len(runs) == 2 and all(r.closed for r in h.readers)
    assert json.loads(state.read_text())["markets"] == runs


async def test_trial_option_cannot_be_used_by_baseline(tmp_path):
    from polymaker.m4_run import run

    with pytest.raises(ValueError, match="REQUIRES_LIVE_WITHOUT_ONCE"):
        await run(tmp_path, until_first_fill=tmp_path / "unused.json")
    with pytest.raises(ValueError, match="REQUIRES_LIVE_WITHOUT_ONCE"):
        await run(tmp_path, live=True, once=True, until_first_fill=tmp_path / "unused.json")


def test_historical_untracked_maker_ack_is_not_a_trial_fill_or_taker_attempt(tmp_path):
    from m4_helpers import identity, send

    from polymaker.m4_state import M4Ledger

    ledger = M4Ledger(tmp_path / "ledgers" / f"btc-updown-5m-{EPOCH-300}.sqlite3", identity("LIVE"))
    try:
        send(ledger, 7, state="ACK")
        before = ledger.rows()
        trial = FirstFillTrial(tmp_path / "manual-runs/test/campaign.json", tmp_path)
        assert trial.resume() == (None, None)
        assert ledger.rows() == before
        assert inspect_ledger(ledger.path)["fills"] == []
    finally:
        ledger.close()
