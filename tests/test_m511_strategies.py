"""Adversarial state-machine cases from SOP section 13, independent of execution."""

import pytest
from maker_signals.feed import ResearchFeed
from maker_signals.model import D
from maker_signals.strategies import (
    M4_M11_RETENTION_25MS,
    M5_DEPTH_ONLY,
    M5_M8_FREQUENCY,
    M5_OVERLAP_RECOVERY,
    M8_DEPTH_REPAIR,
    M8_SINGLE_WEAK,
    Strategies,
    strategy_version,
)

from test_m511_feed import Tape


class H:
    def __init__(self, signal_variant=None):
        self.events = []
        self.c = ResearchFeed(
            "market",
            ("up", "down"),
            {"up": ".01", "down": ".01"},
            lambda k, p: self.events.append((k, p)),
        )
        self.c.connected = True
        self.c.generation = 1
        self.s = Strategies(self.c, signal_variant=signal_variant)
        for b in self.c.books.values():
            b.bids = {D(".4"): D(100), D(".39"): D(100), D(".38"): D(100)}
            b.asks = {D(".42"): D(100), D(".43"): D(100), D(".44"): D(100)}
            b.complete = b.tick_verified = True
            b.generation = 1
            self.c.good[b.token] = True
        self.at(0)

    def at(self, now, tick=False):
        self.c.now = self.c.watermark = now
        self.c.wall = 1000 + now
        self.c.seq += 1
        self.c.subindex = -1
        for b in self.c.books.values():
            b.received = now
            b.source = 1000 + now
        for flow in self.c.flows.values():
            # Explicit ideal complete input coverage for state-machine tests.
            flow.coverage.extend(now - x / 1000 for x in range(2000, -1, -10))
        self.c.registry.expire(now)
        if tick:
            self.s.tick()

    def fact(
        self, kind, ttl=8, start=None, end=None, refs=(), values=None, anchor=".4", dispatch=False
    ):
        f = self.c.fact(
            kind,
            "up",
            self.c.now + ttl,
            self.c.now - 0.5 if start is None else start,
            self.c.now if end is None else end,
            anchor=D(anchor),
            refs=refs,
            values=values,
        )
        if dispatch:
            self.s.on_fact(f)
        return f

    def rnd(
        self, at, hit=10, q0=100, loss=10, recovery=".6", stable=True, qualified=True, start=None
    ):
        self.at(at)
        return self.fact(
            "ROUND",
            ttl=0,
            start=at - 0.5 if start is None else start,
            values={
                "hit": D(str(hit)),
                "initial": D(str(q0)),
                "loss": D(str(loss)),
                "u": D(str(hit)) / D(str(q0)),
                "d": D(str(loss)) / D(str(q0)),
                "recovery": D(recovery),
                "stable": stable,
                "qualified": qualified,
                "event_ids": ["trade-" + str(at)],
            },
        )

    def rf(self, a, b):
        return self.fact(
            "RF_BID", start=a.start, refs=[a.id, b.id], values={"rounds": (a, b)}, dispatch=True
        )

    def ap(self, side="BUY", volume=10):
        return self.fact(
            "AP_" + side,
            ttl=0.75,
            start=self.c.now - 0.75,
            values={"volume": D(volume), "start_key": (self.c.now - 0.75, self.c.seq - 1, -1)},
            dispatch=True,
        )

    def signals(self, strategy):
        self.s.publish()
        return [p for k, p in self.events if k == "SIGNAL" and p["strategy"] == strategy]


def test_m5_requires_actual_second_round_pressure_overlap_and_freezes_deadline():
    h = H()
    a = h.rnd(0.5)
    b = h.rnd(1)
    rf = h.rf(a, b)
    ap = h.fact("AP_SELL", ttl=0.75, start=-1, end=0, values={"volume": D(10)}, dispatch=True)
    assert not h.signals("M5")
    ap = h.fact("AP_SELL", ttl=0.75, start=0.5, end=1, values={"volume": D(10)}, dispatch=True)
    assert len(h.signals("M5")) == 1
    p = h.s.active("M5", "up")
    deadline = p.deadline
    h.at(1.1)
    h.fact("AP_SELL", ttl=0.75, start=0.6, end=1.1, values={"volume": D(10)}, dispatch=True)
    assert p.deadline == deadline and not p.closed
    h.at(deadline, tick=True)
    assert p.closed


