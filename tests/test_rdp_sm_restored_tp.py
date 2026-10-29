"""Default TP restoration through real signing, serial cancellation and fill proofs."""

import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPCAMatchedTPConfig
from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_ba import BidAbsorption
from polymaker.rdp_sm_ca_matched_tp import fixed_tp_price
from polymaker.rdp_sm_restored_tp import RestoredTP
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_repair_ba_tolerance import tolerant_setup


async def restored_setup(env, monkeypatch):
    setup, c = await tolerant_setup(env, monkeypatch)
    e = setup[1]
    setup[2].ba = BidAbsorption(setup[2], e.cfg.rdp_sm.ba)
    e.cfg.rdp_sm.normal_entry_mode = "ca_v1"
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    e.cfg.rdp_sm.ca_matched_tp.restore_after_ba = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D(".05")
    return setup, c


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110 -- the mock venue has no event publisher.
            await asyncio.sleep(0.005)


def filled(v, oid, tid, qty=5):
    v.fill(oid, tid, qty)
    v.rows[oid]["status"] = "MATCHED" if D(str(qty)) == D(v.rows[oid]["original_size"]) else "LIVE"
    reflect_positions(v)


@pytest.mark.parametrize(
    "entry,bid,ask,expected,source",
    [
        (".30", ".68", ".70", ".65", "PROFIT_TARGET"),
        (".30", ".60", ".62", ".60", "BEST_BID"),
        (".30", ".64", ".65", ".64", "BEST_BID"),
        (".345", ".68", ".70", ".60", "PROFIT_TARGET"),
        (".95", ".01", ".02", None, "TP_MAKER_PRICE_UNAVAILABLE"),
        (".98", ".03", ".04", None, "TP_MAKER_PRICE_UNAVAILABLE"),
    ],
)
def test_fixed_tp_falls_back_only_for_crossing_and_keeps_profit(entry, bid, ask, expected, source):
    book = SimpleNamespace(tick=D(".01"), best_bid=D(bid), best_ask=D(ask))
    price, origin = fixed_tp_price(D(entry), D(".05"), book)
    assert price == (D(expected) if expected else None)
    assert origin == source
    if price is not None:
        assert D(entry) + price <= D(".95") and price < book.best_ask


def test_restoration_switch_requires_tp_and_is_enabled_for_current_strategy():
    with pytest.raises(ValidationError):
        RDPCAMatchedTPConfig(restore_after_ba=True)
    assert not RDPCAMatchedTPConfig().restore_after_ba
    cfg = Config.load("config", load_env=False).rdp_sm
    assert cfg.ca_matched_tp.enabled and cfg.ca_matched_tp.restore_after_ba
    assert cfg.ca_matched_tp.profit_per_share == D(".05")
    assert cfg.repair.cutoff_sec == 240


@pytest.mark.parametrize("reverse", [False, True])
def test_remaining_fifo_cost_excludes_already_paired_lots_and_supports_reverse(reverse):
    yes, no = ("2", "1") if reverse else ("1", "2")
    fills = [
        TradeEvent(yes, Side.BUY, 0.8, 5, "first", TradeState.CONFIRMED, 1, "a"),
        TradeEvent(yes, Side.BUY, 0.3, 5, "remaining", TradeState.CONFIRMED, 2, "b"),
        TradeEvent(no, Side.BUY, 0.1, 5, "paired", TradeState.CONFIRMED, 3, "c"),
    ]
    actor = RestoredTP.__new__(RestoredTP)
    actor.e = SimpleNamespace(
        economic_fills=lambda: fills,
        fill_observed_at={},
        economic_inventory=lambda: (D(5), D(10)) if reverse else (D(10), D(5)),
        meta=SimpleNamespace(yes=SimpleNamespace(token_id="1"), no=SimpleNamespace(token_id="2")),
    )
    # ts is deliberately not used by the FIFO helper; retain its actual observation order.
    actor.e.fill_observed_at = {(f.trade_id, f.maker_order_id): {"elapsed": f.ts} for f in fills}
    assert actor.residual() == ("YES" if reverse else "NO", D(5), D(".3"))


