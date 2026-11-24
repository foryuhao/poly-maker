"""Frozen SOP v1.1 campaigns. Decisions only use causally available public facts.

Signal lifetime never controls the separate T240 execution experiment.
"""

from copy import deepcopy
from decimal import InvalidOperation
from statistics import median

from .feed import PARAMETER_HASH, PARAMETERS
from .model import EPS, ZERO, D
from .primitives import Rounds

M5_DEPTH_ONLY = "m5-depth-only-v1"
M5_OVERLAP_RECOVERY = "m5-overlap-recovery-v1"
M8_SINGLE_WEAK = "m8-single-weak-v1"
M8_DEPTH_REPAIR = "m8-depth-repair-v1"
M5_M8_FREQUENCY = "m5-m8-frequency-v1"
M5_M10_FREQUENCY = "m5-m7-m8-m9-m10-frequency-v1"
M5_M10_NEXT = "m5-m10-frequency-v4"
M5_M10_CALIBRATION = "m5-m10-frequency-v5-calibration"
M5_M10_FAILED_ROOT = "m5-m10-failed-root-calibration-v6"
M5_M10_LOCAL_AP = "m5-m10-local-ap-calibration-v7"
CALIBRATION_VARIANTS = (M5_M10_CALIBRATION, M5_M10_FAILED_ROOT, M5_M10_LOCAL_AP)
FREQUENCY_VARIANTS = (M5_M10_NEXT, *CALIBRATION_VARIANTS)
NEXT_PARAMETERS = {
    "M7": {"round_seconds": .25, "hold_seconds": .025, "stable_recovery": ".40",
           "pause_bin_fraction": ".35", "pause_total_fraction": ".50"},
    "M9": {"round_seconds": .25, "hold_seconds": .025, "stable_recovery": ".40",
           "support_ttl_seconds": 6, "formation_seconds": 8, "residual_fraction": ".95",
           "seed_ex_ttl_seconds": .75},
    "M10": {"round_seconds": .5, "hold_seconds": .025, "stable_recovery": ".50",
            "decay_fraction": ".99", "recovery_fraction": ".60"},
}
M4_M11_RETENTION_25MS = "m4-m11-retention-25ms-v1"
SIGNAL_VARIANTS = {
    M5_DEPTH_ONLY: {"M5": M5_DEPTH_ONLY},
    M5_OVERLAP_RECOVERY: {"M5": M5_OVERLAP_RECOVERY},
    M8_SINGLE_WEAK: {"M8": M8_SINGLE_WEAK},
    M8_DEPTH_REPAIR: {"M8": M8_DEPTH_REPAIR},
    M5_M8_FREQUENCY: {"M5": M5_OVERLAP_RECOVERY, "M8": M8_DEPTH_REPAIR},
    M4_M11_RETENTION_25MS: {"M4+M11": M4_M11_RETENTION_25MS},
}
# Retain the previously published flag as an alias for the M5/M8 frequency settings.
SIGNAL_VARIANTS[M5_M10_FREQUENCY] = SIGNAL_VARIANTS[M5_M8_FREQUENCY]
SIGNAL_VARIANTS[M5_M10_NEXT] = {**SIGNAL_VARIANTS[M5_M8_FREQUENCY],
    "M7": "m7-short-pause-single-refill-v4",
    "M9": "m9-single-support-weak-retest-v4",
    "M10": "m10-two-return-decay-v4"}
SIGNAL_VARIANTS[M5_M10_CALIBRATION] = {**SIGNAL_VARIANTS[M5_M8_FREQUENCY],
    "M7": "m7-short-pause-single-refill-v5-calibration",
    "M9": "m9-single-support-weak-retest-v5-calibration",
    "M10": "m10-two-return-decay-v5-calibration"}
SIGNAL_VARIANTS[M5_M10_FAILED_ROOT] = {**SIGNAL_VARIANTS[M5_M10_CALIBRATION],
    "M7": "m7-short-pause-single-refill-failed-root-v6"}
SIGNAL_VARIANTS[M5_M10_LOCAL_AP] = {**SIGNAL_VARIANTS[M5_M10_FAILED_ROOT],
    "M7": "m7-local-ap-short-pause-v7", "M10": "m10-local-ap-two-return-v7"}
FOLLOW_SIGNAL_VARIANTS = (M5_M8_FREQUENCY, M5_M10_FREQUENCY)
M9_RESIDUAL_FRACTION = D(
    PARAMETERS["strategies"]["M9"]["residual_max_reference_volume_fraction_exclusive"]
)


def frequency_defaults(signal_variant):
    result = deepcopy(NEXT_PARAMETERS)
    if signal_variant in CALIBRATION_VARIANTS:
        result["M7"]["pause_seconds"] = .5
        result["M9"].update(private_seed=False, seed_round_seconds=.5,
                            seed_hold_seconds=.1, seed_stable_recovery=".50")
    if signal_variant in (M5_M10_FAILED_ROOT, M5_M10_LOCAL_AP):
        result["M7"]["restart_after_failed_attack"] = False
    if signal_variant == M5_M10_LOCAL_AP:
        for strategy in ("M7", "M10"):
            result[strategy]["ap_window_seconds"] = .75
    return result


