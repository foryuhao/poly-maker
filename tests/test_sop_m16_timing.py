"""M16 raw-path timing: exact boundaries, explicit endpoints and causal clocks."""

from decimal import Decimal as D
import random

import pytest

from sop_signals.m16_timing import Observation, RecoveryPath


class Tape:
    def __init__(self, *, quantum=".001", q0="100", start=0):
        self.path = RecoveryPath(token="yes", price=D(".40"), generation=1,
                                 start=start, q0=q0, receiver_quantum=quantum)
        self.seq, self.points = 0, []

    def point(self, received, quantity, kind="BID_PRICE_CHANGE", **overrides):
        self.seq += 1
        data = dict(kind=kind, token="yes", price=D(".40"), generation=1,
                    received=received, available=received, frame=self.seq,
                    row=0, change=0, source=1794800000 + received, quantity=D(quantity))
        data.update(overrides)
        point = Observation(**data)
        self.points.append(point)
        self.path.observe(point)
        return point

    def normal(self):
        self.point(0, "10", "DIRECT_SELL")
        self.point(.02, "80")
        self.point(.22, "85")
        self.point(.24, "95")
        return self

    def close(self, **values):
        return self.path.finish(**dict(available=.501, watermark=.501, coverage_proven=True) | values)


def test_normal_latency_and_selected_raw_provenance():
    t = Tape().normal()
    r = t.close()
    assert r["status"] == "TIMING_PROVEN"
    assert (r["tau_low"], r["tau_high"]) == (D(".198"), D(".222"))
    assert r["t_qualified"] == r["t_final_trough"] == r["t_base"] == t.points[1].record()
    assert r["t_minus"] == t.points[2].record() and r["t_plus"] == t.points[3].record()
    assert r["hits"] == 10 and r["loss"] == 20 and r["recovery"] == D(".75")
    assert r["qualified"] and r["stable"] and r["observations"] == 4


def test_availability_and_source_clock_are_not_latency_inputs():
    a = Tape().normal().close()
    t = Tape()
    for time, qty, kind in ((0, "10", "DIRECT_SELL"), (.02, "80", "BID_BOOK"),
                            (.22, "85", "BID_PRICE_CHANGE"), (.24, "95", "BID_BOOK")):
        t.point(time, qty, kind, available=time + .02, source=5000 - time)
    b = t.close(available=.52, watermark=.501)
    assert b["status"] == a["status"]
    assert (b["tau_low"], b["tau_high"]) == (a["tau_low"], a["tau_high"])
    # The adapter separately rejects source regression; this calculator does
    # not reorder source time or use its magnitude to manufacture faster times.


@pytest.mark.parametrize("kind", ["BBO", "ASK_PRICE_CHANGE", "CACHE_READ", "DIRECT_BUY", "TIMER"])
def test_non_anchor_callbacks_cannot_narrow_a_wide_crossing_bracket(kind):
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.22, "85", kind)
    t.point(.24, "95")
    r = t.close()
    assert r["status"] == "TIMING_UNAVAILABLE"
    assert r["reason"] == "EXPLICIT_CROSSING_BRACKET_UNPROVEN_OR_OVER_50MS"
    assert r["depth_observations"] == 2


@pytest.mark.parametrize("overrides", [{"token": "no"}, {"price": D(".39")}])
def test_other_tokens_and_prices_cannot_supply_a_quantity_endpoint(overrides):
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.22, "85", **overrides)
    t.point(.24, "95")
    assert t.close()["status"] == "TIMING_UNAVAILABLE"


def test_final_unbroken_segment_replaces_an_earlier_temporary_crossing():
    t = Tape().normal()
    t.point(.29, "84")
    t.point(.31, "95")
    r = t.close()
    assert r["status"] == "TIMING_PROVEN"
    assert r["t_minus"]["received"] == .29 and r["t_plus"]["received"] == .31
    assert (r["tau_low"], r["tau_high"]) == (D(".268"), D(".292"))


def test_new_final_trough_resets_the_base_and_old_recovery_segment():
    t = Tape().normal()
    t.point(.28, "70")
    t.point(.31, "80")
    t.point(.33, "100")
    r = t.close()
    assert r["t_qualified"]["received"] == .02
    assert r["t_final_trough"]["received"] == r["t_base"]["received"] == .28
    assert r["t_plus"]["received"] == .33
    assert (r["tau_low"], r["tau_high"]) == (D(".028"), D(".052"))