async def test_initial_ca_tp_also_uses_best_bid_fallback(env, monkeypatch):
    from test_rdp_sm_ba import trade
    from test_rdp_sm_ca import buy
    from test_rdp_sm_ca_matched_tp import fill, tp_setup

    setup = await tp_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D(".05")
    native = e.gateway.send_prepared

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        fill(v, oid, "initial" if len(v.post_requests) == 1 else "tp")
        v.rows[oid]["status"] = "MATCHED"
        reflect_positions(v)
        if len(v.post_requests) == 1:
            # Opening cost .50 -> TP .45; the opposite ask has fallen to .22.
            from test_rdp_sm_repair_ba_tolerance import book

            feed.apply(book(feed, ".20", ".22"))
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        feed.apply([trade(feed), buy(feed)])
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), "initial-fallback"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2 and e.attempts[-1].quote.price == 0.20
        assert e.economic_inventory() == (D(5), D(5))
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_ba_expires_then_restored_tp_pairs_serially(env, monkeypatch, tmp_path, cached):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    companion = (await attach(setup, tmp_path))[0] if cached else None
    try:
        absorption(feed, "2")
        ba, sizing = await c.repair(*e.economic_inventory(), "ba-first")
        assert ba.close_barrier == "SAFE_TO_CONTINUE", ba.block_reason
        assert sizing["policy"] == "opposite_ba_v1"
        old = e.attempts[-1].order_id
        assert v.rows[old]["status"] == "CANCELED"
        native = e.gateway.send_prepared

        async def send(signed, **kw):
            assert old in e.proofs and old in e.terminal
            assert not any(row["status"] == "LIVE" for row in v.rows.values())
            raw = await native(signed, **kw)
            filled(v, signed.order_ids[0], "restored-fill")
            return raw

        monkeypatch.setattr(e.gateway, "send_prepared", send)
        result, sizing = await asyncio.wait_for(c.repair(*e.economic_inventory(), "restore"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["execution_role"] == "RESTORED_TP"
        assert sizing["price_source"] == "PROFIT_TARGET"
        assert result.requested_quotes[0].price == 0.65 and result.requested_quotes[0].size == 5
        assert result.requested_quotes[0].window_id.endswith(":restored-tp")
        assert e.economic_inventory() == (D(5), D(5)) and e.repair_episode is None
        assert len(v.post_requests) == 3
        assert all(
            row["postOnly"] and row["orderType"] == "GTC"
            for body in v.post_requests
            for row in body
        )
        logs = journal_records(e.journal.path)
        assert any(r["kind"] == "sm_restored_tp_signal" for r in logs)
        assert any(r["kind"] == "sm_restored_tp_closed" for r in logs)
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


async def test_tp_ba_tp_cycle_posts_ba_before_cancel_and_does_not_reuse_signal(env, monkeypatch):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    # Original signal lease stays fixed even when the TP/BA close proof is slow.
    feed.opposite_ba.cfg.signal_lifetime_sec = 1
    feed.opposite_ba.cfg.max_entry_age_ms = 750
    try:
        first = asyncio.create_task(c.repair(*e.economic_inventory(), "restore-one"))
        await until(lambda: len(v.post_requests) == 2)
        tp_id = e.attempts[-1].order_id
        absorption(feed, "2")
        result, sizing = await asyncio.wait_for(first, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["reason"] == "OPPOSITE_BA_HANDOFF"
        assert v.rows[tp_id]["status"] == "CANCELED" and tp_id in e.terminal
        assert len(v.post_requests) == 3  # BA already posted inside this TP window.
        assert e.attempts[-1].quote.window_id.endswith(":ba-handoff:1")
        native = e.gateway.send_prepared

        async def send(signed, **kw):
            assert not any(row["status"] == "LIVE" for row in v.rows.values())
            return await native(signed, **kw)

        monkeypatch.setattr(e.gateway, "send_prepared", send)
        second = asyncio.create_task(c.repair(*e.economic_inventory(), "restore-two"))
        await until(lambda: len(v.post_requests) == 4)
        oid = e.attempts[-1].order_id
        filled(v, oid, "second-tp-fill")
        tp, sizing = await asyncio.wait_for(second, 5)
        assert tp.close_barrier == "SAFE_TO_CONTINUE", tp.block_reason
        assert sizing["execution_role"] == "RESTORED_TP"
        assert len(v.post_requests) == 4 and e.economic_inventory() == (D(5), D(5))
    finally:
        await cleanup(setup)


async def test_failed_ba_admission_restores_tp_and_same_signal_cannot_evict_it(env, monkeypatch):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed, lifecycle = setup[:4]
    try:
        absorption(feed, "2")
        native = lifecycle.prepare

        async def unavailable(*args, **kw):
            return None

        monkeypatch.setattr(lifecycle, "prepare", unavailable)
        task = asyncio.create_task(c.repair(*e.economic_inventory(), "ba-not-posted"))
        await until(lambda: len(v.post_requests) == 2)
        await asyncio.sleep(0.08)
        assert not task.done()  # The old unconsumed confirmation did not cancel the TP.
        filled(v, e.attempts[-1].order_id, "after-admission-failure")
        result, sizing = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["execution_role"] == "RESTORED_TP" and len(v.post_requests) == 2
        monkeypatch.setattr(lifecycle, "prepare", native)
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("partial", [1, 3.448276])
async def test_partial_ba_never_upsizes_restored_tp_to_five(env, monkeypatch, partial):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    native = e.gateway.send_prepared

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        filled(v, signed.order_ids[0], "partial-ba", partial)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        absorption(feed, "2")
        result, _ = await c.repair(*e.economic_inventory(), "partial")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert await c.repair(*e.economic_inventory(), "no-upsize") is None
        assert len(v.post_requests) == 2
        assert e.economic_inventory() == (D(5), D(str(partial)))
    finally:
        await cleanup(setup)


async def test_late_tp_fill_during_cancel_completes_pair_before_next_ba(env, monkeypatch):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    try:
        task = asyncio.create_task(c.repair(*e.economic_inventory(), "late-fill"))
        await until(lambda: len(v.post_requests) == 2)
        oid = e.attempts[-1].order_id

        def on_cancel(venue):
            if not venue.rows[oid]["associate_trades"]:
                filled(venue, oid, "cancel-race")

        v.on_cancel = on_cancel
        absorption(feed, "2")
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(5), D(5))
        assert await c.repair(*e.economic_inventory(), "no-duplicate") is None
        assert len(v.post_requests) == 3  # BA was live before the late TP fill; then canceled.
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["unknown", "proof", "cash", "cutoff"])
async def test_no_restoration_before_proof_or_without_budget_or_after_cutoff(
    env, monkeypatch, fault
):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, _, lifecycle = setup[:4]
    try:
        if fault == "unknown":
            e.attempts[0].outcome = "UNKNOWN"
        elif fault == "proof":
            e.proofs.clear()
        elif fault == "cash":
            lifecycle.account["basic_spendable_budget_usd"] = 0
        else:
            e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
            e.time_anchor = e.monotonic()
        assert await c.repair(*e.economic_inventory(), "blocked-restore") is None
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_best_bid_fallback_stays_fixed_and_t240_cancels_it(env, monkeypatch):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed, _, _, _, _, pump = setup[:8]
    try:
        pump.update(no_bid=".20", no_ask=".22")
        await asyncio.sleep(0.025)
        task = asyncio.create_task(c.repair(*e.economic_inventory(), "fixed-fallback"))
        await until(lambda: len(v.post_requests) == 2)
        a = e.attempts[-1]
        assert a.quote.price == 0.20
        pump.update(no_bid=".25", no_ask=".27")
        await asyncio.sleep(0.06)
        assert len(v.post_requests) == 2 and a.quote.price == 0.20
        e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
        e.time_anchor = e.monotonic()
        result, sizing = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["price_source"] == "BEST_BID"
        assert sizing["reason"] == "MARKET_SUBMISSION_CUTOFF"
        assert v.rows[a.order_id]["status"] == "CANCELED"
    finally:
        await cleanup(setup)


async def test_restore_before_native_post_rechecks_cutoff_and_new_ba(env, monkeypatch):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    native = e.gateway.prepare

    async def prepare(*args, **kw):
        signed = await native(*args, **kw)
        absorption(feed, "2")
        return signed

    monkeypatch.setattr(e.gateway, "prepare", prepare)
    try:
        result, _ = await asyncio.wait_for(
            c.repair(*e.economic_inventory(), "ba-during-signing"), 5
        )
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        assert all(not a.post_started for a in e.attempts[1:])
    finally:
        await cleanup(setup)


@pytest.mark.parametrize(
    "fault", ["cutoff", "disconnect", "generation", "tick", "inventory", "cash"]
)
async def test_restored_tp_native_guard_rechecks_after_signing(env, monkeypatch, fault):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed, lifecycle = setup[:4]
    native = e.gateway.prepare

    async def prepare(*args, **kw):
        signed = await native(*args, **kw)
        if fault == "cutoff":
            e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
            e.time_anchor = e.monotonic()
        elif fault == "disconnect":
            setup[-2]["running"] = False
            await setup[-1]
            feed.invalidate("WS_DISCONNECTED")
        elif fault == "generation":
            feed.connect()
        elif fault == "tick":
            old = feed.books["2"].tick
            feed.apply(
                {
                    "event_type": "tick_size_change",
                    "market": e.meta.condition_id,
                    "asset_id": "2",
                    "old_tick_size": str(old),
                    "new_tick_size": ".01" if old == D(".001") else ".001",
                }
            )
        elif fault == "inventory":
            e.inventory_mismatch = "unexplained"
        else:
            lifecycle.account["basic_spendable_budget_usd"] = 0
            # Cash evidence cannot be silently replaced after the order was reserved.
            e.invalidate("ACCOUNT_EVIDENCE_CHANGED")
        return signed

    monkeypatch.setattr(e.gateway, "prepare", prepare)
    try:
        result, _ = await asyncio.wait_for(c.repair(*e.economic_inventory(), "guard-" + fault), 5)
        assert len(v.post_requests) == 1
        assert all(not a.post_started for a in e.attempts[1:])
        if fault != "inventory":
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["disconnect", "generation", "tick"])
async def test_active_restored_tp_cancels_on_hard_market_data_failure(env, monkeypatch, fault):
    setup, c = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    task = None
    try:
        task = asyncio.create_task(c.repair(*e.economic_inventory(), "active-" + fault))
        await until(lambda: len(v.post_requests) == 2)
        oid = e.attempts[-1].order_id
        setup[-2]["running"] = False
        await setup[-1]
        if fault == "disconnect":
            feed.invalidate("WS_DISCONNECTED")
        elif fault == "generation":
            feed.connect()
        else:
            old = feed.books["2"].tick
            feed.apply(
                {
                    "event_type": "tick_size_change",
                    "market": e.meta.condition_id,
                    "asset_id": "2",
                    "old_tick_size": str(old),
                    "new_tick_size": ".01" if old == D(".001") else ".001",
                }
            )
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert v.rows[oid]["status"] == "CANCELED" and len(v.post_requests) == 2
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(setup)