def resolve_frequency_parameters(signal_variant, overrides=None):
    """Resolve only the three target strategies; never mutate shared defaults."""
    result = frequency_defaults(signal_variant)
    if overrides is not None:
        if (signal_variant not in FREQUENCY_VARIANTS or not isinstance(overrides, dict)
                or set(overrides)-set(NEXT_PARAMETERS)):
            raise ValueError("FREQUENCY_PARAMETERS_REQUIRE_TARGET_STRATEGIES_AND_VERSION")
        for strategy, knobs in overrides.items():
            if not isinstance(knobs, dict) or set(knobs)-set(result[strategy]):
                raise ValueError("UNKNOWN_FREQUENCY_PARAMETER")
            result[strategy].update(deepcopy(knobs))
    if signal_variant not in FREQUENCY_VARIANTS:
        return result
    try:
        for p in result.values():
            if not 0 < p["hold_seconds"] < p["round_seconds"] <= .5:
                raise ValueError("INVALID_ROUND_TIMING")
            if not 0 < D(str(p["stable_recovery"])) <= 1:
                raise ValueError("INVALID_STABLE_RECOVERY")
        if (not 0 < D(str(result["M10"]["decay_fraction"])) < 1
                or not 0 < D(str(result["M10"]["recovery_fraction"])) <= 1
                or not 0 < D(str(result["M7"]["pause_bin_fraction"]))
                <= D(str(result["M7"]["pause_total_fraction"])) < 1
                or not .25 <= result["M7"].get("pause_seconds", .5) <= .5
                or not 0 < D(str(result["M9"]["residual_fraction"])) < 1):
            raise ValueError("FREQUENCY_PARAMETERS_CHANGE_REQUIRED_BEHAVIOR")
        for name in ("support_ttl_seconds", "formation_seconds", "seed_ex_ttl_seconds"):
            value = result["M9"][name]
            if type(value) not in (int, float) or not 0 < value < float("inf"):
                raise ValueError("INVALID_FREQUENCY_LIFETIME")
        if signal_variant in (M5_M10_FAILED_ROOT, M5_M10_LOCAL_AP) and not isinstance(
                result["M7"]["restart_after_failed_attack"], bool):
            raise ValueError("FAILED_ROOT_RESTART_REQUIRES_BOOLEAN")
        if signal_variant == M5_M10_LOCAL_AP:
            for strategy in ("M7", "M10"):
                value = result[strategy]["ap_window_seconds"]
                if type(value) not in (int, float) or not .25 <= value <= .75:
                    raise ValueError("INVALID_PRIVATE_AP_WINDOW")
        if signal_variant in CALIBRATION_VARIANTS:
            p = result["M9"]
            if not isinstance(p["private_seed"], bool):
                raise ValueError("PRIVATE_SEED_REQUIRES_BOOLEAN")
            if not 0 < p["seed_hold_seconds"] < p["seed_round_seconds"] <= .5:
                raise ValueError("INVALID_PRIVATE_SEED_TIMING")
            if not 0 < D(str(p["seed_stable_recovery"])) <= 1:
                raise ValueError("INVALID_PRIVATE_SEED_RECOVERY")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("INVALID_FREQUENCY_PARAMETER_VALUE") from exc
    return result


def comparable(a, b):
    return b > 0 and D(".5") <= a / b <= 2


def key(f):
    return f.confirmed, f.input_seq, f.values.get("available_subindex", -1)


def strategy_version(strategy, signal_variant=None):
    base = strategy.split("@")[0]
    if signal_variant and base in SIGNAL_VARIANTS[signal_variant]:
        return SIGNAL_VARIANTS[signal_variant][base]
    return (
        "fixed-first-legal-t15-v1"
        if base == "MAKER_CONTROL"
        else "m4-frequency-v2"
        if base == "M4"
        else PARAMETERS["strategies"]["M11" if "+" in base else base]["version"]
    )


class Campaign:
    def __init__(self, manager, strategy, token, root, deadline, anchor=None):
        self.m, self.c, self.strategy, self.token = manager, manager.c, strategy, token
        self.root, self.deadline, self.anchor = root, deadline, anchor
        self.started = self.c.now
        self.stage, self.rounds, self.refs = "FORMING", [], []
        self.candidate = self.observer = None
        self.closed = False
        self.data = {}
        self.c.event(
            "CAMPAIGN_STARTED",
            {
                "strategy": strategy,
                "token": token,
                "root_id": root,
                "deadline": deadline,
                "anchor": anchor,
            },
        )

    def end(self, reason, reason_class="HARD_INVALID"):
        if self.closed:
            return
        self.closed = True
        if self.observer:
            self.observer.closed = True
        if self.candidate:
            self.c.registry.end(self.candidate, self.c.now, reason, reason_class)
        self.c.event(
            "CAMPAIGN_ENDED",
            {
                "strategy": self.strategy,
                "token": self.token,
                "root_id": self.root,
                "reason": reason,
                "reason_class": reason_class,
            },
        )

    def observe(self, callback, gate=None, progress=None, qualified=None, *, local_window=False):
        if self.observer:
            self.observer.closed = True
        self.observer = Rounds(
            self.c,
            self.token,
            f"{self.strategy}:{self.root}",
            callback,
            gate=self.c.cursor if gate is None else gate,
            anchor=self.anchor,
            progress_callback=progress,
            qualified_callback=qualified,
            lost_callback=lambda reason: self.end(reason),
            unavailable_callback=(lambda reason: self.end(reason, "UNKNOWN"))
            if local_window or (self.m.signal_variant in CALIBRATION_VARIANTS
                                and self.strategy in NEXT_PARAMETERS) else None,
            **({"window_seconds": self.m.frequency_parameters[self.strategy]["round_seconds"],
                "hold_seconds": self.m.frequency_parameters[self.strategy]["hold_seconds"],
                "stable_recovery": self.m.frequency_parameters[self.strategy]["stable_recovery"]}
               if self.m.signal_variant in FREQUENCY_VARIANTS and self.strategy in NEXT_PARAMETERS else {}),
        )
        self.c.observers.append(self.observer)

    def confirm(self, deadline, refs, historical=(), features=None):
        if self.closed or self.candidate or self.c.now >= deadline:
            return
        rounds = {r.id for r in self.rounds}
        for f in refs:
            if f.kind.startswith("RF"):
                rounds.update(r.id for r in f.values.get("rounds", ()))
        claims = {(self.strategy, self.token, r) for r in rounds}
        if claims & self.m.consumed_rounds:
            self.end("REUSED_STAGE_ROUNDS")
            return
        self.m.consumed_rounds.update(claims)
        self.deadline, self.refs, self.stage = deadline, list(refs), "CONFIRMED"
        all_refs = tuple(dict.fromkeys([*historical, *[f.id for f in refs]]))
        facts = [self.c.registry.facts[r] for r in all_refs if r in self.c.registry.facts]
        self.candidate = self.c.fact(
            "CANDIDATE_" + self.strategy,
            self.token,
            deadline,
            min((f.start for f in facts), default=self.started),
            self.c.now,
            anchor=self.anchor,
            refs=all_refs,
            values={
                "strategy": self.strategy,
                "root_id": self.root,
                "current_required_refs": [f.id for f in refs],
                "historical_refs": list(historical),
                "feature_values": features or {},
                "component_deadlines": {f.id: f.deadline for f in refs},
            },
        )
        self.m.pending.append(self)


