"""Initial TP direction: MATCHED -> TP ACK -> CONFIRMED -> real BA handoff."""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_ca_matched_tp import CAMatchedTP
from polymaker.rdp_sm_opposite_ba import OppositeBAController
from polymaker.rdp_sm_sl_ledger import fifo_pairs
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_ba import trade
from test_rdp_sm_ca import buy
from test_rdp_sm_ca_matched_tp import fill, tp_setup
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_restored_tp import until


def observe(v, e, oid, tid, qty=5, status=TradeState.CONFIRMED):
    """Venue fill plus the real owned WS event sequence, one identity only."""
    row = fill(v, oid, tid, qty, status=status.value)
    v.rows[oid]["status"] = (
        "MATCHED"
        if D(str(v.rows[oid]["size_matched"])) == D(str(v.rows[oid]["original_size"]))
        else "LIVE"
    )
    reflect_positions(v)
    for state in [TradeState.MATCHED, status] if status is TradeState.CONFIRMED else [status]:
        e.processor.on_trade(
            TradeEvent(
                v.rows[oid]["asset_id"],
                Side.BUY,
                float(v.rows[oid]["price"]),
                qty,
                tid,
                state,
                v.time,
                oid,
                e.meta.condition_id,
            ),
            e.meta.condition_id,
        )
    return row


@asynccontextmanager
async def delayed_tp(env, monkeypatch, tmp_path, cached=False, token="1", preposition_ba=False):
    setup = await tp_setup(env, monkeypatch)
    v, e, feed, lifecycle, normal, wire = setup[:6]
    e.cfg.rdp_sm.ca_matched_tp.restore_after_ba = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D(".05")
    for cfg in (e.cfg.rdp_sm.repair.opposite_ba, feed.opposite_ba.cfg):
        cfg.signal_lifetime_sec = 1
        cfg.max_entry_age_ms = 750
        cfg.max_sell_age_ms = 500
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native, native_init = e.gateway.send_prepared, CAMatchedTP.__init__
    c = SimpleNamespace(
        setup=setup,
        v=v,
        e=e,
        feed=feed,
        lifecycle=lifecycle,
        wire=wire,
        token=token,
        opposite="2" if token == "1" else "1",
        owner=None,
        opening=None,
        tp=None,
        ba=None,
        opening_trade=None,
        on_ba=None,
        ambiguous=False,
        task=None,
        extra_tasks=[],
        native=native,
    )

    def capture(owner, *args, **kwargs):
        native_init(owner, *args, **kwargs)
        c.owner = owner

    async def send(signed, **kwargs):
        attempt = e.attempts[-1]
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            c.opening = oid
            if preposition_ba:
                absorption(feed, c.opposite)
            c.opening_trade = fill(v, oid, "delayed-opening", status="MATCHED")
            v.rows[oid]["status"] = "MATCHED"
            reflect_positions(v)
        elif attempt.quote.window_id.endswith(":ca-tp"):
            c.tp = oid
        elif ":ba-handoff:" in attempt.quote.window_id:
            c.ba = oid
            if oid in v.rows and c.on_ba:
                c.on_ba(oid)
            if c.ambiguous:
                return [{"success": True, "orderID": "", "status": ""}]
        return raw

    monkeypatch.setattr(CAMatchedTP, "__init__", capture)
    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        feed.apply([trade(feed, token), buy(feed, token)])
        c.task = asyncio.create_task(normal.enter(D(0), D(0), "delayed-ca"))
        await until(lambda: c.tp is not None and e.attempts[1].outcome == "ACK")
        assert e.economic_inventory() == (D(0), D(0)) and e.repair_episode is None
        assert c.opening_trade["status"] == "MATCHED" and v.rows[c.tp]["status"] == "LIVE"
        assert not any(c.tp in ids for ids in v.cancels)
        c.first_fill_mono = e.fill_observed_at[("delayed-opening", c.opening)]["fill_monotonic"]

        async def confirm(*, expect_live=True):
            c.opening_trade["status"] = "CONFIRMED"
            reflect_positions(v)
            await until(
                lambda: (
                    e.repair_episode is not None
                    and e.repair_episode.get("ca_tp_direction_owner") == "delayed-ca"
                )
            )
            assert e.repair_episode["dual_normal"]  # Full two-order close proof is still required.
            assert e.repair_episode["side"] == ("YES" if token == "1" else "NO")
            assert e.repair_episode["first_fill_monotonic"] == c.first_fill_mono
            assert e.repair_episode["normal_entry_side"] == e.repair_episode["side"]
            assert not e.safe_to_continue
            if expect_live:
                assert v.rows[c.tp]["status"] == "LIVE"

        c.confirm = confirm
        yield c
    finally:
        for task in [c.task, *c.extra_tasks]:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("token", ["1", "2"])
