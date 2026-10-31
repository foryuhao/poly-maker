"""Opposite BA: no upper price allowance, fixed maker quote, unchanged lease."""

from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import RDPOppositeBAExecutionConfig, RDPRepairConfig
from polymaker.rdp_sm_repair_ba import RepairBidAbsorption
from test_rdp_sm_ba import depth, trade
from test_rdp_sm_ca import fresh_at
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption, controller_fixture, setup_exit
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions


def install(c, feed, **options):
    execution = RDPOppositeBAExecutionConfig(enabled=True, **options)
    c.executor.cfg.rdp_sm.repair.opposite_ba_execution = execution
    feed.opposite_ba = RepairBidAbsorption(feed, c.ba_config, execution)


def fixture(**options):
    c, f, clock = controller_fixture(signal_lifetime_sec=3, max_book_age_ms=200)
    install(c, f, **options)
    return c, f, clock


def book(f, bid, ask, token="2"):
    return {
        "event_type": "book",
        "market": f.meta.condition_id,
        "asset_id": token,
        "timestamp": str(f.wall() * 1000),
        "bids": [{"price": str(bid), "size": "80"}],
        "asks": [{"price": str(ask), "size": "20"}],
    }


@pytest.mark.parametrize(
    "move,allowed", [(".03", True), (".05", True), (".06", True), (".20", True), ("-.01", False)]
)
@pytest.mark.parametrize("legacy_tolerance", [None, ".01", ".05"])
def test_confirmed_opportunity_has_no_upward_cap_but_still_rejects_down_break(move, allowed, legacy_tolerance):
    c, f, clock = fixture(price_tolerance=legacy_tolerance)
    clock.now = 0.01
    absorption(f, "2")
    source = f.opposite_ba.states["2"]
    original = source.anchor_bid_price
    clock.now = 0.02
    bid = original + D(move)
    f.apply(book(f, bid, bid + f.books["2"].tick))
    assert source.invalid_reason == "BA_ANCHOR_INVALID"
    assert (c.select(f.snapshot())[0] == "NO") is allowed
    if allowed:
        ep = c.episodes.selected
        assert ep.episode_id == source.episode_id
        assert ep.anchor_bid_price == original
        assert ep.order_deadline == 3.01 and ep.signal_start_mono == 0.01
        q = c.candidates(f.snapshot(), D(5), D(0), "bounded")["NO"][0][0]
        assert D(str(q.price)) == bid
    else:
        ep = next(iter(c.episodes.opportunities.values()))
        assert ep.invalid_reason == "REPAIR_BA_BID_BELOW_ANCHOR"


def test_formation_before_confirmation_remains_strict():
    c, f, clock = fixture()
    clock.now = 0.01
    f.apply(trade(f, "2"))
    f.apply(book(f, ".50", ".52"))
    f.apply([depth(f, 75, "2"), depth(f, 80, "2")])
    assert not c.episodes.opportunities
    assert c.select(f.snapshot())[0] is None


def test_quote_is_latest_ask_minus_tick_without_an_anchor_price_cap():
    c, f, clock = fixture()
    clock.now = 0.01
    absorption(f, "2")
    q, sizing = c.candidates(f.snapshot(), D(5), D(0), "inside")["NO"]
    assert D(str(q[0].price)) == f.books["2"].best_ask - f.books["2"].tick
    assert D(str(q[0].price)) > c.episodes.selected.anchor_bid_price
    assert sizing["quote_fixed"] and sizing["price_tolerance"] is None
    assert sizing["max_price"] is None
    clock.now = 0.02
    f.apply(book(f, ".52", ".56"))  # Ask-minus-tick exceeds the removed .54 anchor cap.
    quote = c.candidates(f.snapshot(), D(5), D(0), "above-old-cap")["NO"][0][0]
    assert D(str(quote.price)) == D(".56") - f.books["2"].tick
    assert D(str(quote.price)) > D(".54")
    assert c.quote_block_reason is None
    clock.now = 0.03
    f.apply(book(f, ".52", ".54"))
    quote = c.candidates(f.snapshot(), D(5), D(0), "narrower")["NO"][0][0]
    assert D(str(quote.price)) == D(".54") - f.books["2"].tick
    assert c.episodes.selected.anchor_bid_price == D(".49")