@pytest.mark.parametrize(
    ("variant", "loss", "recovery", "expected"),
    [
        (None, 10, ".6", False),
        (M5_DEPTH_ONLY, 10, ".6", True),
        (M5_DEPTH_ONLY, 30, ".6", False),
        (M5_DEPTH_ONLY, 10, ".5", False),
    ],
)
def test_m5_depth_variant_relaxes_only_hit_comparability(variant, loss, recovery, expected):
    h = H(variant)
    first = h.rnd(.5, recovery=".7")
    second = h.rnd(1, hit=30, loss=loss, recovery=recovery)
    h.rf(first, second)
    h.ap("SELL")
    candidates = h.signals("M5")
    assert bool(candidates) == expected
    if expected:
        payload = candidates[0]["payload"]
        assert payload["strategy_version"] == M5_DEPTH_ONLY
        assert payload["feature_values"]["relative_hit_ratio"] == 3


@pytest.mark.parametrize(
    ("overlap", "recovery", "expected"),
    [(True, ".6", True), (False, ".6", False), (True, ".5", False)],
)
def test_m5_overlap_recovery_keeps_overlap_and_recovery_as_gates(overlap, recovery, expected):
    h = H(M5_OVERLAP_RECOVERY)
    first = h.rnd(.5, recovery=".7")
    second = h.rnd(1, hit=30, loss=30, recovery=recovery)
    h.rf(first, second)
    h.fact("AP_SELL", start=.5 if overlap else 0, end=1 if overlap else .5, dispatch=True)
    candidates = h.signals("M5")
    assert bool(candidates) == expected
    if expected:
        payload = candidates[0]["payload"]
        assert payload["strategy_version"] == M5_OVERLAP_RECOVERY
        assert payload["feature_values"]["relative_hit_ratio"] == 3
        assert payload["feature_values"]["relative_depth_ratio"] == 3


def test_m6_depth_loss_needs_covered_executions_and_100ms_hold():
    h = H()
    a = h.rnd(0.5)
    b = h.rnd(1)
    h.rf(a, b)
    old = dict(h.c.books["up"].asks)
    h.c.flows["up"].coverage.clear()
    h.c.books["up"].asks = {D(".42"): D(10), D(".43"): D(10), D(".44"): D(100)}
    h.s.depth("up", {}, old, 1)
    assert not h.signals("M6")
    h.at(1.1, tick=True)
    assert not h.signals("M6")
    h.at(1.2, tick=True)
    assert len(h.signals("M6")) == 1
    p = h.s.active("M6", "up")
    h.c.books["up"].asks = old
    h.s.check_lw("up")
    h.s.tick()
    assert p.closed


def test_m7_early_reattack_cannot_be_relabelled_as_new_campaign():
    h = H()
    h.at(1)
    root = h.ap()
    p = h.s.active("M7", "up")
    h.at(2)
    early = h.fact("AP_BUY", ttl=0.75, values={"volume": D(10)})
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (1.2, 2, -1)}, early)
    h.s.on_fact(early)
    assert p.closed and h.s.active("M7", "up") is None


def m7_ready():
    h = H()
    h.at(1)
    h.ap()
    h.at(1.5, tick=True)
    p = h.s.active("M7", "up")
    h.s.reattack_round(p, h.rnd(2.3))
    assert p.stage == "WAIT_AP2"
    return h, p


def test_m7_pending_buy_window_started_before_rf_is_not_reset():
    h, p = m7_ready()
    h.at(3.2)
    ap = h.fact("AP_BUY", ttl=0.75)
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (2.2, 1, -1)}, ap)
    assert p.closed and not h.signals("M7")


