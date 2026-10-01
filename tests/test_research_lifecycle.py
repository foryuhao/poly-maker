"""M4A.3 causal episode and state lifecycle regressions."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from polymaker.domain import Side, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order_once
from polymaker.research.capture import CausalCapture
from polymaker.research.lifecycle import EpisodeLifecycle
from polymaker.research.observer import ResearchConfig, ResearchObserver
from polymaker.research.schema import Episode, ResearchEvent, StateSnapshot
from polymaker.state.store import StateStore
from polymaker.state.tracker import OrderEvent, TradeEvent, UserEventProcessor
from polymaker.userstream.parse import normalize_order


def rig(monkeypatch, *, end_us=400_000_000):
    current = [100_000_000, 10_000_000_000]
    monkeypatch.setattr("polymaker.research.capture.clock", lambda: tuple(current))
    observer = ResearchObserver(ResearchConfig(enabled=True, queue_capacity=4096))
    capture = CausalCapture(observer, "session")
    lifecycle = EpisodeLifecycle(capture, dataset_id="m4a", market_slug="slug",
        condition_id="condition", yes_token_id="yes", no_token_id="no",
        market_start_us=0, market_end_us=end_us, strategy_version="v0.3",
        strategy_config_id="config", venue_min_order_qty=1)

    def emit(kind, payload, *, at=None, exchange_at=None):
        if at is not None:
            current[0], current[1] = at, at * 100
        capture.emit(kind, "condition", payload, exchange_at_us=exchange_at)

    def rows():
        queue = observer.queue
        assert queue is not None
        result = list(queue._queue.queue)
        return result

    return lifecycle, capture, emit, rows, current


def fill(role="FIRST", trade="a", order="first", qty=2, price="0.6",
         accepted=True, status="MATCHED", token=None):
    return {"role": role, "trade_id": trade, "maker_order_id": order,
        "token_id": token or ("yes" if role == "FIRST" else "no"),
        "side": "BUY", "qty": qty, "price": price, "status": status,
        "accepted": accepted, "quarantined": False, "fill_truth_ready": True}


def records(rows, cls):
    return [row for row in rows() if isinstance(row, cls)]


def test_trigger_requires_first_accepted_positive_economic_fill(monkeypatch):
    lifecycle, _cap, emit, rows, _clock = rig(monkeypatch)
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "transition": "REGISTERED"})
    emit("FILL_REVISION", fill(accepted=False, status="FAILED"))
    emit("FILL_REVISION", fill(role="PAIR", trade="pair", order="pair"))
    assert lifecycle.episode_id is None
    assert not records(rows, Episode)
    emit("FILL_REVISION", fill(), at=104_000_000, exchange_at=100_000_000)
    assert len(records(rows, Episode)) == 1
    first = records(rows, StateSnapshot)[0]
    assert first.first_qty == "2" and first.pair_qty == "2"
    assert first.available_at_us == 104_000_000
    assert first.since_first_exchange_fill_s == 4.0
    assert first.knowledge_seq == 4
    assert records(rows, Episode)[0].first_fill_known_at_us == 104_000_000
    assert records(rows, Episode)[0].first_fill_exchange_us == 100_000_000
    assert records(rows, Episode)[0].capture_start_us == 100_000_000


def test_partial_fills_status_revisions_and_historical_immutability(monkeypatch):
    lifecycle, _cap, emit, rows, _clock = rig(monkeypatch)
    emit("FILL_REVISION", fill(qty=2, price="0.61"))
    first_snapshot = records(rows, StateSnapshot)[0]
    episode_id = lifecycle.episode_id
    emit("FILL_REVISION", fill(qty=2, price="0.61", status="MINED"))
    emit("FILL_REVISION", fill(qty=2, price="0.61", status="MINED"))
    emit("FILL_REVISION", fill(trade="b", qty=3, price="0.62"))
    emit("FILL_REVISION", fill(role="PAIR", trade="p1", order="pair", qty=1, price="0.35"))
    emit("FILL_REVISION", fill(role="PAIR", trade="p2", order="pair", qty=2, price="0.36"))
    emit("FILL_REVISION", fill(role="PAIR", trade="p3", order="pair", qty=2, price="0.37"))
    latest = records(rows, StateSnapshot)[-1]
    assert lifecycle.episode_id == episode_id
    assert latest.first_qty == "5" and latest.first_cost_usd == "3.08"
    assert latest.first_vwap == str(Decimal("3.08") / Decimal(5))
    assert latest.pair_qty == "5" and latest.pair_cost_usd == "1.81"
    assert latest.paired_qty == "5" and latest.unpaired_first_qty == "0"
    assert first_snapshot.first_qty == "2" and first_snapshot.first_cost_usd == "1.22"
    assert first_snapshot.knowledge_seq == 1
    assert len(records(rows, Episode)) == 6  # first, status, second FIRST, three PAIR
    assert [e.revision for e in records(rows, Episode)] == list(range(1, 7))


def test_actual_books_tick_orders_and_unknown_no_book(monkeypatch):
    _lifecycle, _cap, emit, rows, _clock = rig(monkeypatch)
    emit("BOOK", {"token_id": "yes", "bids": ((.6, 9), (.59, 8)),
        "asks": ((.62, 7),), "tick_size": .01, "snapshot_generation": 2})
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "token_id": "yes",
        "price": .6, "qty": 4, "remaining_qty": 4, "transition": "REGISTERED"})
    emit("FILL_REVISION", fill(qty=2))
    early = records(rows, StateSnapshot)[0]
    assert early.yes_book.best_bid == "0.6"
    assert early.no_book.valid is False and early.no_book.best_bid is None
    assert early.tick_size == "0.01"
    assert early.working_orders[0].order_id == "first"
    assert early.working_orders[0].remaining_qty == "2"
    emit("BOOK", {"token_id": "no", "bids": ((.37, 3),),
        "asks": ((.4, 2),), "tick_size": .01, "snapshot_generation": 2})
    emit("DELTA", {"token_id": "yes", "side": "BUY", "price": .61, "size": 5})
    emit("FILL_REVISION", fill(trade="b", qty=1, price="0.61"))
    later = records(rows, StateSnapshot)[-1]
    assert later.yes_book.best_bid == "0.61"
    assert later.no_book.best_bid == "0.37"
    assert later.yes_qty == "3" and later.no_qty == "0"
    assert early.no_book.valid is False
    assert later.knowledge_seq > early.knowledge_seq
    emit("ORDER_STATE", {"token_id": "yes", "orders": ()})
    assert records(rows, StateSnapshot)[-1].working_orders == ()
    assert len(later.working_orders) == 1


def test_material_order_and_failed_revision_are_causal(monkeypatch):
    lifecycle, _cap, emit, rows, _clock = rig(monkeypatch)
    emit("FILL_REVISION", fill(qty=3, price="0.6"))
    before = records(rows, StateSnapshot)[0]
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "token_id": "yes",
        "price": .6, "qty": 3, "remaining_qty": 1, "transition": "REGISTERED"})
    count = len(records(rows, StateSnapshot))
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "token_id": "yes",
        "price": .6, "qty": 3, "remaining_qty": 1, "transition": "REGISTERED"})
    assert len(records(rows, StateSnapshot)) == count
    emit("CANCEL_REQUEST", {"scope": "IDS", "order_ids": ("first",), "request_id": "r"})
    pending = records(rows, StateSnapshot)[-1]
    assert pending.sample_kind == "ORDER"
    assert pending.working_orders[0].cancel_requested_at_us is not None
    emit("FILL_REVISION", fill(qty=3, price="0.6", accepted=False, status="FAILED"))
    after = records(rows, StateSnapshot)[-1]
    assert after.first_qty == "0" and after.first_cost_usd == "0"
    assert before.first_qty == "3"
    assert lifecycle.episode_id == records(rows, Episode)[0].episode_id
    assert records(rows, Episode)[-1].fill_truth_status == "INVALID"


def test_finalization_and_pair_completion_revise_without_ending_observation(monkeypatch):
    lifecycle, _cap, emit, rows, _clock = rig(monkeypatch, end_us=105_000_000)
    emit("FILL_REVISION", fill(qty=3), at=101_000_000)
    emit("SAFETY", {"transition": "FIRST_FINALIZED", "first_finalized": True},
        at=101_100_000)
    emit("FILL_REVISION", fill(role="PAIR", trade="p", order="pair", qty=3, price="0.4"),
        at=101_200_000)
    emit("SAFETY", {"transition": "PAIR_TERMINAL", "pair_terminal": True},
        at=101_300_000)
    episode = records(rows, Episode)[-1]
    assert episode.first_leg_final_qty == "3"
    assert episode.actual_pair_complete_us == 101_200_000
    assert episode.capture_status == "OPEN"
    assert lifecycle.capture_status == "OPEN"
    lifecycle.tick(102_000_000, 10_100_000_000 + 1_000_000_000)
    assert records(rows, StateSnapshot)[-1].sample_kind == "PERIODIC"
    assert records(rows, StateSnapshot)[-1].paired_qty == "3"


def test_projection_failure_isolated_at_capture_boundary(monkeypatch):
    lifecycle, cap, emit, rows, _clock = rig(monkeypatch)

    def broken(*_args, **_kwargs):
        raise RuntimeError("research snapshot failure")

    monkeypatch.setattr(lifecycle, "_snapshot", broken)
    emit("FILL_REVISION", fill())
    assert cap.failures == 1
    assert len(records(rows, ResearchEvent)) == 1
    assert len(records(rows, Episode)) == 1


def test_missed_deadline_is_explicitly_censored(monkeypatch):
    lifecycle, _cap, emit, rows, _clock = rig(monkeypatch, end_us=104_000_000)
    emit("CONNECTION", {"stream": "market", "state": "UP"})
    emit("CONNECTION", {"stream": "user", "state": "UP"})
    emit("FILL_REVISION", fill(), at=101_000_000)
    lifecycle.tick(104_000_000, 10_100_000_000 + 3_000_000_000)
    assert records(rows, Episode)[-1].capture_status == "CENSORED"
    assert "SCHEDULED_SNAPSHOT_GAP" in records(rows, Episode)[-1].quality_reasons


def test_fixed_cadence_post_complete_and_horizon(monkeypatch):
    lifecycle, _cap, emit, rows, current = rig(monkeypatch, end_us=105_000_000)
    emit("CONNECTION", {"stream": "market", "state": "UP"})
    emit("CONNECTION", {"stream": "user", "state": "UP"})
    emit("FILL_REVISION", fill(), at=101_000_000)
    emit("BASELINE_DECISION", {"strategy_state": "COMPLETE", "pure_quote": None},
        at=101_200_000)
    lifecycle.tick(102_000_000, 10_100_000_000 + 1_000_000_000)
    lifecycle.tick(103_200_000, 10_100_000_000 + 2_200_000_000)
    lifecycle.tick(104_000_000, 10_100_000_000 + 3_000_000_000)
    lifecycle.tick(105_000_000, 10_100_000_000 + 4_000_000_000)
    scheduled = records(rows, StateSnapshot)
    periodic = [s for s in scheduled if s.sample_kind == "PERIODIC"]
    assert [s.scheduled_at_us for s in periodic] == [102_000_000, 103_000_000,
        104_000_000, 105_000_000]
    assert all(s.baseline_state == "COMPLETE" for s in periodic)
    assert lifecycle.capture_status == "COMPLETE"
    assert records(rows, Episode)[-1].capture_status == "COMPLETE"
    assert [e for e in records(rows, ResearchEvent) if e.kind == "ORDER_SUBMIT"] == []
    current[:] = [106_000_000, 10_000_000_000 + 5_000_000_000]
    lifecycle.tick(*current)
    assert len([s for s in records(rows, StateSnapshot) if s.sample_kind == "PERIODIC"]) == 4


def test_shutdown_censors_and_prefill_ring_bounded(monkeypatch):
    lifecycle, _cap, emit, rows, current = rig(monkeypatch)
    for n in range(3000):
        emit("TRADE", {"n": n}, at=50_000_000 + n * 10_000)
    assert len(lifecycle.prefill) <= 2001
    emit("FILL_REVISION", fill(), at=101_000_000)
    current[:] = [102_000_000, 11_000_000_000]
    asyncio.run(lifecycle.close("PROCESS_SHUTDOWN"))
    final = records(rows, Episode)[-1]
    assert final.capture_status == "CENSORED"
    assert "PROCESS_SHUTDOWN" in final.quality_reasons
    assert records(rows, StateSnapshot)[-1].sample_kind == "FINAL"


def test_ws_remaining_size_replaces_causal_order_remainder(monkeypatch):
    _lifecycle, _cap, emit, rows, _clock = rig(monkeypatch)
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "token_id": "yes",
        "price": .6, "qty": 5, "remaining_qty": 5, "transition": "REGISTERED"})
    emit("ORDER_STATE", {"order_id": "pair", "role": "PAIR", "token_id": "no",
        "price": .4, "qty": 5, "remaining_qty": 5, "transition": "REGISTERED"})
    emit("FILL_REVISION", fill(qty=2), at=101_000_000)
    before = records(rows, StateSnapshot)[-1]
    assert {o.order_id: o.remaining_qty for o in before.working_orders} == {
        "first": "3", "pair": "5"}

    emit("ORDER_STATE", {"order_id": "pair", "role": "PAIR", "transition": "UPDATE",
        "status": "LIVE", "remaining_size": 3, "size_matched": 2, "cancel": False},
        at=101_050_000)
    pair_updated = records(rows, StateSnapshot)[-1]
    assert {o.order_id: o.remaining_qty for o in pair_updated.working_orders} == {
        "first": "3", "pair": "3"}
    assert {o.order_id: o.remaining_qty for o in before.working_orders}["pair"] == "5"

    # These are the fields emitted by UserEventProcessor.on_order, not a synthetic
    # remaining_qty field. The venue's cumulative matched size includes an as-yet
    # undiscovered economic fill.
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "transition": "UPDATE",
        "status": "LIVE", "remaining_size": 1, "size_matched": 4, "cancel": False},
        at=101_100_000)
    updated = records(rows, StateSnapshot)[-1]
    assert {o.order_id: o.remaining_qty for o in updated.working_orders}["first"] == "1"
    assert updated.knowledge_seq > before.knowledge_seq
    assert before.working_orders[0].remaining_qty == "3"

    emit("FILL_REVISION", fill(trade="b", qty=2), at=101_200_000)
    latest = records(rows, StateSnapshot)[-1]
    assert {o.order_id: o.remaining_qty for o in latest.working_orders}["first"] == "1"
    emit("FILL_REVISION", fill(role="PAIR", trade="p", order="pair", qty=2),
        at=101_250_000)
    assert {o.order_id: o.remaining_qty for o in records(rows, StateSnapshot)[-1].working_orders}[
        "pair"] == "3"
    emit("CANCEL_REQUEST", {"scope": "IDS", "order_ids": ("first",), "request_id": "r"},
        at=101_300_000)
    pending = records(rows, StateSnapshot)[-1]
    first_pending = next(o for o in pending.working_orders if o.order_id == "first")
    assert first_pending.remaining_qty == "1"
    assert first_pending.cancel_requested_at_us == 101_300_000
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "transition": "UPDATE",
        "status": "CANCELED", "remaining_size": 1, "size_matched": 4, "cancel": True},
        at=101_400_000)
    assert [o.order_id for o in records(rows, StateSnapshot)[-1].working_orders] == ["pair"]
    assert before.working_orders[0].remaining_qty == "3"


def test_first_finalization_time_is_first_observed_and_immutable(monkeypatch):
    lifecycle, _cap, emit, rows, _clock = rig(monkeypatch)
    emit("FILL_REVISION", fill(), at=101_000_000)
    initial = records(rows, StateSnapshot)[-1]
    emit("SAFETY", {"transition": "FIRST_FINALIZED"}, at=101_100_000)
    first_revision = records(rows, Episode)[-1]
    finalized = records(rows, StateSnapshot)[-1]
    assert first_revision.first_leg_finalized_at_us == 101_100_000
    emit("SAFETY", {"first_finalized": True}, at=101_200_000)
    emit("SAFETY", {"first_finalized": True}, at=101_300_000)
    emit("FILL_REVISION", fill(role="PAIR", trade="p", order="pair", qty=1),
        at=101_400_000)
    assert lifecycle.first_finalized_at_us == 101_100_000
    assert records(rows, Episode)[-1].first_leg_finalized_at_us == 101_100_000
    assert first_revision.first_leg_finalized_at_us == 101_100_000
    assert initial.first_finalized is False
    assert finalized.first_finalized is True


def test_terminal_baseline_emits_immediate_material_snapshot_and_keeps_cadence(monkeypatch):
    lifecycle, _cap, emit, rows, _clock = rig(monkeypatch, end_us=105_000_000)
    emit("FILL_REVISION", fill(), at=101_000_000)
    before = records(rows, StateSnapshot)[-1]
    emit("BASELINE_DECISION", {"strategy_state": "COMPLETE", "pure_quote": None},
        at=101_400_000)
    terminal = records(rows, StateSnapshot)[-1]
    assert terminal is not before
    assert terminal.sample_kind == "SAFETY"
    assert terminal.baseline_state == "COMPLETE"
    assert terminal.available_at_us == 101_400_000
    assert terminal.knowledge_seq == before.knowledge_seq + 1
    assert records(rows, Episode)[-1].revision == terminal.episode_revision
    assert before.baseline_state == "UNKNOWN"
    count = len(records(rows, StateSnapshot))
    emit("BASELINE_DECISION", {"strategy_state": "COMPLETE", "pure_quote": None},
        at=101_500_000)
    assert len(records(rows, StateSnapshot)) == count
    lifecycle.tick(102_000_000, 10_100_000_000 + 1_000_000_000)
    periodic = records(rows, StateSnapshot)[-1]
    assert periodic.sample_kind == "PERIODIC"
    assert periodic.baseline_state == "COMPLETE"
    assert periodic.scheduled_at_us == 102_000_000
    assert lifecycle.capture_status == "OPEN"
    assert not [e for e in records(rows, ResearchEvent) if e.kind == "ORDER_SUBMIT"]


def test_late_writer_failure_revises_complete_episode_without_rewriting_it(monkeypatch):
    lifecycle, _cap, emit, rows, current = rig(monkeypatch, end_us=103_000_000)
    emit("CONNECTION", {"stream": "market", "state": "UP"})
    emit("CONNECTION", {"stream": "user", "state": "UP"})
    emit("FILL_REVISION", fill(), at=101_000_000)
    lifecycle.tick(102_000_000, 10_100_000_000 + 1_000_000_000)
    lifecycle.tick(103_000_000, 10_100_000_000 + 2_000_000_000)
    completed = records(rows, Episode)[-1]
    completed_snapshot = records(rows, StateSnapshot)[-1]
    assert completed.capture_status == "COMPLETE"
    assert lifecycle.capture_status == "COMPLETE"
    causal_events = tuple(records(rows, ResearchEvent))
    accepted_fills = {key: dict(value) for key, value in lifecycle.fills.items()}
    baseline_state = lifecycle.baseline_state
    current[:] = [103_100_000, 10_100_000_000 + 2_100_000_000]
    asyncio.run(lifecycle.close("WRITER_FAILURE"))
    corrected = records(rows, Episode)[-1]
    assert corrected.revision == completed.revision + 1
    assert corrected.episode_id == completed.episode_id
    assert corrected.capture_status == "CENSORED"
    assert "WRITER_FAILURE" in corrected.quality_reasons
    assert completed.capture_status == "COMPLETE"
    assert completed_snapshot.episode_revision == completed.revision
    assert records(rows, StateSnapshot)[-1].episode_revision == corrected.revision
    assert records(rows, StateSnapshot)[-1].sample_kind == "FINAL"
    assert tuple(records(rows, ResearchEvent)) == causal_events
    assert lifecycle.fills == accepted_fills
    assert lifecycle.baseline_state == baseline_state
    count = len(records(rows, Episode))
    asyncio.run(lifecycle.close("WRITER_FAILURE"))
    assert len(records(rows, Episode)) == count


def test_parser_tracker_cancellation_removes_research_working_order(monkeypatch):
    _lifecycle, capture, _emit, rows, current = rig(monkeypatch)
    store = StateStore(":memory:")
    try:
        tracker = UserEventProcessor(store, research=capture)
        tracker.register_order("first", "condition", "yes", Side.BUY, 5, .6,
            source="favorite_first_leg")
        tracker.on_trade(TradeEvent("yes", Side.BUY, .6, 2, "trade-a",
            TradeState.MATCHED, 100.0, "first"), "condition")
        before = records(rows, StateSnapshot)[-1]
        assert [o.order_id for o in before.working_orders] == ["first"]

        cancel = normalize_order({"id": "first", "asset_id": "yes", "side": "BUY",
            "price": ".6", "original_size": "5", "size_matched": "2",
            "status": "LIVE", "type": "CANCELLATION"})
        assert cancel is not None and cancel.is_cancel and cancel.status == "LIVE"
        current[:] = [101_100_000, 10_110_000_000]
        tracker.on_order(cancel, "condition", received_at_us=101_050_000)
        after = records(rows, StateSnapshot)[-1]
        assert store.orders_for("yes") == []
        assert after.working_orders == ()
        assert after.order_state_resolved is True
        assert after.knowledge_seq == records(rows, ResearchEvent)[-1].event_seq
        assert before.working_orders[0].order_id == "first"
        assert before.working_orders[0].remaining_qty == "3"
    finally:
        store.close()


def test_tracker_quarantine_and_authoritative_reconcile_project_resolution(monkeypatch):
    _lifecycle, capture, _emit, rows, current = rig(monkeypatch)
    store = StateStore(":memory:")
    try:
        tracker = UserEventProcessor(store, research=capture)
        tracker.register_order("first", "condition", "yes", Side.BUY, 5, .6,
            source="favorite_first_leg")
        tracker.on_trade(TradeEvent("yes", Side.BUY, .6, 2, "trade-a",
            TradeState.MATCHED, 100.0, "first"), "condition")
        before = records(rows, StateSnapshot)[-1]
        assert before.order_state_resolved is True

        current[:] = [101_100_000, 10_110_000_000]
        tracker.on_order(OrderEvent("first", "yes", Side.BUY, .6, 2,
            original_size=5, size_matched=3, status="LIVE"), "condition",
            received_at_us=101_050_000)
        quarantined = records(rows, StateSnapshot)[-1]
        assert "first" in tracker.quarantined_orders
        assert quarantined.order_state_resolved is False
        assert "UNRESOLVED_ORDER" in quarantined.quality_reasons
        assert quarantined.knowledge_seq == records(rows, ResearchEvent)[-1].event_seq
        assert before.order_state_resolved is True

        # The same authoritative order may be absent from the live working store
        # while its economic/order evidence remains unresolved.
        cancel = normalize_order({"id": "first", "asset_id": "yes", "side": "BUY",
            "price": ".6", "original_size": "5", "size_matched": "3",
            "status": "LIVE", "type": "CANCELLATION"})
        assert cancel is not None
        current[:] = [101_200_000, 10_120_000_000]
        tracker.on_order(cancel, "condition", received_at_us=101_150_000)
        canceled_unresolved = records(rows, StateSnapshot)[-1]
        assert store.orders_for("yes") == []
        assert canceled_unresolved.working_orders == ()
        assert canceled_unresolved.order_state_resolved is False

        class ResolvedGateway:
            async def get_order(self, order_id):
                return {"id": order_id, "asset_id": "yes", "side": "BUY",
                    "price": ".6", "original_size": "5", "size_matched": "2",
                    "status": "CANCELED"}

            async def get_trades(self, *, market=None):
                return [{"id": "trade-a", "status": "MATCHED", "market": "condition",
                    "asset_id": "taker", "side": "SELL", "maker_orders": [
                        {"order_id": "first", "asset_id": "yes", "side": "BUY",
                         "matched_amount": "2", "price": ".6"}]}]

        outcome = asyncio.run(reconcile_owned_order_once("first", ResolvedGateway(), tracker))
        assert outcome is ReconciliationOutcome.COMPLETE
        assert "first" not in tracker.quarantined_orders
        current[:] = [101_300_000, 10_130_000_000]
        capture.emit("SAFETY", "condition", {"transition": "FILL_TRUTH_RECONCILE",
            "order_id": "first", "outcome": outcome.value})
        resolved = records(rows, StateSnapshot)[-1]
        assert resolved.sample_kind == "SAFETY"
        assert resolved.working_orders == ()
        assert resolved.order_state_resolved is True
        assert resolved.knowledge_seq == records(rows, ResearchEvent)[-1].event_seq
        assert quarantined.order_state_resolved is False
        assert canceled_unresolved.order_state_resolved is False
    finally:
        store.close()