@pytest.mark.parametrize(
    "bad",
    ["down", "crossed", "stale", "reconnect", "duplicate", "out_of_order", "tick"],
)
def test_invalid_data_or_price_segment_cannot_revive_by_returning_to_original(bad):
    c, f, clock = fixture()
    clock.now = 0.01
    absorption(f, "2")
    eid = f.opposite_ba.states["2"].episode_id
    clock.now = 0.02
    if bad == "down":
        f.apply([book(f, ".48", ".51"), book(f, ".49", ".51")])
    elif bad == "crossed":
        f.apply([book(f, ".51", ".51"), book(f, ".49", ".51")])
    elif bad == "stale":
        clock.now = 0.3
        f.apply(book(f, ".49", ".51"))
    elif bad == "reconnect":
        f.connect()
        f.apply(book(f, ".49", ".51"))
    elif bad == "duplicate":
        row = trade(f, "2")
        f.apply([row, row])
    elif bad == "tick":
        f.opposite_ba.invalidate("BA_TICK_CHANGED", "2")
    else:
        f.apply(trade(f, "2", timestamp=str((f.wall() - 0.05) * 1000)))
    assert not c.select(f.snapshot())[0]
    if eid in c.episodes.opportunities:
        assert c.episodes.opportunities[eid].invalid_reason


def test_execution_does_not_renew_deadline_or_allow_second_consumption():
    c, f, clock = fixture()
    clock.now = 0.01
    absorption(f, "2")
    c.select(f.snapshot())
    ep = c.episodes.selected
    signal = c.episodes.evaluate(f.snapshot(), outcome="NO")
    claim = c.episodes.claim(signal)
    c.episodes.consume(claim)
    c.episodes.release(claim)
    assert not c.episodes.evaluate(f.snapshot(), outcome="NO").direction
    # Active order outlives the original SELL's new-entry freshness, not its lease.
    fresh_at(f, clock, 2.9)
    assert c.episodes.active_reason(claim, f.snapshot()) is None
    fresh_at(f, clock, 3.01)
    assert c.episodes.active_reason(claim, f.snapshot()) == "REPAIR_BA_ORDER_DEADLINE"
    assert ep.order_deadline == 3.01


async def tolerant_setup(env, monkeypatch):
    setup, c = await setup_exit(env, monkeypatch)
    install(c, setup[2])
    return setup, c


async def test_real_post_uses_latest_ask_minus_tick_after_up_move_and_stays_fixed(env, monkeypatch):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, f, _, _, wire = setup[:6]
    try:
        absorption(f, "2")
        ep = next(iter(f.opposite_ba.opportunities.values()))
        p0 = ep.anchor_bid_price
        price = p0 + D(".09") - f.books["2"].tick
        setup[-2].update(no_bid=str(p0 + D(".07")), no_ask=str(p0 + D(".09")))
        f.apply(book(f, p0 + D(".07"), p0 + D(".09")))

        def move():
            setup[-2].update(no_bid=str(p0 + D(".12")), no_ask=str(p0 + D(".14")))
            f.apply(book(f, p0 + D(".12"), p0 + D(".14")))

        wire.delay = move
        outcome = await c.repair(*e.economic_inventory(), "fixed-inside")
        assert outcome is not None, (
            c.signal_block_reason,
            c.quote_block_reason,
            c.lifecycle.wait_reason,
            f.snapshot(),
            f.opposite_ba.diagnostics(),
        )
        result, sizing = outcome
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert D(str(result.requested_quotes[0].price)) == price
        assert result.requested_quotes[0].size == 5
        assert len(v.post_requests) == 2  # original opening plus one repair
        assert v.post_requests[-1][0]["postOnly"]
        assert result.fill_deadline_monotonic == ep.order_deadline
        deletes = [t for t in wire.timeline if t[0] == "DELETE"]
        assert deletes[-1][2] >= ep.order_deadline
        assert D(sizing["anchor_bid_price"]) == p0 and sizing["max_price"] is None
        assert D(sizing["quote_price"]) == price and sizing["quote_fixed"]
        assert await c.repair(*e.economic_inventory(), "no-repeat") is None
        assert len(v.post_requests) == 2
        logs = journal_records(e.journal.path)
        reason = next(
            r["data"]["payload"]["reason"]
            for r in logs
            if r["kind"] in {"sm_obi_close_requested", "sm_ba_close_requested"}
            and r["data"]["payload"]["window_id"] == "fixed-inside"
        )
        assert reason == "ORDER_DEADLINE"
        assert any(r["kind"] == "sm_opposite_ba_execution" for r in logs)
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["down", "crossed", "stale", "T240"])
async def test_real_active_fixed_quote_still_closes_on_boundary_fault(env, monkeypatch, fault):
    setup, c = await tolerant_setup(env, monkeypatch)
    _, e, f, _, _, wire = setup[:6]
    try:
        absorption(f, "2")
        ep = next(iter(f.opposite_ba.opportunities.values()))

        def change():
            if fault == "down":
                f.apply(book(f, ep.anchor_bid_price - D(".01"), ep.anchor_bid_price + D(".02")))
            elif fault == "crossed":
                f.apply(book(f, ep.anchor_bid_price + D(".02"), ep.anchor_bid_price + D(".02")))
            elif fault == "stale":
                setup[-2]["running"] = False
            else:
                e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
                e.time_anchor = e.monotonic()

        wire.delay = change
        result, _ = await c.repair(*e.economic_inventory(), "fixed-fault")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert any(t[0] == "DELETE" for t in wire.timeline)
        if fault != "stale":
            assert result.close_monotonic >= ep.signal_start_mono
    finally:
        await cleanup(setup)