def test_m7_new_post_rf_attack_confirms_with_original_deadlines():
    h, p = m7_ready()
    h.at(3.6)
    ap = h.fact("AP_BUY", ttl=0.75)
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (2.31, 100, -1)}, ap)
    assert len(h.signals("M7")) == 1 and p.deadline == 4.35


def test_m7_short_pause_and_overlapping_refill_both_required_before_fresh_reattack():
    h = H()
    h.at(1)
    root = h.ap()
    p = h.s.active("M7", "up")
    h.s.reattack_round(p, h.rnd(1.51))
    assert p.stage == "WAIT_EX" and not h.signals("M7")
    assert p.stage == "WAIT_AP2" and len(p.rounds) == 1
    assert p.data["ex"].refs == (root.id,)
    assert p.data["ex"].end == 1.5
    h.at(2.4)
    attack = h.fact("AP_BUY", ttl=.75)
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (1.52, 100, -1)}, attack)
    candidate, = h.signals("M7")
    assert candidate["payload"]["strategy_version"] == "m7-short-pause-single-refill-v1"
    assert p.deadline == pytest.approx(3.15)


def test_m7_short_pause_requires_complete_coverage():
    h = H()
    h.at(1)
    h.ap()
    p = h.s.active("M7", "up")
    h.at(1.5)
    h.c.flows["up"].coverage.clear()
    assert not h.signals("M7") and p.closed
    assert h.c.good["up"]


def m8_weak():
    h = H()
    for at, r, stable in ((0.5, ".8", True), (1, ".8", True), (1.5, ".2", False), (2, ".2", False)):
        h.s.on_round(h.rnd(at, recovery=r, stable=stable))
    return h, h.s.active("M8", "up")


def test_m8_bad_qualified_middle_round_breaks_weak_sequence():
    h = H()
    for at, r, stable in (
        (0.5, ".8", True),
        (1, ".8", True),
        (1.5, ".2", False),
        (2, ".6", True),
        (2.5, ".2", False),
    ):
        h.s.on_round(h.rnd(at, recovery=r, stable=stable))
    assert not h.s.active("M8", "up")


def test_m8_historical_df_may_expire_before_repair_confirmation():
    h, p = m8_weak()
    assert p
    for at in (3.5, 4):
        h.s.repair_round(p, h.rnd(at, recovery=".7"))
    assert len(h.signals("M8")) == 1 and p.deadline == 12
    assert not h.c.registry.valid(p.data["df"], 4)
    h.s.repair_round(p, h.rnd(4.5, recovery=".6"))
    assert p.closed


@pytest.mark.parametrize("variant", [None, M8_SINGLE_WEAK])
def test_m8_single_weak_variant_still_requires_two_new_repairs(variant):
    h = H(variant)
    for at, r, stable in ((.5, ".8", True), (1, ".8", True), (1.5, ".2", False)):
        h.s.on_round(h.rnd(at, recovery=r, stable=stable))
    p = h.s.active("M8", "up")
    if variant is None:
        assert p is None
    else:
        assert len(p.data["df"].values["weak"]) == 1
        h.s.repair_round(p, h.rnd(2, recovery=".7"))
        assert not h.signals("M8")
        h.s.repair_round(p, h.rnd(2.5, recovery=".7"))
        candidates = h.signals("M8")
        assert len(candidates) == 1
        assert candidates[0]["payload"]["strategy_version"] == M8_SINGLE_WEAK