async def test_delayed_confirmation_proves_original_side_then_ba_ack_precedes_tp_delete(
    env, monkeypatch, tmp_path, cached, token
):
    async with delayed_tp(env, monkeypatch, tmp_path, cached, token) as c:
        await c.confirm()
        native_cancel = c.e.gateway.cancel_detailed
        events = []

        def ba_fill(oid):
            assert c.v.rows[c.tp]["status"] == "LIVE"
            assert not any(c.tp in ids for ids in c.v.cancels)
            events.append("BA_POST")
            observe(c.v, c.e, oid, "ba-pairs")

        async def cancel(ids):
            if c.tp in ids:
                assert c.e.attempts[-1].outcome == "ACK"
                events.append("TP_DELETE_AFTER_ACK")
            return await native_cancel(ids)

        c.on_ba = ba_fill
        monkeypatch.setattr(c.e.gateway, "cancel_detailed", cancel)
        absorption(c.feed, c.opposite)
        result, sizing = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert events[0] == "BA_POST" and events[1:]
        assert all(event == "TP_DELETE_AFTER_ACK" for event in events[1:])
        assert len(c.v.post_requests) == len(result.placement_results) == 3
        assert c.e.economic_inventory() == (D(5), D(5)) and c.e.repair_episode is None
        assert len(sizing["ca_matched_tp"]["ba_handoff_orders"]) == 1
        assert all(
            row["postOnly"] and row["orderType"] == "GTC"
            for body in c.v.post_requests
            for row in body
        )
        logs = journal_records(c.e.journal.path)
        proof = next(
            r["data"]["payload"] for r in logs if r["kind"] == "sm_ca_matched_tp_direction_proven"
        )
        initial = next(
            r["data"]["payload"]
            for r in logs
            if r["kind"] == "sm_repair_episode"
            and r["data"]["payload"]["event"] == "OPENING_SUBMITTED"
        )
        assert initial["side"] is None  # This is the actual previously broken creation path.
        assert proof["first_fill_monotonic"] == c.first_fill_mono
        assert any(r["kind"] == "sm_tp_ba_handoff_accepted" for r in logs)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("token", ["1", "2"])
