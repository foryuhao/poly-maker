"""RDP-RISK-002 v2 frozen contract and isolated production observer tests."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import subprocess
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest

from polymaker.risk_shadow import (
    BASELINE,
    Capture,
    ExecutionJournalReader,
    ObserverMarketData,
    Summary,
    Writer,
    metadata,
    observe,
)
from polymaker.risk_shadow_model import (
    NA,
    UNKNOWN,
    Market,
    Quote,
    ShadowModel,
    candidate_prices,
    compare_decision,
    hold_candidate,
    settlement,
    variance_rate,
    volatility_risk,
)

ROOT = Path(__file__).resolve().parents[1]
MARKET = Market("btc-updown-5m-300", "0x" + "a" * 64, "1", "2", 300, 0.01)


class MemoryWriter:
    def __init__(self):
        self.rows = []

    def emit(self, name, row):
        self.rows.append((name, row))
        return True


def price(model, t, p=0.6, outcome="YES", *, generation=1, event_id=None):
    return model.quote_update(
        outcome,
        Quote(
            p - 0.005,
            p + 0.005,
            model.market.start + t,
            1000 + t,
            9000 + t,
            0.01,
            generation,
            True,
            event_id or f"{outcome}:{t}",
        ),
    )


def snap(model, t):
    s, r, _ = model.sample(int(t), model.market.start + t, 1000 + t)
    return s, r


def actual(size=5):
    return {
        "actual_state": "REBALANCE",
        "actual_side": "BUY NO",
        "actual_intended_price": 0.4,
        "actual_intended_size": size,
    }


def market_with_inventory(t=30, p=0.6):
    m = ShadowModel(MARKET, "session")
    m.inventory_update(5, 0, validity="VALID", timestamp=300)
    price(m, t, p)
    price(m, t, 1 - p, "NO")
    return m


def test_baseline_01_and_protected_sources_identical():
    # The historical freeze remains an ancestor, not a prohibition on approved
    # strategy development. Preserve the isolated observer. The user's explicit
    # sub-0.1 dust policy changes Merge admission; its proofs have dedicated tests.
    subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=ROOT, check=True)
    frozen = "c9136c93cc40d6bc534df6b60610e13df8326c79"
    for name in (
        "risk_shadow.py",
        "risk_shadow_model.py",
        "risk_shadow_launch.py",
        "risk_shadow_replay.py",
    ):
        path = f"src/polymaker/{name}"
        original = subprocess.check_output(["git", "show", f"{frozen}:{path}"], cwd=ROOT)
        assert (
            hashlib.sha256(original).digest() == hashlib.sha256((ROOT / path).read_bytes()).digest()
        ), path


@pytest.mark.asyncio
async def test_baseline_02_default_off_has_no_io(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polymaker.risk_shadow.GammaClient", lambda: pytest.fail("network initialized")
    )
    assert await observe("anything", tmp_path / "absent") == {"shadow_enabled": False}
    assert not (tmp_path / "absent").exists()


def test_baseline_03_shadow_no_execution_import_or_gateway_rpc_permission():
    for name in ("risk_shadow.py", "risk_shadow_model.py"):
        tree = ast.parse((ROOT / "src/polymaker" / name).read_text())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(
            "execution" in n or "merge" in n or "rdp_sm" in n or "config" in n for n in imports
        )
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert not set(calls) & {
            "place_order",
            "cancel_order",
            "post_order",
            "post_orders",
            "submit_merge",
            "redeem",
            "sign",
            "HTTPProvider",
        }


def test_market_01_02_mid_age_and_official_clock_independent():
    m = ShadowModel(MARKET)
    price(m, 0)
    s = m.market_snapshot(302)
    assert s["yes_mid"] == pytest.approx(0.6)
    assert s["yes_spread"] == pytest.approx(0.01)
    assert s["yes_quote_age_ms"] == 2000
    assert s["yes_quote_source_timestamp"] == 9000
    assert s["p_yes"] == pytest.approx(0.6)
    assert s["no_quote_validity"] == "MISSING"
    assert s["crosscheck_status"] == "NOT_AVAILABLE"


def test_market_03_stale_boundary():
    m = ShadowModel(MARKET)
    price(m, 0)
    assert m.market_snapshot(305)["yes_quote_validity"] == "VALID"
    s = m.market_snapshot(305.001)
    assert s["yes_quote_validity"] == "STALE"
    assert s["p_yes"] == UNKNOWN


def test_market_04_asynchronous_and_low_confidence():
    m = ShadowModel(MARKET)
    price(m, 0)
    price(m, 1.001, 0.4, "NO")
    s = m.market_snapshot(301.001)
    assert s["crosscheck_status"] == "ASYNCHRONOUS"
    assert s["probability_crosscheck_error"] == UNKNOWN
    price(m, 2, 0.3, "NO")
    price(m, 2, 0.6)
    s, r = snap(m, 2)
    assert s["crosscheck_status"] == "LOW_CONFIDENCE"
    assert r["p_yes"] == UNKNOWN
    assert r["v_hat"] == UNKNOWN


def test_market_05_06_slots_and_no_future_backfill():
    m = ShadowModel(MARKET)
    price(m, 100)
    s, r, h = m.sample(30, 400, 1100)
    assert s["slot_validity"] == "MISSED"
    assert s["yes_mid"] == s["p_yes"] == r["p_yes"] == UNKNOWN
    assert h["virtual_plus5_r_settle"] == UNKNOWN
    assert m.sample(30, 401, 1101) is None


def test_vol_01_formula_separate_from_gate():
    pts = [(0, 0.600), (1, 0.602), (2, 0.601), (3, 0.604)]
    v, sigma = variance_rate(pts)
    assert v == pytest.approx(0.0000046666666667)
    assert sigma == pytest.approx(0.0021602469)
    m = ShadowModel(MARKET)
    for t, p in pts:
        price(m, t, p)
    _, r = snap(m, 3)
    assert r["vol_interval_count"] == 3
    assert r["volatility_validity"] == "INSUFFICIENT_DATA"
    assert r["v_hat"] == r["sigma_hat"] == UNKNOWN


@pytest.mark.parametrize("times", [range(10), range(11)])
def test_vol_02_03_thresholds(times):
    m = ShadowModel(MARKET)
    for t in times:
        price(m, t)
    _, r = snap(m, max(times))
    assert r["v_hat"] == UNKNOWN
    assert r["vol_valid_duration_sec"] < 20


def test_vol_04_price_gap_excluded_not_zero():
    m = ShadowModel(MARKET)
    for t in (0, 1, 10, 11):
        price(m, t, 0.6 if t < 10 else 0.8)
    _, r = snap(m, 11)
    assert r["vol_interval_count"] == 2
    assert r["vol_valid_duration_sec"] == 2
    assert r["vol_gap_count"] == 1
    assert r["realized_gap_reasons"]["PRICE_GAP"] == 1


def test_vol_05_06_old_cache_duplicate_vs_real_zero_update_and_bucket_last():
    m = ShadowModel(MARKET)
    for t in range(31):
        price(m, t, 0.6)
        price(m, t + 0.2, 0.6)
        assert not price(m, t + 0.2, 0.6)
    _, r = snap(m, 31)
    assert len(m.vol.buckets) == 31
    assert r["vol_interval_count"] == 29
    assert r["vol_valid_duration_sec"] == pytest.approx(29)
    assert r["v_hat"] == 0
    assert r["sigma_hat"] == 0
    stale = m.risk(m.market_snapshot(340), 1040)
    assert stale["v_hat"] == UNKNOWN
    assert stale["volatility_validity"] == "STALE_PRICE"


def test_vol_window_both_endpoints_and_generation():
    m = ShadowModel(MARKET)
    for t in range(0, 41, 2):
        price(m, t, 0.6 + 0.001 * t, generation=1 if t < 20 else 2)
    s, r = snap(m, 40)
    assert r["vol_interval_count"] == 14  # 15 intervals minus reconnect interval
    assert r["vol_valid_duration_sec"] == 28
    assert r["vol_gap_count"] == 1
    assert r["v_hat"] == pytest.approx(0.000002)
    assert s["yes_quote_validity"] == "VALID"


def test_vol_07_market_restart_clean_state():
    a = ShadowModel(MARKET)
    for t in range(31):
        price(a, t)
    b = ShadowModel(replace(MARKET, slug="btc-updown-5m-600", start=600))
    assert not b.vol.buckets
    assert b.vol.realized_fields()["r_vol_realized"] == UNKNOWN


def test_risk_01_02_03_fixed_proxy():
    r = volatility_risk(5, 0.0000046666666667, 100)
    assert r["r_vol_proxy"] == pytest.approx(0.0035)
    assert r["volatility_sd"] == pytest.approx(0.0591607978)
    assert volatility_risk(0, UNKNOWN, 100)["r_vol_proxy"] == 0
    assert volatility_risk(UNKNOWN, 0.1, 100)["r_vol_proxy"] == UNKNOWN
    assert volatility_risk(5, UNKNOWN, 100)["r_vol_proxy"] == UNKNOWN
    assert volatility_risk(5, 0.1, 2)["risk_horizon_sec"] == 2


def test_risk_04_transition_and_realized_start_inventory():
    m = ShadowModel(MARKET)
    m.inventory_update(5, 0, validity="VALID", timestamp=300)
    price(m, 0, 0.6)
    price(m, 1, 0.61)
    m.inventory_update(0, 5, validity="VALID", timestamp=301.5)
    price(m, 2, 0.62)
    price(m, 3, 0.63)
    _, r = snap(m, 4)
    assert r["r_vol_realized"] == pytest.approx(0.005)
    assert r["realized_interval_count"] == 2
    assert r["realized_inventory_transition_count"] == 1
    assert r["realized_coverage_status"] == "PARTIAL"
    assert r["realized_gap_reasons"] == {"INVENTORY_TRANSITION_GAP": 1}


def test_inventory_unknown_and_late_confirmation_never_rewrites_history():
    m = ShadowModel(MARKET)
    price(m, 0)
    price(m, 1, 0.61)
    _, before = snap(m, 2)
    m.inventory_update(5, 0, validity="VALID", timestamp=303)
    price(m, 3, 0.62)
    price(m, 4, 0.63)
    _, after = snap(m, 5)
    assert before["r_vol_realized"] == UNKNOWN
    assert after["realized_gap_reasons"]["INVENTORY_UNKNOWN"] == 2
    assert after["r_vol_realized"] == pytest.approx(0.0025)
    assert before["inventory_validity"] == UNKNOWN


@pytest.mark.parametrize(
    "delta,win,value,win_change,lose_change",
    [(5, 0.99, 4.95, 0.05, -4.95), (-5, 0.01, 0.05, 4.95, -0.05)],
)
def test_settle_01_to_06(delta, win, value, win_change, lose_change):
    r = settlement(delta, 0.99)
    assert r["r_settle"] == pytest.approx(0.2475)
    assert r["settlement_sd"] == pytest.approx(0.4974937186)
    assert r["inventory_win_prob"] == pytest.approx(win)
    assert r["inventory_lose_prob"] == pytest.approx(1 - win)
    assert r["inventory_market_value"] == pytest.approx(value)
    assert r["win_payoff_change"] == pytest.approx(win_change)
    assert r["lose_payoff_change"] == pytest.approx(lose_change)
    assert r["conditional_downside_loss"] == pytest.approx(value)
    assert r["expected_terminal_payoff_change"] == pytest.approx(0, abs=1e-14)
    assert r["terminal_win_value"] == 5
    assert r["terminal_lose_value"] == 0


def test_settle_05_zero_and_tie_unknown_distinct():
    r = settlement(0, UNKNOWN)
    assert r["inventory_win_prob"] == r["inventory_lose_prob"] == r["inventory_aligned"] == NA
    assert r["r_settle"] == r["conditional_downside_loss"] == 0
    assert settlement(UNKNOWN, 0.5)["inventory_win_prob"] == UNKNOWN
    assert settlement(5, 0.5)["inventory_aligned"] == NA


@pytest.mark.parametrize(
    "t,offset,action",
    [
        (59, 5, "PASSIVE_REBALANCE"),
        (60, 2, "PASSIVE_REBALANCE"),
        (149, 2, "PASSIVE_REBALANCE"),
        (150, 0, "ACTIVE_REBALANCE"),
    ],
)
def test_shadow_01_02_boundaries(t, offset, action):
    m = market_with_inventory(t, 0.595)
    s, r = snap(m, t)
    d = compare_decision(actual(), s, r)
    assert d["shadow_tick_offset"] == offset
    assert d["shadow_action"] == action
    assert d["shadow_size"] == 5
    assert d["shadow_quote"] == pytest.approx(0.4 - offset * 0.01)
    assert d["price_difference_ticks"] == pytest.approx(offset)
    assert d["price_difference_usdc"] == pytest.approx(offset * 0.01 * 5)


def test_shadow_03_invalid_not_clipped():
    m = market_with_inventory(30, 0.975)
    s, r = snap(m, 30)
    d = compare_decision(actual(100), s, r)
    assert d["shadow_quote"] == UNKNOWN
    assert d["shadow_action"] == "UNDETERMINED"
    assert d["reason_code"] == "INVALID_CANDIDATE_QUOTE"
    assert d["price_difference_ticks"] == UNKNOWN


def test_shadow_04_dynamic_tick_changes():
    m = market_with_inventory(60, 0.595)
    old = m.quotes["NO"]
    m.quotes["NO"] = replace(old, tick=0.001)
    s, r = snap(m, 60)
    d = compare_decision(actual(), s, r)
    assert d["shadow_quote"] == pytest.approx(0.398)
    assert d["price_difference_ticks"] == pytest.approx(2)
    s["no_official_tick_size"] = UNKNOWN
    d = compare_decision(actual(), s, r)
    assert d["reason_code"] == "TICK_UNKNOWN"
    assert d["shadow_quote"] == UNKNOWN


@pytest.mark.parametrize(
    "t,p,delta,expected",
    [
        (240, 0.95, 5, True),
        (239, 0.95, 5, False),
        (270, 0.949, 5, False),
        (270, 0.99, -5, False),
        (270, 0.99, 0, False),
    ],
)
def test_shadow_05_hold_all_conditions(t, p, delta, expected):
    m = market_with_inventory(t, p)
    m.inventory_update(max(delta, 0), max(-delta, 0), validity="VALID", timestamp=300)
    s, r = snap(m, t)
    assert hold_candidate(s, r) is expected
    r["inventory_validity"] = UNKNOWN
    assert hold_candidate(s, r) == UNKNOWN


def test_data_03_virtual_inventory_isolated():
    m = ShadowModel(MARKET)
    for t in range(31):
        price(m, t, 0.99)
    s, r, h = m.sample(31, 331, 1031)
    assert r["delta"] == r["q_yes"] == UNKNOWN
    assert r["r_settle"] == UNKNOWN
    assert h["inventory_source"] == "HYPOTHETICAL"
    assert h["virtual_plus5_r_settle"] == pytest.approx(0.2475)
    assert h["virtual_minus5_r_settle"] == pytest.approx(0.2475)
    assert h["virtual_plus5_win_prob"] == pytest.approx(0.99)
    assert h["virtual_minus5_win_prob"] == pytest.approx(0.01)
    assert not any(k in h for k in ("q_yes", "delta", "q_no"))
    assert s["session_id"] == NA


@pytest.mark.asyncio
async def test_existing_ws_receipts_dedup_identity_tick_and_errors():
    m, w = ShadowModel(MARKET), MemoryWriter()
    capture = Capture(m, w)
    service = ObserverMarketData(capture)
    service.set_markets([(MARKET.condition_id, ["1", "2"])])
    service.connected, service.connection_generation = True, 1
    frame = {
        "event_type": "book",
        "market": MARKET.condition_id,
        "asset_id": "1",
        "bids": [{"price": ".59", "size": "5"}],
        "asks": [{"price": ".61", "size": "5"}],
        "timestamp": "1000",
        "tick_size": ".01",
        "hash": "one",
    }
    service._receipt_us, service._receipt_ns = 300_000_000, 1_000_000_000_000
    await service._process_raw(json.dumps(frame))
    assert m.quotes["YES"].received == 300
    assert m.quotes["YES"].mono == 1000
    count = len(w.rows)
    service._receipt_us = 301_000_000
    service._receipt_ns = 1_001_000_000_000
    await service._process_raw(json.dumps(frame))
    assert len(w.rows) == count
    assert m.quotes["YES"].received == 300
    service._dispatch({"event_type": "tick_size_change", "asset_id": "1", "new_tick_size": ".001"})
    assert m.quotes["YES"].tick == 0.001
    assert m.quotes["YES"].received == 300
    service._dispatch({**frame, "market": "wrong"})
    assert w.rows[-1][1]["reason"] == "INVALID_IDENTITY"
    service._handle("bad JSON")
    assert w.rows[-1][1]["event"] == "SHADOW_ERROR"
    service._dispatch({**frame, "hash": "bad", "bids": [{"price": "nan", "size": "5"}]})
    assert m.quotes["YES"].fields(301)["quote_validity"] == "MISSING"


def test_lifecycle_01_to_04_full_300_and_five_markets_stop_independent():
    for epoch in range(300, 1800, 300):
        m = ShadowModel(replace(MARKET, start=epoch, slug=f"btc-updown-5m-{epoch}"))
        summary = Summary(m, epoch)
        for t in range(300):
            price(m, t, 0.6 + 0.005 * math.sin(t))
            price(m, t, 0.4 - 0.005 * math.sin(t), "NO")
            s, r, h = m.sample(t, epoch + t, 1000 + t)
            summary.sample(s, r)
            assert r["delta"] == UNKNOWN
            assert r["r_vol_realized"] == UNKNOWN
            if t >= 21:
                assert r["volatility_validity"] == "VALID"
            if t in (240, 270):
                assert s["quote_validity"] == "VALID"
                assert h["virtual_plus5_r_settle"] != UNKNOWN
        assert len(m.slots) == 300
        assert summary.data["observed_snapshot_slots"] == 300
        assert summary.data["t240_valid"] is True
        assert summary.data["t270_valid"] is True


def test_lifecycle_05_bounded_writer_errors_and_unknown_json(tmp_path):
    writer = Writer(tmp_path / "run", capacity=1)
    assert writer.queue.maxsize == 1
    writer.emit("risk_snapshots", {"value": UNKNOWN, "na": NA, "zero": 0})
    writer.close()
    row = json.loads((tmp_path / "run/risk_snapshots.jsonl").read_text())
    assert row["value"] == UNKNOWN and row["na"] == NA and row["zero"] == 0
    assert not writer.emit("risk_snapshots", {})
    assert writer.dropped == 1
    bad = Writer(tmp_path / "bad")
    bad.emit("absent", {})
    assert bad.close()
    assert len(bad.errors) == 1


def test_shadow_06_execution_link_no_fill_inference_no_future(tmp_path):
    m = market_with_inventory(60, 0.595)
    m.session_id = "session"
    history = deque([snap(m, 60)])
    path = tmp_path / "journal.jsonl"
    path.write_text("")
    w = MemoryWriter()
    follower = ExecutionJournalReader(path, m, w, history)
    quote = {
        "market_id": MARKET.condition_id,
        "token_id": "2",
        "outcome": "NO",
        "side": "BUY",
        "price": 0.4,
        "size": 5,
        "window_id": "session:w1",
    }

    def row(kind, payload, stamp=360.1):
        return {"ts": stamp, "kind": kind, "data": {"session_id": "session", "payload": payload}}

    follower.accept(
        row(
            "sm_strategy_window",
            {"state": "REBALANCE", "delta": "5", "window_id": "session:w1", "quotes": [quote]},
        ),
        360.2,
    )
    d = [r for name, r in w.rows if name == "decision_comparison"][-1]
    assert d["shadow_quote"] == pytest.approx(0.38)
    assert d["actual_confirmed_fill"] == UNKNOWN
    follower.accept(row("sm_placement_intent", {"quote": quote, "post_started": False}), 360.3)
    assert m.inventory.validity == UNKNOWN
    follower.accept(row("sm_placement_ack", {"quote": quote, "order_id": "order"}), 360.4)
    assert [r for name, r in w.rows if name == "decision_comparison"][-1]["record_stage"] == "ACK"
    follower.accept(
        row(
            "sm_trade",
            {
                "market_id": MARKET.condition_id,
                "maker_order_id": "order",
                "status": "CONFIRMED",
                "trade_id": "fill",
                "size": 5,
                "price": 0.4,
            },
        ),
        360.5,
    )
    fill = [r for name, r in w.rows if name == "decision_comparison"][-1]
    assert fill["record_stage"] == "CONFIRMED_FILL"
    assert fill["actual_confirmed_fill"]["size"] == 5
    follower.accept(
        row(
            "sm_run_result",
            {
                "confirmed_yes": 5,
                "confirmed_no": 5,
                "execution_status": "COMPLETED",
                "final_close_barrier": "SAFE_TO_CONTINUE",
            },
        ),
        361,
    )
    assert follower.stopped
    price(m, 270)
    assert m.market_snapshot(570)["yes_quote_validity"] == "VALID"
    with pytest.raises(ValueError, match="future"):
        follower.accept(row("sm_trade", {}, 1000), 360)
    before = dict(d)
    follower.accept(row("official_winner", {"winner": "NO"}), 361)
    assert d == before
    assert all("raw_response" not in r for _, r in w.rows)


def test_metadata_uses_actual_start_not_listing_creation():
    raw = {
        "slug": MARKET.slug,
        "conditionId": MARKET.condition_id,
        "eventStartTime": "1970-01-01T00:05:00Z",
        "startDate": "1970-01-01T00:00:00Z",
        "endDate": "1970-01-01T00:10:00Z",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["1", "2"]',
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
    }
    assert metadata(raw, MARKET.slug) == MARKET
    with pytest.raises(ValueError):
        metadata({**raw, "endDate": "1970-01-01T00:11:00Z"}, MARKET.slug)


def test_candidate_all_four_and_zero_delta_none():
    m = market_with_inventory(30, 0.595)
    s, r = snap(m, 30)
    prices = candidate_prices(s, "NO", 5, 5)
    assert [prices[f"candidate_bid_{k}tick"] for k in (0, 1, 2, 5)] == [0.4, 0.39, 0.38, 0.35]
    r.update(delta=0)
    assert compare_decision(actual(), s, r)["shadow_action"] == "NONE"


def test_vol_interval_count_gate_independent_of_duration():
    m = ShadowModel(MARKET)
    for t in range(0, 25, 4):
        price(m, t)
    _, r = snap(m, 24)
    assert r["vol_interval_count"] == 6
    assert r["vol_valid_duration_sec"] == 24
    assert r["v_hat"] == UNKNOWN


def test_real_last_update_in_bucket_selected_and_no_future_price():
    m = ShadowModel(MARKET)
    price(m, 0, 0.6)
    price(m, 0.8, 0.7)
    price(m, 1.2, 0.71)
    assert m.vol.buckets[0].mid == 0.7
    assert m.vol.buckets[0].mono == 1000.8
    future = m.risk(m.market_snapshot(301), 1001)
    assert future["p_yes"] == UNKNOWN
    assert future["vol_interval_count"] == 0


def test_low_confidence_no_inventory_risk():
    m = market_with_inventory(30, 0.6)
    price(m, 30.1, 0.2, "NO")
    s = m.market_snapshot(330.1)
    r = m.risk(s, 1030.1)
    assert r["r_settle"] == r["r_vol_proxy"] == UNKNOWN
    assert (
        compare_decision(actual(), s, {**r, "risk_snapshot_id": "x"})["shadow_action"]
        == "UNDETERMINED"
    )


def test_execution_boundary_uses_true_decision_time(tmp_path):
    m = market_with_inventory(59, 0.595)
    w = MemoryWriter()
    path = tmp_path / "journal.jsonl"
    path.write_text("")
    history = deque([snap(m, 59)])
    follower = ExecutionJournalReader(path, m, w, history)
    quote = {
        "market_id": MARKET.condition_id,
        "token_id": "2",
        "outcome": "NO",
        "side": "BUY",
        "price": 0.4,
        "size": 5,
        "window_id": "session:w1",
    }
    follower.accept(
        {
            "kind": "sm_strategy_window",
            "ts": 360,
            "data": {
                "session_id": "session",
                "payload": {
                    "state": "REBALANCE",
                    "delta": "5",
                    "window_id": "session:w1",
                    "quotes": [quote],
                },
            },
        },
        360.1,
    )
    comparison = w.rows[-1][1]
    assert comparison["shadow_tick_offset"] == 2
    assert comparison["market_input_snapshot_timestamp"] == 359


@pytest.mark.asyncio
async def test_complete_production_observer_mock_ws_and_deterministic_replay(tmp_path, monkeypatch):
    import asyncio

    from polymaker.risk_shadow_replay import replay

    clock = {"wall": 299.5, "mono": 999.5, "service": None, "event": 0}
    native_sleep = asyncio.sleep
    raw = {
        "slug": MARKET.slug,
        "conditionId": MARKET.condition_id,
        "eventStartTime": "1970-01-01T00:05:00Z",
        "endDate": "1970-01-01T00:10:00Z",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["1", "2"]',
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
    }

    class PublicGamma:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def market_by_slug(self, slug):
            assert slug == MARKET.slug
            return raw

    class MockWS(ObserverMarketData):
        def __init__(self, capture):
            super().__init__(capture)
            clock["service"] = self

        async def run(self):
            self.connected = True
            self.connection_generation = 1
            await asyncio.Event().wait()

    async def advance(seconds):
        clock["wall"] += seconds
        clock["mono"] += seconds
        await native_sleep(0)
        service = clock["service"]
        service._receipt_us = int(clock["wall"] * 1e6)
        service._receipt_ns = int(clock["mono"] * 1e9)
        clock["event"] += 1
        for token, bid, ask in [("1", ".59", ".61"), ("2", ".39", ".41")]:
            service._handle(
                json.dumps(
                    {
                        "event_type": "book",
                        "market": MARKET.condition_id,
                        "asset_id": token,
                        "bids": [{"price": bid, "size": "5"}],
                        "asks": [{"price": ask, "size": "5"}],
                        "tick_size": ".01",
                        "timestamp": str(clock["wall"] * 1000),
                        "hash": str(clock["event"]),
                    }
                )
            )
        service._receipt_us = service._receipt_ns = None

    monkeypatch.setattr("polymaker.risk_shadow.GammaClient", PublicGamma)
    monkeypatch.setattr("polymaker.risk_shadow.ObserverMarketData", MockWS)
    monkeypatch.setattr("polymaker.risk_shadow.time.time", lambda: clock["wall"])
    monkeypatch.setattr("polymaker.risk_shadow.time.monotonic", lambda: clock["mono"])
    monkeypatch.setattr("polymaker.risk_shadow.asyncio.sleep", advance)
    result = await observe(MARKET.slug, tmp_path, enabled=True)
    assert result["observed_snapshot_slots"] == 300
    assert result["observed_market_duration_sec"] == 300
    assert result["t240_valid"] is True and result["t270_valid"] is True
    assert result["shadow_dropped_event_count"] == result["shadow_error_count"] == 0
    receipt = replay(result["output_directory"])
    assert receipt["status"] == "PASS", receipt
    assert receipt["snapshot_slots_checked"] == 300


def test_crosscheck_frozen_exact_threshold():
    m = ShadowModel(MARKET)
    m.quote_update("YES", Quote(0.59, 0.61, 300, 1000, 300, 0.01, 1, True, "y"))
    m.quote_update("NO", Quote(0.41, 0.43, 300, 1000, 300, 0.01, 1, True, "n"))
    assert m.market_snapshot(300)["crosscheck_status"] == "VALID"
    assert m.market_snapshot(300)["probability_crosscheck_error"] == 0.02
    m.quote_update("NO", Quote(0.4101, 0.4301, 301, 1001, 301, 0.0001, 1, True, "n2"))
    assert m.market_snapshot(301)["crosscheck_status"] == "LOW_CONFIDENCE"