async def test_real_fixed_quote_fill_preserves_repair_quantity_and_completion(env, monkeypatch):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, f = setup[:3]
    try:
        absorption(f, "2")

        def fill(venue):
            oid = e.attempts[-1].expected_order_id
            if not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "fixed-filled", 5)
            reflect_positions(venue)

        v.on_cancel = fill
        result, _ = await c.repair(*e.economic_inventory(), "fixed-fill")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert e.economic_inventory() == (D(5), D(5))
        assert e.repair_episode is None and e.safe_to_continue
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("value", ["-.01", "1", "NaN", "Infinity"])
def test_invalid_tolerance_rejected(value):
    with pytest.raises(ValidationError):
        RDPOppositeBAExecutionConfig(price_tolerance=value)


def test_execution_mode_config_only_applies_to_opposite_ba():
    cfg = RDPRepairConfig(policy="opposite_ba_v1", opposite_ba_execution={"enabled": True})
    assert cfg.opposite_ba_execution.price_tolerance is None
    with pytest.raises(ValidationError, match="opposite_ba_execution"):
        RDPRepairConfig(policy="hd_tp_sl_v1", opposite_ba_execution={"enabled": True})


@pytest.mark.parametrize("cached", [False, True])
async def test_actual_ca_runner_installs_tolerant_repair_without_changing_normal(
    env, monkeypatch, tmp_path, cached
):
    from test_rdp_sm_ca_entry import (
        test_real_runner_ca_controller_no_ba_confirmation_and_safe_teardown,
    )

    env[1].rdp_sm.repair.opposite_ba_execution.enabled = True
    created = []
    original = RepairBidAbsorption.__init__

    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(RepairBidAbsorption, "__init__", capture)
    await test_real_runner_ca_controller_no_ba_confirmation_and_safe_teardown(
        env, monkeypatch, tmp_path, cached
    )
    assert created and all(d.execution.price_tolerance is None for d in created)


async def test_ca_opening_then_tolerant_opposite_ba_fill(env, monkeypatch):
    from polymaker.rdp_sm_opposite_ba import OppositeBAController
    from test_rdp_sm_ca_entry import ca_setup

    setup = await ca_setup(env, monkeypatch)
    v, e, f, lifecycle, normal = setup[:5]
    e.cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    e.cfg.rdp_sm.repair.opposite_ba = e.cfg.rdp_sm.ba.model_copy(deep=True)
    opposite = OppositeBAController(e, lifecycle)
    install(opposite, f)
    try:

        def fill(venue):
            oid = e.attempts[-1].expected_order_id
            if not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "ca-tolerant-" + venue.rows[oid]["asset_id"], 5)
            reflect_positions(venue)

        v.on_cancel = fill
        await confirm_ca(f)
        opening, _ = await normal.enter(D(0), D(0), "ca-normal")
        assert opening.close_barrier == "SAFE_TO_CONTINUE"
        assert e.economic_inventory() == (D(5), D(0))
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        absorption(f, "2")
        exit_result, sizing = await opposite.repair(*e.economic_inventory(), "tolerant-repair")
        assert exit_result.close_barrier == "SAFE_TO_CONTINUE"
        assert sizing["order_policy"] == "FIXED_ASK_MINUS_TICK"
        assert e.economic_inventory() == (D(5), D(5)) and e.repair_episode is None
        assert len(v.post_requests) == 2
    finally:
        await cleanup(setup)