def test_repeated_same_quantity_is_an_observation_not_a_new_trough_or_crossing():
    t = Tape().normal()
    for i in range(1000):
        t.point(.25 + i / 10000, "95")
    r = t.close()
    assert r["status"] == "TIMING_PROVEN" and r["depth_observations"] == 1003
    assert r["t_base"]["received"] == .02 and r["t_plus"]["received"] == .24


def test_late_material_sell_after_recovery_has_no_identifiable_response_base():
    t = Tape()
    t.point(0, ".1", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.22, "85")
    t.point(.24, "95")
    t.point(.25, "10", "DIRECT_SELL")
    r = t.close()
    assert r["qualified"] and r["stable"]
    assert r["reason"] == "RESPONSE_ALREADY_RECOVERED_AT_BASE"
    assert r["tau_low"] is r["tau_high"] is None


def test_sell_qualified_base_cannot_be_used_as_a_cached_quantity_endpoint():
    t = Tape()
    t.point(0, ".1", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.20, "1", "DIRECT_SELL")
    t.point(.24, "95")
    r = t.close()
    assert r["reason"] == "EXPLICIT_BELOW_RECOVERY_EVENT_AFTER_BASE_MISSING"
    t = Tape()
    t.point(0, ".1", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.20, "1", "DIRECT_SELL")
    t.point(.21, "80")  # A new explicit same-quantity event is sufficient.
    t.point(.24, "95")
    assert t.close()["status"] == "TIMING_PROVEN"


@pytest.mark.parametrize("low,high,passed", [(.20, .25, True), (.20, .250001, False)])
def test_exact_50ms_crossing_limit(low, high, passed):
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(low, "85")
    t.point(high, "95")
    assert (t.close()["status"] == "TIMING_PROVEN") == passed


@pytest.mark.parametrize("cross,stable", [(.40, True), (.400001, False)])
def test_exact_last_continuous_100ms_hold(cross, stable):
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.38, "85")
    t.point(cross, "95")
    r = t.close()
    assert r["stable"] == stable
    assert (r["status"] == "TIMING_PROVEN") == stable


def test_same_frame_subupdates_do_not_prove_elapsed_time():
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.24, "85", frame=10, change=0)
    t.point(.24, "95", frame=10, change=1)
    assert t.close()["reason"] == "EXPLICIT_CROSSING_BRACKET_UNPROVEN_OR_OVER_50MS"


def test_distinct_frames_with_equal_receipt_times_still_have_no_positive_bracket():
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.24, "85")
    t.point(.24, "95")
    assert t.close()["status"] == "TIMING_UNAVAILABLE"


def test_one_raw_parent_cannot_claim_different_receipt_or_availability_clocks():
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80", frame=10)
    t.point(.03, "85", frame=10, row=1)
    t.point(.05, "95", frame=11)
    assert "PARENT_FRAME_CLOCK_DISAGREEMENT" in t.close()["reason"]


def test_equal_received_tq_and_trough_keep_causal_keys_without_time_interpolation():
    t = Tape()
    t.point(0, ".1", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.02, "1", "DIRECT_SELL")
    t.point(.04, "95")
    # The only below-half depth preceded tq in input order, despite having
    # exactly its timestamp. It cannot become an after-base quantity sample.
    assert t.close()["reason"] == "EXPLICIT_BELOW_RECOVERY_EVENT_AFTER_BASE_MISSING"


def test_material_threshold_scales_with_large_pre_hit_quantity():
    t = Tape(q0="10000")
    t.point(0, ".6", "DIRECT_SELL")
    t.point(.02, "9980")
    t.point(.20, ".4", "DIRECT_SELL")
    t.point(.21, "9985")
    t.point(.24, "9995")
    r = t.close()
    assert r["status"] == "TIMING_PROVEN" and r["t_qualified"]["received"] == .20
    assert r["t_base"]["kind"] == "DIRECT_SELL" and r["hits"] == 1