@pytest.mark.parametrize(
    ("weak_recovery", "repair_recovery", "repair_loss", "expected"),
    [(".30", ".60", 10, True), (".31", ".60", 10, False),
     (".30", ".59", 10, False), (".30", ".60", 30, False)],
)
def test_m8_depth_repair_requires_one_weak_and_one_comparable_stable_round(
    weak_recovery, repair_recovery, repair_loss, expected
):
    h = H(M8_DEPTH_REPAIR)
    weak = h.rnd(.5, recovery=weak_recovery, stable=False)
    h.s.on_round(weak)
    assert not h.signals("M8")
    p = h.s.active("M8", "up")
    if p:
        assert p.observer.anchor == weak.anchor
        assert p.observer.gate[0] == weak.confirmed
        h.s.repair_round(p, h.rnd(1, hit=30, loss=repair_loss, recovery=repair_recovery))
    candidates = h.signals("M8")
    assert bool(candidates) == expected
    if expected:
        assert candidates[0]["payload"]["strategy_version"] == M8_DEPTH_REPAIR


def test_combined_frequency_profile_uses_each_strategys_own_version():
    h = H(M5_M8_FREQUENCY)
    h.rf(h.rnd(.5), h.rnd(1, hit=30, loss=30))
    h.ap("SELL")
    assert h.signals("M5")[0]["payload"]["strategy_version"] == M5_OVERLAP_RECOVERY

    h.s.on_round(h.rnd(1.5, recovery=".30", stable=False))
    repair = h.s.active("M8", "up")
    h.s.repair_round(repair, h.rnd(2, hit=30, recovery=".60"))
    assert h.signals("M8")[0]["payload"]["strategy_version"] == M8_DEPTH_REPAIR
    assert strategy_version("M6", M5_M8_FREQUENCY) == strategy_version("M6")


def m9_seed(volume=10):
    h = H()
    h.s.on_round(h.rnd(1))
    h.at(1.1)
    ap = h.fact("AP_SELL", ttl=0.75, start=0.35, values={"volume": D(volume)})
    h.at(1.85)
    h.fact("EX_SELL", ttl=0.75, refs=[ap.id], values={"volume": D(volume)}, dispatch=True)
    return h, h.s.active("M9", "up")


class R:
    minimum = D(".5")
    hit = D(1)
    initial = D(100)


def test_m9_tiny_first_hit_cannot_extend_seed_past_qualification_deadline():
    h, p = m9_seed()
    h.at(p.data["seed_deadline"])
    h.s.retest_qualified(p, R())
    assert p.closed and not h.signals("M9")


def test_m9_qualified_before_seed_expiry_can_complete_later_without_renewing_seed():
    h, p = m9_seed()
    original = p.deadline
    h.at(2.5)
    h.s.retest_qualified(p, R())
    h.s.retest_round(p, h.rnd(3, hit=1, loss=1))
    assert original < 3 and len(h.signals("M9")) == 1 and p.deadline == 11


def test_m9_first_unqualified_round_cannot_be_skipped():
    h, p = m9_seed()
    h.s.retest_round(p, h.rnd(2.4, qualified=False, stable=False))
    assert p.closed
    h.s.retest_round(p, h.rnd(2.9))
    assert not h.signals("M9")


def test_m9_minimum_residual_budget_is_strict():
    h, p = m9_seed(volume=".75")
    h.at(2)
    h.s.retest_progress(p, R())
    assert p.closed


@pytest.mark.parametrize(("weak_volume", "expected"), [("6.49", True), ("6.5", False)])
def test_m9_uses_one_stable_support_and_one_new_weak_retest(weak_volume, expected):
    from maker_signals.model import Trade

    h = H()
    support = h.rnd(1.5)
    h.s.on_round(support)
    assert h.s.active("M9", "up") is None
    h.at(1.6)
    attack = h.ap("SELL")
    h.at(2.35)
    h.fact("EX_SELL", ttl=.75, refs=[attack.id], dispatch=True)
    p = h.s.active("M9", "up")
    assert p.data["seed_rf"].refs == (support.id,)
    assert not h.signals("M9")
    h.at(2.5)
    h.c.flows["up"].trades.append(
        Trade("weak", "up", "SELL", D(".4"), D(weak_volume), 2.5, 1002.5, 2.5,
              h.c.seq, 0, "up", False)
    )
    h.s.retest_qualified(p, R())
    h.s.retest_round(p, h.rnd(3, hit=weak_volume))
    signals = h.signals("M9")
    assert bool(signals) is expected
    if expected:
        assert signals[0]["payload"]["strategy_version"] == "m9-single-support-weak-retest-v1"
        assert len(p.rounds) == 1 and p.rounds[0].id != support.id