def test_restored_tp_console_distinguishes_restore_from_ba():
    from polymaker.rdp_sm_logging import HumanRunLog

    display = HumanRunLog(repair_mode=True)
    try:
        text = "\n".join(
            display.render(
                "events",
                [
                    (
                        "sm_restored_tp_signal",
                        {"side": "NO", "quantity": "5", "price": ".2", "price_source": "BEST_BID"},
                    ),
                    ("sm_restored_tp_ba_handoff", {"episode_id": "new-ba"}),
                    ("sm_restored_tp_closed", {"reason": "OPPOSITE_BA_HANDOFF"}),
                ],
            )
        )
        assert "Default TP Restored" in text and "BEST_BID" in text and "Restored TP -> BA" in text
    finally:
        display.close()


@pytest.mark.parametrize("cached", [False, True])
async def test_ca_main_loop_restores_tp_after_ba_then_finishes_market(
    env, monkeypatch, tmp_path, cached
):
    from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
    from test_rdp_sm_ba import trade
    from test_rdp_sm_ca import buy
    from test_rdp_sm_ca_matched_tp import fill, tp_setup

    setup = await tp_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    e.cfg.rdp_sm.ca_matched_tp.restore_after_ba = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D(".05")
    feed.opposite_ba.cfg.signal_lifetime_sec = 1
    feed.opposite_ba.cfg.max_entry_age_ms = 750
    companion = (await attach(setup, tmp_path))[0] if cached else None
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id, monotonic=e.monotonic)
    loop.executor = e
    result = RunResult(e.meta.condition_id, e.session_id)
    native = e.gateway.send_prepared
    task = driver = None

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        if len(v.post_requests) == 1 or e.attempts[-1].quote.window_id.endswith(":restored-tp"):
            fill(v, signed.order_ids[0], "main-" + str(len(v.post_requests)))
            v.rows[signed.order_ids[0]]["status"] = "MATCHED"
            reflect_positions(v)
        return raw

    async def market_events():
        feed.apply([trade(feed), buy(feed)])
        await until(lambda: len(v.post_requests) == 2)  # Initial TP is live.
        absorption(feed, "2")
        await until(lambda: result.repair_windows >= 1)
        assert result.windows[-1]["sizing"]["execution_role"] == "RESTORED_TP"
        assert e.economic_inventory()[0] == e.economic_inventory()[1]
        setup[-2]["running"] = False
        await setup[-1]
        v.time = int(e.slug.rsplit("-", 1)[1]) + 300

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        task = asyncio.create_task(loop._run_obi_session(result))
        driver = asyncio.create_task(market_events())
        await asyncio.wait_for(asyncio.gather(task, driver), 8)
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
        assert result.normal_windows == 1 and result.repair_windows == 1
        assert len(v.post_requests) == 4
        assert result.residual_shares == 0
        logs = journal_records(e.journal.path)
        pnl = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_restored_tp_result")
        assert pnl["pnl_verified"] and D(pnl["pair_pnl_usdc"]) == D(".25")
        assert pnl["filled_orders"] == 1
    finally:
        for pending in (task, driver):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(p for p in (task, driver) if p is not None), return_exceptions=True)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)