@pytest.mark.parametrize("quantum", [None, "0", "-.001", ".001001"])
def test_unproven_or_insufficient_clock_resolution_cannot_default_to_zero(quantum):
    r = Tape(quantum=quantum).normal().close()
    assert r["reason"] == "RECEIVER_CLOCK_QUANTUM_UNPROVEN"
    assert r["tau_low"] is r["tau_high"] is None


def test_twice_quantum_padding_and_lower_bound_clamp():
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.021, "95")
    r = t.close()
    assert (r["tau_low"], r["tau_high"]) == (D(0), D(".003"))


@pytest.mark.parametrize("close", [dict(watermark=.5), dict(available=.551),
                                    dict(coverage_proven=False), dict(coverage_proven=None)])
def test_incomplete_or_late_close_never_produces_a_timing_interval(close):
    assert Tape().normal().close(**close)["reason"] == "LATE_CLOSE_OR_COVERAGE"


def test_exact_window_end_is_post_window_guard_evidence_not_formation():
    t = Tape().normal()
    t.point(.50, "0")
    r = t.close()
    assert r["status"] == "TIMING_PROVEN" and r["qend"] == 95
    assert r["post_window_min"] == 0 and r["post_window_missing"]
    # Publishing must reject that guard path; timing proof alone is no signal.


@pytest.mark.parametrize("bad", [dict(generation=2), dict(available=.019),
                                 dict(frame=True), dict(row=-1), dict(source=float("nan"))])
def test_invalid_provenance_or_generation_is_unknown(bad):
    t = Tape()
    t.point(0, "10", "DIRECT_SELL")
    t.point(.02, "80", **bad)
    t.point(.22, "85")
    t.point(.24, "95")
    assert t.close()["status"] == "TIMING_UNAVAILABLE"


def test_duplicate_record_and_received_regression_are_not_reordered_or_counted():
    t = Tape().normal()
    t.path.observe(t.points[-1])
    assert "DUPLICATE_OR_UNORDERABLE" in t.close()["reason"]
    t = Tape().normal()
    t.point(.23, "95")
    assert "RECEIVED_CLOCK_REGRESSION" in t.close()["reason"]


def test_original_level_disappearance_and_nonqualified_rounds_are_distinct():
    t = Tape().normal()
    t.point(.26, "0")
    t.point(.28, "100")
    assert t.close()["reason"] == "ORIGINAL_LEVEL_MISSING"
    t = Tape()
    t.point(0, ".1", "DIRECT_SELL")
    t.point(.02, "80")
    t.point(.22, "85")
    t.point(.24, "95")
    assert t.close()["status"] == "NOT_QUALIFIED"


def test_final_path_cannot_use_future_availability_or_restart_after_close():
    t = Tape().normal()
    t.point(.3, "95", available=.52)
    assert t.close()["reason"] == "LATE_CLOSE_OR_COVERAGE"
    with pytest.raises(ValueError, match="ALREADY_CLOSED"):
        t.point(.4, "95")


def test_streaming_intervals_match_independent_full_history_suffix_reconstruction():
    rng = random.Random(1616)
    for _ in range(100):
        t = Tape()
        t.point(0, "10", "DIRECT_SELL")
        depths = [(D(".02"), D(80))]
        depths.extend((D(i) / 100, D(rng.randrange(60, 101))) for i in range(4, 40, 2))
        depths.extend((D(i) / 100, D(100)) for i in (40, 42, 44, 46, 48))
        for time, qty in depths:
            t.point(float(time), str(qty))
        r = t.close()
        # Reconstruct independently from all retained test observations: final
        # trough, last below-half observation and final uninterrupted suffix.
        minimum = min(q for _, q in depths)
        base = next(time for time, q in depths if q == minimum)
        threshold = minimum + (D(100) - minimum) / 2
        last_below = max(i for i, (_, q) in enumerate(depths) if q < threshold)
        minus, plus = depths[last_below][0], depths[last_below + 1][0]
        assert r["status"] == "TIMING_PROVEN"
        assert (r["tau_low"], r["tau_high"]) == (max(D(0), minus - base - D(".002")),
                                                  plus - base + D(".002"))


def test_release_drops_retained_event_references():
    t = Tape().normal()
    t.close()
    t.path.release()
    assert all(getattr(t.path, name) is None for name in ("tq", "tm", "base", "minus", "plus", "last"))