@pytest.mark.parametrize(
    "tp_qty,ba_qty,fill_status",
    [
        (5, 5, TradeState.CONFIRMED),
        (5, 5, TradeState.MATCHED),
        (2, 1, TradeState.CONFIRMED),
        (5, 0, TradeState.CONFIRMED),
    ],
)
async def test_delayed_confirmation_double_and_partial_fills_preserve_reverse_chain_and_cost(
    env, monkeypatch, tmp_path, cached, token, tp_qty, ba_qty, fill_status
):
    async with delayed_tp(env, monkeypatch, tmp_path, cached, token) as c:
        await c.confirm()
        original_side = "YES" if token == "1" else "NO"
        reverse_side = "NO" if token == "1" else "YES"
        tp_cost = D(str(c.v.rows[c.tp]["price"]))
        if ba_qty:
            c.on_ba = lambda oid: observe(c.v, c.e, oid, "ba-before-tp", ba_qty, status=fill_status)

        def tp_fill(venue):
            if (
                venue.rows[c.tp]["status"] == "CANCELED"
                and not venue.rows[c.tp]["associate_trades"]
            ):
                observe(venue, c.e, c.tp, "tp-cancel-race", tp_qty, status=fill_status)
                venue.rows[c.tp]["status"] = "CANCELED"

        c.v.on_cancel = tp_fill
        absorption(c.feed, c.opposite)
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(c.v.post_requests) == 3 and c.e.safe_to_continue
        assert not any(row["status"] == "LIVE" for row in c.v.rows.values())
        residual = 5 - tp_qty - ba_qty
        y, n = c.e.economic_inventory()
        assert y - n == D(residual) * (1 if token == "1" else -1)
        logs = journal_records(c.e.journal.path)
        ba_pnl = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_tp_ba_handoff_result")
        expected_pnl = sum(
            (
                qty * (1 - D(str(old.price)) - D(str(close.price)))
                for old, close, qty in fifo_pairs(
                    c.e.economic_fills(), c.e.fill_observed_at, c.e.meta.yes.token_id
                )
                if close.maker_order_id == c.ba
            ),
            D(0),
        )
        assert D(ba_pnl["pair_pnl_usdc"]) == expected_pnl
        assert ba_pnl["submitted_orders"] == 1
        assert ba_pnl["filled_orders"] == int(ba_qty > 0)
        if residual == -5:
            ep = c.e.repair_episode
            assert ep["side"] == reverse_side and ep["normal_entry_side"] == original_side
            assert ep["chain_id"] == "delayed-ca" and ep["direction_leg"] == 1
            assert ep["opening_window"] == "delayed-ca:direction:1"
            assert D(ep["original_qty"]) == 5 and D(ep["original_cost"]) == 5 * tp_cost
            assert {f.maker_order_id for f in ep["source_lots"]} == {c.tp}
            assert c.e.attempts[1].quote.window_id in ep["chain_windows"]
            assert c.e.attempts[2].quote.window_id in ep["chain_windows"]
            assert len([r for r in logs if r["kind"] == "sm_repair_reverse"]) == 1
            # Close the reverse leg through the ordinary restored TP path.
            c.v.on_cancel = None
            c.on_ba = None
            controller = OppositeBAController(c.e, c.lifecycle)
            c.lifecycle.account = (await c.e._snapshot(include_books=False))[3]
            reverse = asyncio.create_task(
                controller.repair(*c.e.economic_inventory(), "reverse-tp")
            )
            c.extra_tasks.append(reverse)
            await until(lambda: len(c.v.post_requests) == 4 and c.e.attempts[-1].outcome == "ACK")
            reverse_order = c.e.attempts[-1]
            assert reverse_order.quote.outcome == original_side and reverse_order.quote.size == 5
            assert D(str(reverse_order.quote.price)) <= 1 - tp_cost - D(".05")
            observe(c.v, c.e, reverse_order.order_id, "reverse-paired")
            closed, _ = await asyncio.wait_for(reverse, 5)
            assert closed.close_barrier == "SAFE_TO_CONTINUE", closed.block_reason
            assert c.e.economic_inventory() == (D(10), D(10)) and c.e.repair_episode is None
        else:
            assert not any(r["kind"] == "sm_repair_reverse" for r in logs)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("failure", ["reject", "signing", "unknown"])
async def test_delayed_confirmation_ba_failure_preserves_the_same_tp(
    env, monkeypatch, tmp_path, cached, failure
):
    async with delayed_tp(env, monkeypatch, tmp_path, cached) as c:
        await c.confirm()
        if failure == "reject":
            c.wire.reject_index = 0
        elif failure == "unknown":
            c.ambiguous = True
        else:

            async def signer_failure(*args, **kwargs):
                raise ValueError("test signer failed")

            monkeypatch.setattr(c.e.gateway, "prepare", signer_failure)
        absorption(c.feed, c.opposite)
        await until(
            lambda: (
                len(c.e.attempts) == 3
                and c.e.attempts[-1].outcome in {"REJECTED", "ABORTED", "VERIFIED"}
            )
        )
        await asyncio.sleep(0.05)
        assert c.v.rows[c.tp]["status"] == "LIVE" and not c.task.done()
        assert not any(c.tp in ids for ids in c.v.cancels)
        observe(c.v, c.e, c.tp, "tp-after-failed-ba")
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert c.e.economic_inventory() == (D(5), D(5))
        assert len(c.v.post_requests) == (2 if failure == "signing" else 3)