class Strategies:
    def __init__(self, context, outcomes=None, *, signal_variant=None, parent_filters=True,
                 frequency_parameters=None):
        self.c = context
        self.signal_variant = signal_variant
        self.parent_filters = parent_filters
        self.frequency_parameters = resolve_frequency_parameters(signal_variant, frequency_parameters)
        variants = SIGNAL_VARIANTS[signal_variant] if signal_variant else {}
        self.m5_variant = variants.get("M5")
        self.m8_variant = variants.get("M8")
        self.m9_support = {}
        self.m9_seed_observers = {}
        self.outcomes = outcomes or dict(zip(context.tokens, ("Up", "Down"), strict=True))
        self.c.strategies = self
        self.campaigns, self.roots, self.pending, self.published = {}, set(), [], set()
        self.facts = {t: {} for t in context.tokens}
        self.consumed_rounds = set()
        self.lw, self.m11, self.parents, self.parent_roots = {}, {}, {}, set()
        self.lw_reset_seq = {}
        self.m8_reference = {}
        self.private_ap = {}
        if signal_variant == M5_M10_LOCAL_AP:
            from .local_pressure import LocalPressure

            for strategy in ("M7", "M10"):
                window = self.frequency_parameters[strategy]["ap_window_seconds"]
                if window < .75:
                    for token in context.tokens:
                        self.private_ap[strategy, token] = LocalPressure(self, strategy, token, window)

    @property
    def private_m9_seed(self):
        return (self.signal_variant in CALIBRATION_VARIANTS
                and self.frequency_parameters["M9"]["private_seed"])

    def end_private_m9_support(self, token, reason, reason_class="HARD_INVALID"):
        observer = self.m9_seed_observers.get(token)
        if observer is not None:
            observer.closed = True
        support = self.m9_support.pop(token, None)
        if support is not None:
            self.c.registry.end(support, self.c.now, reason, reason_class)

    def private_m9_round(self, fact):
        if fact.values["qualified"]:
            self.record_m9_support(fact)

    def ensure_private_m9_seed(self, token):
        if not self.private_m9_seed or not self.c.good[token] or not self.c.legal(token):
            return
        observer = self.m9_seed_observers.get(token)
        if observer is None or observer.closed:
            p = self.frequency_parameters["M9"]
            observer = Rounds(self.c, token, "m9-private-seed", self.private_m9_round,
                window_seconds=p["seed_round_seconds"], hold_seconds=p["seed_hold_seconds"],
                stable_recovery=p["seed_stable_recovery"],
                lost_callback=lambda reason: self.end_private_m9_support(token, reason),
                unavailable_callback=lambda reason: self.end_private_m9_support(token, reason, "UNKNOWN"))
            self.m9_seed_observers[token] = observer
            self.c.observers.append(observer)
        if observer.round is None and not self.c.registry.valid(self.m9_support.get(token), self.c.now):
            observer.anchor = None

    def release_runtime(self):
        """Drop decision state after the final durable snapshot, without emits."""
        campaigns = {id(p): p for p in (*self.campaigns.values(), *self.m11.values(),
                                      *self.pending)}
        for campaign in campaigns.values():
            if campaign.observer is not None:
                campaign.observer.release_runtime()
            campaign.c = campaign.m = None
        for pressure in self.private_ap.values():
            pressure.release_runtime()
        self.private_ap.clear()
        for values in (self.campaigns, self.roots, self.pending, self.published, self.facts,
                       self.consumed_rounds, self.lw, self.m11, self.parents, self.parent_roots,
                       self.lw_reset_seq, self.m8_reference, self.m9_support, self.m9_seed_observers):
            values.clear()
        self.c = None
        return len(campaigns)

    def active(self, strategy, token):
        p = self.campaigns.get((strategy, token))
        return p if p and not p.closed else None

    def start(self, strategy, token, root, deadline, anchor=None):
        ident = (strategy, token, root)
        # Seen roots are consumed even while another campaign owns the token.
        if ident in self.roots:
            return None
        self.roots.add(ident)
        if self.active(strategy, token):
            self.c.event(
                "ROOT_DIAGNOSTIC_ONLY", {"strategy": strategy, "token": token, "root_id": root}
            )
            return None
        p = Campaign(self, strategy, token, root, deadline, anchor)
        self.campaigns[strategy, token] = p
        return p

    def current(self, token, kind):
        return [f for f in self.facts[token].get(kind, ()) if self.c.registry.valid(f, self.c.now)]

    def start_attack_campaign(self, strategy, fact):
        p = self.start(strategy, fact.token, fact.id, fact.confirmed + 20)
        if not p:
            return
        if strategy == "M7":
            p.data["ap1"] = fact
            p.stage = "WAIT_EX"
            p.observe(lambda r, p=p: self.reattack_round(p, r), gate=key(fact), local_window=True)
        else:
            p.data["ap"] = fact
            p.observe(lambda r, p=p: self.decay_round(p, r), gate=key(fact),
                      progress=lambda r, p=p: self.decay_progress(p, r), local_window=True)

    def on_private_ap(self, strategy, token, watch, fact):
        if strategy == "M7":
            self.on_ap_verdict(token, watch, fact, local=True)
        if watch["side"] == "BUY":
            self.start_attack_campaign(strategy, fact)
        elif strategy == "M7":
            p = self.active("M7", token)
            if p and "ex" not in p.data:
                p.end("REVERSE_AP")

    def on_fact(self, fact):
        t = fact.token
        self.facts[t].setdefault(fact.kind, []).append(fact)
        if fact.kind in ("AP_BUY", "AP_SELL"):
            opposite = "SELL" if fact.kind == "AP_BUY" else "BUY"
            for strategy in ("M7", "M10"):
                pressure = self.private_ap.get((strategy, t))
                if pressure is not None:
                    pressure.end_active(opposite, "REVERSE_AP")
        seed = self.active("M9", t)
        if (seed and "seed_ex_source" in seed.data
                and self.c.registry.valid(seed.data["seed_ex"], self.c.now)
                and fact.id != seed.data["ap"].id and (
                    fact.kind == "AP_BUY" or fact.kind == "AP_SELL"
                    and fact.values["volume"] >= seed.data["ap"].values["volume"]*D(".50"))):
            reason = "REVERSE_AP" if fact.kind == "AP_BUY" else "EX_PRESSURE_RESUMED"
            self.c.registry.end(seed.data["seed_ex"], self.c.now, reason)
            if not seed.data["qualified"] or reason != "REVERSE_AP":
                seed.end("SEED_"+reason)
        if fact.kind == "AP_BUY":
            # A new positive root is diagnostic while its campaign is alive.
            for strategy in ("M7", "M10"):
                if (strategy, t) not in self.private_ap:
                    self.start_attack_campaign(strategy, fact)
        if fact.kind == "AP_SELL":
            p = self.active("M7", t)
            if p and "ex" not in p.data:
                p.end("REVERSE_AP")
        if fact.kind == "EX_SELL":
            support = self.m9_support.get(t)
            if self.c.registry.valid(support, self.c.now):
                self.combine_m9(t, support)
        self.combine(t)

    def combine(self, t):
        compare_hit = self.m5_variant not in (M5_DEPTH_ONLY, M5_OVERLAP_RECOVERY)
        compare_depth = self.m5_variant != M5_OVERLAP_RECOVERY
        for rf in self.current(t, "RF_BID"):
            rounds = rf.values["rounds"]
            a, b = rounds
            for ap in self.current(t, "AP_SELL"):
                if (
                    min(ap.end, b.end) > max(ap.start, b.start)
                    and (not compare_hit or comparable(b.values["u"], a.values["u"]))
                    and (not compare_depth or comparable(b.values["d"], a.values["d"]))
                    and b.values["recovery"] >= D(".8") * a.values["recovery"]
                ):
                    p = self.start(
                        "M5", t, rf.id + "|" + ap.id, min(rf.deadline, ap.deadline), rf.anchor
                    )
                    if p:
                        features = {"rounds": [a.id, b.id]}
                        if not compare_hit:
                            features["relative_hit_ratio"] = (
                                b.values["u"] / a.values["u"] if a.values["u"] > 0 else None
                            )
                        if not compare_depth:
                            features["relative_depth_ratio"] = (
                                b.values["d"] / a.values["d"] if a.values["d"] > 0 else None
                            )
                        p.confirm(p.deadline, [rf, ap], features=features)
            for lw in self.current(t, "LW_ASK"):
                p = self.start(
                    "M6", t, rf.id + "|" + lw.id, min(rf.deadline, lw.deadline), rf.anchor
                )
                if p:
                    p.confirm(p.deadline, [rf, lw])

    def combine_m9(self, token, rf):
        c = self.c
        for ex in self.current(token, "EX_SELL"):
            ap = c.registry.facts[ex.refs[0]]
            if c.now - min(rf.start, ap.start) > 20 + EPS:
                continue
            root = rf.id + "|" + ex.id
            seed_ex = ex
            ttl = (self.frequency_parameters["M9"]["seed_ex_ttl_seconds"]
                   if self.signal_variant in FREQUENCY_VARIANTS else .75)
            if not .75 <= ttl <= 3:
                raise ValueError("INVALID_M9_SEED_EX_TTL")
            deadline = min(rf.deadline, ex.deadline if ttl == .75 else ex.deadline+(ttl-.75))
            p = self.start("M9", token, root, deadline, rf.anchor)
            if p:
                if ttl > .75:
                    # Freeze a strategy-local freshness budget while EX is
                    # still alive. Never extend the shared fact or renew it on
                    # the first small hit. Hard source invalidations still win.
                    seed_ex = c.fact("EX_SELL_M9_SEED", token, deadline, ex.start, ex.end,
                        anchor=ex.anchor, refs=[ex.id], values={
                            "source_ex_id": ex.id, "source_original_deadline": ex.deadline,
                            "seed_ttl_seconds": ttl, "frozen_seed_deadline": deadline,
                            "volume": ap.values["volume"], "pressure_resume_fraction": ".50"})
                    p.data["seed_ex_source"] = ex
                p.data.update(
                    seed_rf=rf,
                    seed_ex=seed_ex,
                    ap=ap,
                    qualified=False,
                    seed_deadline=p.deadline,
                    formation_deadline=c.now + (self.frequency_parameters["M9"]["formation_seconds"]
                                               if self.signal_variant in FREQUENCY_VARIANTS else 5),
                )
                p.observe(
                    lambda r, p=p: self.retest_round(p, r),
                    qualified=lambda r, p=p: self.retest_qualified(p, r),
                    progress=lambda r, p=p: self.retest_progress(p, r),
                    local_window=True,
                )
                self.retest_guard(p)

    def on_ap_verdict(self, token, watch, fact, *, local=False):
        if local != (("M7", token) in self.private_ap):
            return
        p = self.active("M7", token)
        if not p or watch["side"] != "BUY" or fact is None:
            return
        if p.stage in ("WAIT_EX", "RF") and fact.id != p.root:
            restart = (self.signal_variant in (M5_M10_FAILED_ROOT, M5_M10_LOCAL_AP)
                       and self.frequency_parameters["M7"]["restart_after_failed_attack"]
                       and p.candidate is None)
            if not restart:
                self.roots.add(("M7", token, fact.id))
            p.end("EARLY_REATTACK")
            return
        if p.stage == "WAIT_AP2":
            if tuple(watch["start_key"]) <= p.data["rf_key"]:
                p.end("REATTACK_STARTED_BEFORE_RF")
                return
            rf = p.data["rf"]
            if self.c.registry.valid(rf, self.c.now):
                p.confirm(
                    min(fact.deadline, rf.deadline, p.deadline),
                    [rf, fact],
                    [p.root, p.data["ex"].id],
                )

    def reattack_round(self, p, r):
        if p.closed or not r.values["qualified"]:
            return
        if not r.values["stable"]:
            p.end("QUALIFIED_REFILL_FAILED")
            return
        if "rf" in p.data:
            return
        p.anchor = r.anchor
        p.rounds.append(r)
        if self.c.now - r.start > 15 + EPS:
            p.end("RF_FORMATION_TIMEOUT")
            return
        rf = self.new_rf(p, p.rounds)
        p.data.update(rf=rf, rf_key=key(rf))
        if "ex" in p.data:
            p.stage = "WAIT_AP2"
        # A BUY window that started before this RF must still reach its own verdict.

    def check_short_pause(self, p):
        c = self.c
        ap = p.data["ap1"]
        start = ap.confirmed
        knobs = self.frequency_parameters["M7"] if self.signal_variant in FREQUENCY_VARIANTS else {
            "pause_bin_fraction": ".20", "pause_total_fraction": ".35"}
        duration = knobs.get("pause_seconds", .5)
        end = start + duration
        if c.now > end + 0.050 + EPS:
            p.end("EX_WINDOW_UNAVAILABLE", "UNKNOWN")
            return
        if c.watermark < end - EPS:
            return
        flow = c.flows[p.token]
        if not c.good[p.token] or not flow.covered(start, end):
            p.end("EX_WINDOW_UNAVAILABLE", "UNKNOWN")
            return
        volumes = [
            flow.volume(left, right, "BUY", after_key=key(ap))
            for left, right in ((start, start + duration/2), (start + duration/2, end))
        ]
        reference = ap.values["volume"]
        if (max(volumes) > reference * D(knobs["pause_bin_fraction"])
                or sum(volumes, ZERO) > reference * D(knobs["pause_total_fraction"])):
            p.end("EX_NOT_CONFIRMED")
            return
        ex = c.fact(
            "EX_BUY_M7_SHORT",
            p.token,
            c.now + 0.75,
            start,
            end,
            refs=[ap.id],
            values={"volume": reference, "subwindow_volumes": volumes,
                    **({"pause_seconds": duration} if self.signal_variant in CALIBRATION_VARIANTS else {})},
        )
        p.data["ex"] = ex
        if "rf" in p.data:
            p.data["rf_key"] = key(ex)
            p.stage = "WAIT_AP2"
        else:
            p.stage = "RF"

    def new_rf(self, p, rounds):
        f = self.c.fact(
            "RF_" + p.strategy,
            p.token,
            self.c.now + 8,
            rounds[0].start,
            rounds[-1].end,
            anchor=p.anchor,
            refs=[r.id for r in rounds],
            values={"rounds": tuple(rounds)},
        )
        return f

    def decay_round(self, p, r):
        if p.closed or not r.values["qualified"]:
            return
        v = r.values
        if not v["stable"]:
            p.end("QUALIFIED_REFILL_FAILED")
            return
        if p.candidate:
            last = p.rounds[-1]
            if v["recovery"] < max(D(".5"), D(".8") * last.values["recovery"]):
                p.end("RECOVERY_WEAKENED")
            return
        p.anchor = r.anchor
        if p.rounds:
            prev = p.rounds[-1].values
            knobs = self.frequency_parameters["M10"] if self.signal_variant in FREQUENCY_VARIANTS else {
                "decay_fraction": ".9", "recovery_fraction": ".8"}
            if (
                v["hit"] > D(knobs["decay_fraction"]) * prev["hit"]
                or v["u"] > D(knobs["decay_fraction"]) * prev["u"]
                or v["recovery"] < D(knobs["recovery_fraction"]) * prev["recovery"]
            ):
                p.end("RETURN_DECAY_FAILED")
                return
        p.rounds.append(r)
        if self.c.now - p.rounds[0].start > 15 + EPS:
            p.end("RETURN_SEQUENCE_TIMEOUT")
            return
        if len(p.rounds) == 2:
            rf = self.new_rf(p, p.rounds)
            p.confirm(
                min(rf.deadline, p.deadline),
                [rf],
                [p.root, *[r.id for r in p.rounds]],
                {
                    "RF12_confirmed_available_at": rf.confirmed,
                    "R2_evidence_start": r.start,
                    "R2_confirmed_available_at": r.confirmed,
                },
            )

    def decay_progress(self, p, r):
        if p.closed or not p.candidate:
            return
        last = p.rounds[-1].values
        if r.hit > D("1.25") * last["hit"] or r.hit / r.initial > D("1.25") * last["u"]:
            p.end("RETURN_PRESSURE_RESUMED")

    def retest_guard(self, p):
        if p.closed:
            return
        c = self.c
        d = p.data
        if not c.good[p.token]:
            p.end("REQUIRED_INPUT_UNAVAILABLE", "UNKNOWN")
            return
        volume = c.flows[p.token].volume(c.now - 0.750, c.now, "SELL", frontier=True)
        if ("seed_ex_source" in d and c.registry.valid(d["seed_ex"], c.now)
                and volume >= d["ap"].values["volume"]*D(".50")):
            c.registry.end(d["seed_ex"], c.now, "EX_PRESSURE_RESUMED")
            p.end("SEED_EX_PRESSURE_RESUMED")
            return
        if volume >= d["ap"].values["volume"] * self.m9_residual_fraction():
            p.end("RETEST_RESIDUAL_PRESSURE_RESUMED")
            return
        original = d.get("seed_ex_source")
        source_end = c.registry.endings.get(original.id) if original else None
        if (source_end and source_end["reason_class"] != "SOFT_EXPIRED"
                and not (d["qualified"] and source_end["reason"] == "REVERSE_AP")):
            c.registry.end(d["seed_ex"], c.now, source_end["reason"], source_end["reason_class"])
            p.end("SEED_"+source_end["reason"], source_end["reason_class"])
            return
        if not d["qualified"]:
            if not all(c.registry.valid(d[k], c.now) for k in ("seed_rf", "seed_ex")):
                p.end("SEED_ENDED_BEFORE_QUALIFICATION")
                return
        else:
            for k in ("seed_rf", "seed_ex"):
                end = c.registry.endings.get(d[k].id)
                # Pure TTL and a reverse BUY AP ending old EX are historical.
                if (
                    end
                    and end["reason_class"] != "SOFT_EXPIRED"
                    and not (k == "seed_ex" and end["reason"] == "REVERSE_AP")
                ):
                    p.end("SEED_" + end["reason"], end["reason_class"])
                    return
        if not p.candidate and c.now >= d["formation_deadline"]:
            p.end("RETEST_FORMATION_TIMEOUT", "SOFT_EXPIRED")

    def retest_progress(self, p, r):
        if p.closed:
            return
        self.retest_guard(p)
        if r.minimum >= p.data["ap"].values["volume"] * self.m9_residual_fraction():
            p.end("RETEST_RESIDUAL_BUDGET_TOO_SMALL")

    def retest_qualified(self, p, r):
        self.retest_progress(p, r)
        if p.closed:
            return
        if not p.data["qualified"]:
            if self.c.now >= p.data["seed_deadline"]:
                p.end("SEED_ENDED_BEFORE_QUALIFICATION")
                return
            p.data["qualified"] = True
            p.deadline = p.data["formation_deadline"]
            p.data["first_qualified"] = self.c.now

    def retest_round(self, p, r):
        if p.closed:
            return
        self.retest_guard(p)
        if p.closed:
            return
        if not r.values["qualified"]:
            if not p.rounds and not p.candidate:
                p.end("FIRST_RETEST_ROUND_UNQUALIFIED")
            return
        if not r.values["stable"]:
            p.end("QUALIFIED_REFILL_FAILED")
            return
        if p.candidate:
            return
        p.rounds.append(r)
        rf = self.new_rf(p, p.rounds)
        p.confirm(
            min(rf.deadline, p.data["ap"].confirmed + 20),
            [rf],
            [p.data["seed_rf"].id, p.data["seed_ex"].id, p.data["ap"].id],
            {
                "retest_started_at": r.start,
                "retest_first_qualified_available_at": p.data["first_qualified"],
                "seed_original_deadline": p.data["seed_deadline"],
                "residual_reference_volume": p.data["ap"].values["volume"],
            },
        )

    def on_round(self, r):
        # M8 reference/weakening uses all qualified shared rounds, never a
        # selected favorable subsequence. Repair owns a separate post-DF observer.
        t = r.token
        if not r.values["qualified"]:
            return
        if not self.private_m9_seed:
            self.record_m9_support(r)
        if self.active("M8", t):
            return
        v = r.values
        if self.m8_variant == M8_DEPTH_REPAIR:
            if v["recovery"] <= D(".30"):
                self.start_repair((r,), threshold=D(".60"))
            return
        state = self.m8_reference.setdefault(t, {"refs": [], "weak": []})
        refs = state["refs"]
        if len(refs) < 2:
            if not v["stable"]:
                refs.clear()
                return
            refs.append(r)
            if len(refs) == 2 and (
                r.confirmed - refs[0].start > 5 + EPS
                or median(x.values["recovery"] for x in refs) < D(".7")
            ):
                refs.clear()
            return
        if (
            r.anchor != refs[0].anchor
            or self.c.now - refs[-1].confirmed > 5 + EPS
            or not comparable(v["u"], median(x.values["u"] for x in refs))
            or not comparable(v["d"], median(x.values["d"] for x in refs))
            or v["recovery"] > D(".3")
            or v["recovery"] > D(".5") * median(x.values["recovery"] for x in refs)
        ):
            self.m8_reference.pop(t, None)
            return
        state["weak"].append(r)
        required_weak_rounds = 1 if self.m8_variant == M8_SINGLE_WEAK else 2
        if len(state["weak"]) == required_weak_rounds:
            self.start_repair(
                tuple(state["weak"]),
                reference=tuple(refs),
                threshold=max(D(".5"), D(".8") * median(x.values["recovery"] for x in refs)),
            )
            self.m8_reference.pop(t, None)

    def record_m9_support(self, r):
        c, token = self.c, r.token
        support = self.m9_support.get(token)
        if not r.values["stable"]:
            if support is not None:
                c.registry.end(support, c.now, "QUALIFIED_REFILL_FAILED")
                self.m9_support.pop(token)
            return
        if c.registry.valid(support, c.now):
            return
        support = c.fact(
            "RF_M9_SEED",
            token,
            c.now + (self.frequency_parameters["M9"]["support_ttl_seconds"]
                     if self.signal_variant in FREQUENCY_VARIANTS else 3),
            r.start,
            r.end,
            anchor=r.anchor,
            refs=[r.id],
            values={"rounds": (r,)},
        )
        self.m9_support[token] = support
        self.combine_m9(token, support)

    def m9_residual_fraction(self):
        return (D(self.frequency_parameters["M9"]["residual_fraction"])
                if self.signal_variant in FREQUENCY_VARIANTS else M9_RESIDUAL_FRACTION)

    def start_repair(self, weak, *, reference=(), threshold):
        rounds = reference + weak
        last = weak[-1]
        df = self.c.fact(
            "DF_BID",
            last.token,
            self.c.now + 1,
            rounds[0].start,
            last.end,
            anchor=last.anchor,
            refs=[r.id for r in rounds],
            values={"reference": reference, "weak": weak},
        )
        p = self.start("M8", last.token, df.id, self.c.now + 5, last.anchor)
        if p:
            p.data.update(df=df, threshold=threshold)
            p.observe(lambda r: self.repair_round(p, r), gate=key(df))

    def repair_round(self, p, r):
        if p.closed or not r.values["qualified"]:
            return
        v = r.values
        weak = p.data["df"].values["weak"]
        if not v["stable"] or v["recovery"] < p.data["threshold"]:
            p.end("REPAIR_RECOVERY_FAILED")
            return
        if v["recovery"] >= D(".7"):
            self.c.registry.end(p.data["df"], self.c.now, "SUPPORT_RECOVERED")
        if p.candidate:
            return
        depth_repair = self.m8_variant == M8_DEPTH_REPAIR
        if (
            (not depth_repair and not comparable(v["u"], median(x.values["u"] for x in weak)))
            or not comparable(v["d"], median(x.values["d"] for x in weak))
        ):
            p.end("REPAIR_IMPACT_NOT_COMPARABLE")
            return
        p.rounds.append(r)
        required_repair_rounds = 1 if depth_repair else 2
        if len(p.rounds) == required_repair_rounds:
            if self.c.now >= p.deadline:
                p.end("REPAIR_TIMEOUT", "SOFT_EXPIRED")
                return
            rf = self.new_rf(p, p.rounds)
            p.confirm(
                rf.deadline, [rf], [p.root], {"repair_recovery_threshold": p.data["threshold"]}
            )

    def invalidate(self, token, reason):
        for (_strategy, target), pressure in self.private_ap.items():
            if target == token:
                pressure.reset(reason)
        if self.private_m9_seed:
            self.end_private_m9_support(token, reason, "UNKNOWN")
        for p in tuple(self.campaigns.values()):
            if p.token == token:
                p.end(reason, "UNKNOWN")
        self.lw.pop(token, None)
        self.m8_reference.pop(token, None)
        self.m9_support.pop(token, None)
        for p in tuple(self.m11.values()):
            if p.token == token:
                p.end(reason, "UNKNOWN")

    def trade(self, t):
        for (_strategy, token), pressure in self.private_ap.items():
            if token == t.token:
                pressure.trade(t)
        p = self.active("M9", t.token)
        if p:
            self.retest_guard(p)
        self.check_lw(t.token)

    def depth(self, t, old_bids, old_asks, received):
        self.ensure_private_m9_seed(t)
        book = self.c.books[t]
        if t not in self.lw and old_asks and self.lw_reset_seq.get(t) != self.c.seq:
            levels = sorted(old_asks)[:3]
            q0 = sum((old_asks[x] for x in levels), ZERO)
            if q0 > 0 and sum((book.asks.get(x, ZERO) for x in levels), ZERO) < q0:
                self.lw[t] = {
                    "levels": levels,
                    "q0": q0,
                    "start": received,
                    "hold": None,
                    "fact": None,
                }
        self.check_lw(t)
        self.guard_anchors(t)
        for p in tuple(self.m11.values()):
            if p.token == t:
                self.check_filter(p)

    def intermediate_depth(self, t):
        self.guard_anchors(t)
        for p in tuple(self.m11.values()):
            if p.token == t and not p.closed:
                d = p.data
                book = self.c.books[t]
                if any(x not in book.bids for x in d.get("levels", ())):
                    p.end("M11_LEVEL_LOST")
                elif d.get("cross") is not None and any(
                    book.bids[x] - q < f
                    for x, q, f in zip(d["levels"], d["q0"], d["floors"], strict=True)
                ):
                    p.end("M11_RETENTION_BREACH")

    def ask_removed(self, t, price):
        self.lw_reset_seq[t] = self.c.seq
        w = self.lw.get(t)
        if w and w["levels"][0] == price:
            if w["fact"]:
                self.c.registry.end(w["fact"], self.c.now, "LW_ORIGINAL_ASK_LOST")
            self.lw.pop(t, None)

    def check_lw(self, t):
        c = self.c
        w = self.lw.get(t)
        if not w:
            return
        book = c.books[t]
        q0 = w["q0"]
        loss = max(ZERO, q0 - sum((book.asks.get(x, ZERO) for x in w["levels"]), ZERO))
        executed = sum(
            (
                x.quantity
                for x in c.flows[t].trades
                if x.side == "BUY"
                and x.price in w["levels"]
                and w["start"] - 0.5 <= x.received <= c.now
            ),
            ZERO,
        )
        residual = max(ZERO, loss - executed)
        fraction = residual / loss if loss else ZERO
        if not c.good[t] or w["levels"][0] not in book.asks:
            if w["fact"]:
                c.registry.end(w["fact"], c.now, "LW_ANCHOR_OR_QUALITY_LOST")
            self.lw.pop(t, None)
            return
        if w["fact"]:
            if loss == 0 or residual / q0 < D(".1") or fraction < D(".25"):
                c.registry.end(w["fact"], c.now, "SUPPLY_RESTORED")
                self.lw.pop(t, None)
            elif not c.registry.valid(w["fact"], c.now):
                self.lw.pop(t, None)
            return
        if c.now >= w["start"] + 0.75 - EPS:
            self.lw.pop(t, None)
            return
        # A covered prehistory is necessary to subtract actual executions.
        good = (
            loss / q0 >= D(".3")
            and residual / q0 >= D(".2")
            and fraction >= D(".5")
            and c.flows[t].covered(w["start"] - 0.5, c.now)
        )
        if not good:
            w["hold"] = None
            return
        if w["hold"] is None:
            w["hold"] = c.now
        if c.watermark >= w["hold"] + 0.1 - EPS:
            f = c.fact(
                "LW_ASK",
                t,
                c.now + 0.75,
                w["start"],
                c.now,
                anchor=w["levels"][0],
                values={
                    "levels": w["levels"],
                    "q0": q0,
                    "loss": loss,
                    "executed_buy": executed,
                    "residual": residual,
                    "residual_fraction": fraction,
                },
            )
            w["fact"] = f
            self.on_fact(f)

    def guard_anchors(self, t):
        c = self.c
        support = self.m9_support.get(t)
        if support is not None and c.books[t].bids.get(support.anchor, ZERO) <= 0:
            c.registry.end(support, c.now, "ANCHOR_LOST")
            self.m9_support.pop(t)
        for p in tuple(self.campaigns.values()):
            if (
                p.token == t
                and not p.closed
                and p.anchor is not None
                and c.books[t].bids.get(p.anchor, ZERO) <= 0
            ):
                p.end("ANCHOR_LOST")

    def tick(self):
        c = self.c
        for pressure in self.private_ap.values():
            pressure.close()
        for t in c.tokens:
            self.ensure_private_m9_seed(t)
            self.check_lw(t)
            self.guard_anchors(t)
            for kind, facts in self.facts[t].items():
                self.facts[t][kind] = [f for f in facts if c.registry.valid(f, c.now)]
            state = self.m8_reference.get(t)
            if state and state["refs"] and c.now - state["refs"][0].start > 10 + EPS:
                self.m8_reference.pop(t, None)
        for p in tuple(self.campaigns.values()):
            if p.closed:
                continue
            if p.strategy == "M9":
                self.retest_guard(p)
            if c.now >= p.deadline:
                p.end("ORIGINAL_DEADLINE", "SOFT_EXPIRED")
                continue
            if p.strategy == "M7" and p.stage == "WAIT_EX":
                self.check_short_pause(p)
            if (
                p.strategy == "M7"
                and p.stage == "WAIT_AP2"
                and not c.registry.valid(p.data["rf"], c.now)
            ):
                p.end("RF_ENDED")
            for f in p.refs:
                if not c.registry.valid(f, c.now):
                    ending = c.registry.endings.get(
                        f.id, {"reason": "DEPENDENCY_EXPIRED", "reason_class": "SOFT_EXPIRED"}
                    )
                    p.end(ending["reason"], ending["reason_class"])
        for p in tuple(self.m11.values()):
            self.check_filter(p)

    def payload(self, p):
        f = p.candidate
        strategy = p.strategy
        version = strategy_version(strategy, self.signal_variant)
        return {
            "id": f.id,
            "candidate_id": f.id,
            "strategy": strategy,
            "strategy_version": version,
            **({"strategy_parameters": self.frequency_parameters[strategy]}
               if self.signal_variant in FREQUENCY_VARIANTS and strategy in NEXT_PARAMETERS else {}),
            "spec_version": PARAMETERS["spec_version"],
            "parameter_hash": PARAMETER_HASH,
            "market_id": self.c.market,
            "token": f.token,
            "token_id": f.token,
            "outcome": self.outcomes[f.token],
            "generation": f.generation,
            "root_id": p.root,
            "parent_candidate_id": p.data.get("parent_id"),
            "price": str(f.anchor),
            "anchor_price": str(f.anchor),
            "confirmed": f.confirmed,
            "confirmed_available_at": f.confirmed,
            "deadline": f.deadline,
            "original_deadline": f.deadline,
            "input_seq": f.input_seq,
            "evidence_start": f.start,
            "evidence_end": f.end,
            "primitive_ids": list(f.refs),
            "round_ids": [x.id for x in p.rounds],
            "event_ids": list(
                dict.fromkeys(e for x in p.rounds for e in x.values.get("event_ids", []))
            ),
            "observation_valid": True,
            "signal_valid": True,
            "entry_valid": True,
            "holding_validity": "VALID",
            "quality_status": "OK",
            "end_reason": None,
            "reason_class": None,
            "signal_remaining_ms_at_confirmation": (f.deadline - f.confirmed) * 1000,
            **f.values,
        }

    def publish(self):
        self.tick()
        pending, self.pending = self.pending, []
        for p in pending:
            f = p.candidate
            if p.closed or not self.c.good[p.token] or not self.c.registry.valid(f, self.c.now):
                self.c.event(
                    "CANDIDATE_SUPPRESSED",
                    {"id": f.id, "strategy": p.strategy, "reason": "INVALID_BEFORE_PUBLICATION"},
                )
                continue
            self.published.add(f.id)
            self.c.emit(
                "SIGNAL", {"strategy": p.strategy, "event": "CANDIDATE", "payload": self.payload(p)}
            )
            if self.parent_filters and p.strategy in ("M9", "M10"):
                self.parent(p.strategy, f, p.root)

    def candidates(self):
        self.tick()
        return [
            self.payload(p)
            for p in (*self.campaigns.values(), *self.m11.values())
            if not p.closed
            and p.candidate
            and p.candidate.id in self.published
            and self.c.registry.valid(p.candidate, self.c.now)
        ]

    def parent(self, strategy, fact, root):
        """Mother facts must carry their frozen original explicit validity."""
        ident = (strategy, fact.token, root)
        if ident in self.parent_roots:
            return
        self.parent_roots.add(ident)
        self.parents[fact.id] = fact
        c = self.c
        book = c.books[fact.token]
        p = Campaign(self, strategy + "+M11", fact.token, root, fact.deadline, fact.anchor)
        p.data.update(
            parent=fact,
            parent_id=fact.id,
            parent_strategy=strategy,
            landmark=fact.confirmed + 1.05,
            landmark_done=False,
        )
        self.m11[ident] = p
        levels = sorted((x for x in book.bids if x < fact.anchor), reverse=True)[:2]
        if len(levels) != 2 or not c.good[fact.token]:
            p.end("M11_REQUIRED_LEVELS_UNAVAILABLE", "UNKNOWN")
            return
        quantities = [book.bids[x] for x in levels]
        tuned_m4 = strategy == "M4" and self.signal_variant == M4_M11_RETENTION_25MS
        p.data.update(
            levels=levels,
            q0=quantities,
            threshold=[D(1)] * 2 if tuned_m4 else [max(D(".5"), q * D(".1")) for q in quantities],
            formation_seconds=1.0 if tuned_m4 else 0.5,
            retention_seconds=0.025 if tuned_m4 else 0.5,
            cross=None,
        )

    def check_filter(self, p):
        c = self.c
        d = p.data
        parent = d["parent"]
        parent_valid = c.registry.valid(parent, c.now) and c.good[p.token]
        if not d["landmark_done"] and c.now >= d["landmark"]:
            d["landmark_done"] = True
            if c.now > d["landmark"] + 0.05 + EPS:
                c.event(
                    "LANDMARK_UNAVAILABLE", {"parent_id": parent.id, "reason": "PROCESSING_LATE"}
                )
            elif parent_valid:
                self.landmark(p, False)
                if not p.closed and p.candidate and c.registry.valid(p.candidate, c.now):
                    self.landmark(p, True)
        if p.closed:
            return
        if not parent_valid:
            p.end("PARENT_ENDED_OR_UNKNOWN", "UNKNOWN" if not c.good[p.token] else "HARD_INVALID")
            return
        book = c.books[p.token]
        if any(x not in book.bids for x in d["levels"]):
            p.end("M11_LEVEL_LOST")
            return
        delta = [book.bids[x] - q for x, q in zip(d["levels"], d["q0"], strict=True)]
        if d["cross"] is None:
            if c.now >= parent.confirmed + d["formation_seconds"] - EPS:
                p.end("M11_FORMATION_TIMEOUT", "SOFT_EXPIRED")
                return
            if all(x >= a for x, a in zip(delta, d["threshold"], strict=True)):
                d.update(
                    cross=c.now,
                    adds=delta,
                    floors=[
                        max(a, x * D(".5")) for x, a in zip(delta, d["threshold"], strict=True)
                    ],
                )
            return
        if any(x < f for x, f in zip(delta, d["floors"], strict=True)):
            p.end("M11_RETENTION_BREACH")
            return
        end = d["cross"] + d["retention_seconds"]
        if not p.candidate and c.watermark >= end - EPS:
            if c.now > end + 0.05 + EPS or not c.flows[p.token].covered(d["cross"], end):
                p.end("M11_HOLD_UNAVAILABLE", "UNKNOWN")
                return
            p.confirm(
                parent.deadline,
                [parent],
                features={
                    "M11_first_joint_cross_available_at": d["cross"],
                    "M11_hold_end": end,
                    "M11_hold_confirmed_available_at": c.now,
                    "M11_baseline_quantities": d["q0"],
                    "M11_first_cross_net_adds": d["adds"],
                    "M11_frozen_retention_floors": d["floors"],
                    "M11_evidence_kind": "AGGREGATE_VISIBLE_NET_DEPTH_NOT_ORDER_IDENTITY",
                },
            )

    def landmark(self, p, filtered):
        name = p.data["parent_strategy"] + ("+M11" if filtered else "") + "@1.05s"
        f = p.data["parent"]
        q = Campaign(self, name, p.token, p.root, f.deadline, p.anchor)
        q.data["parent_id"] = f.id
        q.confirm(
            f.deadline, [f], features={"fixed_landmark": p.data["landmark"], "filtered": filtered}
        )
        self.campaigns[name, p.token] = q