def test_m10_two_returns_and_nonrenewable_rf12_deadline():
    h = H()
    h.at(0)
    h.ap()
    p = h.s.active("M10", "up")
    for at, hit in ((0.5, 10), (1.05, 8)):
        h.s.decay_round(p, h.rnd(at, hit=hit))
    assert len(h.signals("M10")) == 1 and p.deadline == 9.05
    assert p.deadline - p.candidate.confirmed == pytest.approx(8)
    h.at(2)
    h.ap()
    assert h.s.active("M10", "up") is p and p.deadline == 9.05
    r = R()
    r.hit = D("10.01")
    h.s.decay_progress(p, r)
    assert p.closed


def test_m10_failure_is_not_sliding_round_search():
    h = H()
    h.ap()
    p = h.s.active("M10", "up")
    for at, hit in ((0.5, 10), (1, 9.1), (1.5, 7), (2, 5), (2.5, 3)):
        h.s.decay_round(p, h.rnd(at, hit=hit))
    assert p.closed and not h.signals("M10")


@pytest.mark.parametrize(("second_depth", "expected"), [(100, True), (90, False)])
def test_m10_two_returns_still_require_both_pressure_measures_to_decay(second_depth, expected):
    h = H()
    h.ap()
    p = h.s.active("M10", "up")
    h.s.decay_round(p, h.rnd(.5, hit=10))
    assert not h.signals("M10")
    h.s.decay_round(p, h.rnd(1, hit=9, q0=second_depth))
    signals = h.signals("M10")
    assert bool(signals) is expected
    if expected:
        assert signals[0]["payload"]["strategy_version"] == "m10-two-return-decay-v1"
        assert p.deadline == 9
        r = R()
        r.hit = D("11.26")
        h.s.decay_progress(p, r)
        assert p.closed


def parent(h):
    f = h.fact("M4_PARENT", ttl=5, values={"version": "m4-frequency-v2"})
    h.s.parent("M4", f, "root")
    return f, next(iter(h.s.m11.values()))


def add_depth(h, amount):
    h.c.books["up"].bids[D(".39")] = h.c.books["up"].bids[D(".38")] = D(100) + D(amount)


def test_m4_m11_replay_variant_requires_both_levels_and_full_25ms_hold():
    h = H(M4_M11_RETENTION_25MS)
    f, p = parent(h)
    h.at(0.8)
    h.c.books["up"].bids[D(".39")] = D(103)
    h.s.check_filter(p)
    assert p.data["cross"] is None
    h.c.books["up"].bids[D(".38")] = D(101)
    h.s.check_filter(p)
    h.at(0.824)
    assert not h.signals("M4+M11")
    h.at(0.825)
    signal, = h.signals("M4+M11")
    assert signal["payload"]["strategy_version"] == M4_M11_RETENTION_25MS
    assert p.data["floors"] == [D("1.5"), D(1)] and p.deadline == f.deadline
    assert strategy_version("M9+M11", M4_M11_RETENTION_25MS) == strategy_version("M9+M11")


def test_m11_400ms_formation_then_full_500ms_hold_and_landmark():
    h = H()
    f, p = parent(h)
    h.at(0.4)
    add_depth(h, 20)
    h.s.check_filter(p)
    h.at(0.5)
    assert not h.signals("M4+M11")
    h.at(0.899)
    assert not h.signals("M4+M11")
    h.at(0.9)
    assert len(h.signals("M4+M11")) == 1 and p.deadline == f.deadline
    h.at(1.05)
    h.s.publish()
    h.s.publish()
    assert len(h.signals("M4@1.05s")) == len(h.signals("M4+M11@1.05s")) == 1