async def test_delayed_confirmation_rejects_ba_that_predates_first_matched(
    env, monkeypatch, tmp_path
):
    async with delayed_tp(env, monkeypatch, tmp_path, preposition_ba=True) as c:
        await c.confirm()
        assert c.owner.ba_controller.select(c.feed.snapshot())[0] is None
        assert c.owner.ba_controller.signal_block_reason == "OPPOSITE_BA_PREDATES_POSITION"
        await asyncio.sleep(0.05)
        assert len(c.v.post_requests) == 2 and c.v.rows[c.tp]["status"] == "LIVE"
        observe(c.v, c.e, c.tp, "tp-kept")
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE"


async def test_matched_only_cannot_publish_direction_or_admit_ba(env, monkeypatch, tmp_path):
    async with delayed_tp(env, monkeypatch, tmp_path) as c:
        absorption(c.feed, c.opposite)
        await asyncio.sleep(0.05)
        assert not c.owner.prove_ba_direction() and len(c.v.post_requests) == 2
        assert c.e.repair_episode is None and not any(c.tp in ids for ids in c.v.cancels)
        await c.confirm()
        observe(c.v, c.e, c.tp, "tp-pairs-without-ba")
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE"


@pytest.mark.parametrize("fault", ["tp_filled_during_signing", "ba_stale_during_signing"])
@pytest.mark.parametrize("cached", [False, True])
async def test_delayed_confirmation_handoff_revalidates_position_and_age_at_native_post(
    env, monkeypatch, tmp_path, cached, fault
):
    async with delayed_tp(env, monkeypatch, tmp_path, cached) as c:
        await c.confirm()
        prepare = c.e.gateway.prepare

        async def change_after_signing(*args, **kwargs):
            signed = await prepare(*args, **kwargs)
            if fault == "tp_filled_during_signing":
                observe(c.v, c.e, c.tp, "tp-before-ba-send")
            else:
                # Keep the original first-SELL t0; a new direction proof must
                # never turn an aged BA back into a fresh opportunity.
                c.owner.ba_controller.ba_config.max_entry_age_ms = 0.001
            return signed

        monkeypatch.setattr(c.e.gateway, "prepare", change_after_signing)
        absorption(c.feed, c.opposite)
        await until(lambda: len(c.e.attempts) == 3 and c.e.attempts[-1].outcome == "ABORTED")
        assert not c.e.attempts[-1].post_started and len(c.v.post_requests) == 2
        if fault == "ba_stale_during_signing":
            assert c.v.rows[c.tp]["status"] == "LIVE"
            assert not any(c.tp in ids for ids in c.v.cancels)
            observe(c.v, c.e, c.tp, "tp-after-stale-ba")
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert c.e.economic_inventory() == (D(5), D(5))


def test_direction_proof_is_visible_in_console():
    from polymaker.rdp_sm_logging import HumanRunLog

    log = HumanRunLog(repair_mode=True)
    try:
        lines = log.render(
            "events", [("sm_ca_matched_tp_direction_proven", {"side": "YES", "actual_delta": "5"})]
        )
        assert "CA TP Direction Proven: YES" in "\n".join(lines)
    finally:
        log.close()


@pytest.mark.parametrize("cached", [False, True])
async def test_ba_between_matched_and_confirmed_keeps_original_t0_when_still_fresh(
    env, monkeypatch, tmp_path, cached
):
    async with delayed_tp(env, monkeypatch, tmp_path, cached) as c:
        absorption(c.feed, c.opposite)
        original = c.feed.opposite_ba.selected
        if original is None:
            c.feed.opposite_ba.evaluate(c.feed.snapshot(), outcome="NO")
            original = c.feed.opposite_ba.selected
        assert original is not None
        t0, deadline = original.signal_start_mono, original.order_deadline
        await c.confirm(expect_live=False)
        await until(lambda: c.ba is not None and c.e.attempts[-1].outcome == "ACK")
        observe(c.v, c.e, c.ba, "ba-after-confirmation")
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(c.v.post_requests) == 3 and c.e.economic_inventory() == (D(5), D(5))
        assert original.signal_start_mono == t0 and original.order_deadline == deadline
        logs = journal_records(c.e.journal.path)
        event = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_tp_ba_handoff_signal")
        assert event["original_signal_start_monotonic"] == t0
        assert event["original_order_deadline"] == deadline