def test_m11_breach_no_retry_and_late_spike_does_not_raise_floor():
    h = H()
    f, p = parent(h)
    h.at(0.1)
    add_depth(h, 20)
    h.s.check_filter(p)
    h.at(0.4)
    add_depth(h, 100)
    h.s.check_filter(p)
    assert p.data["floors"] == [D(10), D(10)]
    h.at(0.5)
    add_depth(h, 20)
    h.s.check_filter(p)
    h.at(0.6)
    assert len(h.signals("M4+M11")) == 1
    h.at(0.7)
    add_depth(h, 9)
    h.s.check_filter(p)
    assert p.closed
    add_depth(h, 30)
    h.s.parent("M4", f, "root")
    assert len(h.s.m11) == 1


def test_m11_prepublication_breach_suppresses_candidate_and_unknown_parent_ends():
    h = H()
    f, p = parent(h)
    h.at(0.1)
    add_depth(h, 20)
    h.s.check_filter(p)
    h.at(0.61)
    h.s.check_filter(p)
    assert p.candidate
    add_depth(h, 9)
    assert not h.signals("M4+M11")
    h = H()
    f, p = parent(h)
    h.c.unavailable("up", "GAP")
    assert p.closed


def test_m11_atomic_removed_restored_level_cannot_revive_hold():
    t = Tape()
    t.books(0)
    s = Strategies(t.feed)
    f = t.feed.fact("M4_PARENT", "up", 5, 0, 0, anchor=D(".4"))
    s.parent("M4", f, "root")
    p = next(iter(s.m11.values()))
    t.apply(
        0.1,
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "up", "side": "BUY", "price": ".39", "size": "120"},
                {"asset_id": "up", "side": "BUY", "price": ".38", "size": "120"},
            ],
        },
    )
    assert p.data["cross"] == 0.1
    t.apply(
        0.15,
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "up", "side": "BUY", "price": ".39", "size": "100"},
                {"asset_id": "up", "side": "BUY", "price": ".39", "size": "120"},
            ],
        },
    )
    assert p.closed


def test_m9_residual_limit_applies_to_rolling_total_across_trades():
    from maker_signals.model import Trade

    h, p = m9_seed(volume=3)
    h.at(2)
    h.s.retest_qualified(p, R())
    assert not p.closed
    for at in (2.05, 2.2):
        h.at(at)
        h.c.flows["up"].trades.append(
            Trade(str(at), "up", "SELL", D(".4"), D(1), at, 1000 + at, at, h.c.seq, 0, "up", False)
        )
        h.s.retest_guard(p)
    assert p.closed and not h.signals("M9")


def test_m5_cannot_reuse_same_rf_stage_after_old_candidate_ends():
    h = H()
    a = h.rnd(0.5)
    b = h.rnd(1)
    h.rf(a, b)
    h.fact("AP_SELL", ttl=0.75, start=0.5, end=1, values={"volume": D(10)}, dispatch=True)
    assert len(h.signals("M5")) == 1
    h.at(1.75, tick=True)
    h.fact("AP_SELL", ttl=0.75, start=0.9, end=1.75, values={"volume": D(10)}, dispatch=True)
    assert len(h.signals("M5")) == 1


def test_m6_complete_two_level_book_uses_actual_depth_not_minimum_three_levels():
    h = H()
    a = h.rnd(0.5)
    b = h.rnd(1)
    h.rf(a, b)
    old = {D(".42"): D(100), D(".43"): D(100)}
    h.c.books["up"].asks = {D(".42"): D(20), D(".43"): D(100)}
    h.s.depth("up", {}, old, 1)
    h.at(1.1, tick=True)
    assert len(h.signals("M6")) == 1


def test_m11_joint_cross_at_formation_deadline_is_too_late():
    h = H()
    f, p = parent(h)
    h.at(0.5)
    add_depth(h, 20)
    h.s.check_filter(p)
    assert p.closed and not h.signals("M4+M11")
