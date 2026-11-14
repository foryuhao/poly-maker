"""Bounded signal-count calibration on shared causal public input, without PnL selection.

Only the primary manager controls the broker. Shadow managers own independent
campaigns/rounds, and cannot weaken shared AP, book-quality, M5/M6/M8 primitives.
Raw SIGNAL rows must never be summed across parameter profiles.
"""

import argparse
import asyncio
import gc
import gzip
import hashlib
import json
import resource
import sys
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path

PROFILES = {
    "control_v3": {},
    "fast250": {s: {"round_seconds": .25} for s in ("M7", "M9", "M10")},
    "hold25": {s: {"hold_seconds": .025} for s in ("M7", "M9", "M10")},
    "recovery40": {s: {"stable_recovery": ".40", **({"round_seconds": .25} if s == "M9" else {})}
                   for s in ("M7", "M9", "M10")},
    "recovery40_hold25": {s: {"stable_recovery": ".40", "hold_seconds": .025,
                              **({"round_seconds": .25} if s == "M9" else {})}
                          for s in ("M7", "M9", "M10")},
    "recovery40_hold25_pause60": {
        s: {"stable_recovery": ".40", "hold_seconds": .025,
            **({"pause_bin_fraction": ".33", "pause_total_fraction": ".60"} if s == "M7" else {}),
            **({"round_seconds": .25} if s == "M9" else {})}
        for s in ("M7", "M9", "M10")},
}


def write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str)+"\n")
    temp.replace(path)


def verify_input(member):
    with Path(member["input"]).open("rb") as raw:
        if hashlib.file_digest(raw, "sha256").hexdigest() != member["sha256"]:
            raise ValueError("FROZEN_INPUT_CHANGED")


class Fanout:
    def __init__(self, managers, primary_id="control_v3"):
        self.managers = managers
        self.primary_id = primary_id

    def release_runtime(self):
        count = sum(manager.release_runtime() for manager in self.managers.values())
        self.managers.clear()
        return count

    def __getattr__(self, name):
        if name in {"on_fact", "on_round", "on_ap_verdict", "invalidate", "tick", "publish",
                    "ask_removed", "intermediate_depth", "depth", "trade"}:
            def dispatch(*args, **kwargs):
                for manager in self.managers.values():
                    getattr(manager, name)(*args, **kwargs)
            return dispatch
        return getattr(self.managers[self.primary_id], name)


def target_map(profiles, primary_id, shadow_targets=None, profile_targets=None):
    allowed = {"M7", "M9", "M10"}
    if not isinstance(profiles, dict) or profiles.get(primary_id) != {}:
        raise ValueError("GRID_REQUIRES_UNCHANGED_CONTROL")
    if profile_targets is not None:
        if (not isinstance(profile_targets, dict) or shadow_targets is not None
                or set(profile_targets) != set(profiles)-{primary_id}):
            raise ValueError("GRID_PROFILE_TARGETS_MUST_COVER_EXACT_SHADOWS")
        selected = profile_targets
    else:
        selected = {name: shadow_targets or sorted(allowed) for name in profiles if name != primary_id}
    result = {}
    for name, targets in selected.items():
        if (not isinstance(profiles[name], dict)
                or not isinstance(targets, (list, tuple)) or not targets or len(set(targets)) != len(targets)
                or not set(targets) <= allowed or set(profiles[name])-set(targets)):
            raise ValueError("GRID_PARAMETERS_MUST_BELONG_TO_DECLARED_TARGETS")
        result[name] = sorted(targets)
    return result


def attach_profiles(context, primary, profiles=None, primary_id="control_v3", shadow_targets=None,
                    profile_targets=None):
    from maker_signals.strategies import Strategies

    profiles = PROFILES if profiles is None else profiles
    targets_by_profile = target_map(profiles, primary_id, shadow_targets, profile_targets)

    class TargetsOnly(Strategies):
        def __init__(self, *args, targets, **kwargs):
            self.targeted_strategies = frozenset(targets)
            super().__init__(*args, **kwargs)

        def start(self, strategy, token, root, deadline, anchor=None):
            if strategy not in self.targeted_strategies:
                return None
            return super().start(strategy, token, root, deadline, anchor)

        def ensure_private_m9_seed(self, token):
            if "M9" in self.targeted_strategies:
                super().ensure_private_m9_seed(token)

        def on_fact(self, fact):
            # Only public AP and SELL-exhaustion facts drive these shadows;
            # other fact kinds need not be cached by the target managers.
            if fact.kind in {"AP_BUY", "AP_SELL", "EX_SELL", "EX_SELL_M9_PRIVATE"}:
                super().on_fact(fact)

        def combine(self, token):
            pass

        def on_round(self, fact):
            if "M9" in self.targeted_strategies and fact.values["qualified"] and not self.private_m9_seed:
                self.record_m9_support(fact)

        def check_lw(self, token):
            pass

    managers = {primary_id: primary}
    primary.calibration_profile = primary_id
    for name, parameters in profiles.items():
        if name != primary_id:
            values = deepcopy(primary.frequency_parameters)
            for strategy, overrides in parameters.items():
                values[strategy].update(deepcopy(overrides))
            managers[name] = TargetsOnly(
                context, primary.outcomes, signal_variant=primary.signal_variant,
                parent_filters=False, frequency_parameters=values,
                enabled_strategies=targets_by_profile[name],
                targets=targets_by_profile[name])
            managers[name].calibration_profile = name
    context.strategies = Fanout(managers, primary_id)
    return managers


async def run(args):
    import m511_sim
    from m510_trade_through import SCENARIO_SETTINGS
    from m511_sim import ResearchOnlyEngine, definition
    from m510_sim_replay import supplement_settlement
    from maker_signals.strategies import M5_M10_NEXT

    from polymaker.m1234_sim import runner

    loaded = {"m511_sim": str(Path(m511_sim.__file__).resolve()),  # noqa: ASYNC240 -- Offline source identity.
              "runner": str(Path(runner.__file__).resolve())}  # noqa: ASYNC240 -- Offline source identity.
    if (Path(loaded["m511_sim"]) != args.extension_dir.resolve() / "m511_sim.py"
            or args.source_root.resolve() != runner.ROOT):
        raise RuntimeError("GRID_FROZEN_SOURCE_IMPORT_MISMATCH")
    profile_bytes = args.profiles.read_bytes() if args.profiles else None
    profiles = json.loads(profile_bytes) if profile_bytes is not None else PROFILES
    if profiles.get(args.control_id) != {}:
        raise ValueError("GRID_REQUIRES_UNCHANGED_CONTROL")
    target_file = getattr(args, "profile_targets", None)
    target_bytes = target_file.read_bytes() if target_file else None
    per_profile = json.loads(target_bytes) if target_bytes is not None else None
    resolved_targets = target_map(profiles, args.control_id, args.shadow_targets, per_profile)
    frequency_file = getattr(args, "frequency_parameters", None)
    baseline_parameters = json.loads(frequency_file.read_text()) if frequency_file else None
    groups = getattr(args, "strategies", None)
    configuration = {}
    if baseline_parameters is not None:
        configuration["frequency_parameters"] = baseline_parameters
    if groups is not None:
        configuration["enabled_strategies"] = groups
    for overrides in profiles.values():
        definition(args.signal_variant or M5_M10_NEXT, only_m5_m10=True,
                   frequency_parameters=overrides)
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output / "import-proof.json", {"status": "MATCH", "loaded_modules": loaded,
          "expected_source_root": str(args.source_root.resolve()),
          "expected_extension": str(args.extension_dir.resolve())})
    members = json.loads(args.manifest.read_text())["markets"]
    implementation = {str(p.relative_to(args.source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (args.source_root / "src/polymaker").rglob("*.py")}
    run_id = hashlib.sha256((str(time.time_ns())+str(args.output)).encode()).hexdigest()
    write(args.output / "plan.json", {
        "run_identity": run_id,
        "mode": "PARAMETER_GRID_ONLY", "real_orders": 0, "source_manifest": str(args.manifest),
        "profiles": profiles, "selection": "signal count and evidence completeness only; PnL not consulted",
        "primary_manager": args.control_id,
        "source_manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "signal_variant": args.signal_variant or M5_M10_NEXT,
        "shadow_managers": "M7/M9/M10 only; independent campaigns",
        "shadow_targets": args.shadow_targets or ["M7", "M9", "M10"],
        "profile_targets": resolved_targets,
        "profiles_input_sha256": hashlib.sha256(profile_bytes).hexdigest() if profile_bytes else None,
        "profile_targets_input_sha256": hashlib.sha256(target_bytes).hexdigest() if target_bytes else None,
        "baseline_frequency_parameters": baseline_parameters,
        "enabled_strategies": groups,
    })
    completed = []
    for member in members:
        verify_input(member)
        engine = ResearchOnlyEngine(args.output / member["market"],
                                    signal_variant=args.signal_variant or M5_M10_NEXT, **configuration)
        engine.store.run_owner = run_id
        with gzip.open(member["input"], "rt") as stream:
            managers = None
            for line in stream:
                event = json.loads(line)
                if event["kind"] == "INIT":
                    cfg = event["definition"]["settings"]
                    cfg.update(implementation=implementation,
                               m5_m11=definition(args.signal_variant or M5_M10_NEXT, only_m5_m10=True,
                                                **configuration),
                               scenarios=SCENARIO_SETTINGS, parameter_grid=profiles,
                               parameter_profile_targets=resolved_targets)
                await engine.ingest(event)
                if event["kind"] == "INIT":
                    ext = engine.research_extension
                    context = ext.research
                    primary = ext.research_signals
                    managers = attach_profiles(context, primary, profiles, args.control_id,
                                               args.shadow_targets, per_profile)
        if getattr(args, "allow_incomplete", False) and engine.coverage["complete_window"] != member["complete_window"]:
            raise ValueError("FROZEN_WINDOW_COVERAGE_CHANGED:"+member["market"])
        if engine.ended and not engine.resolution and member.get("official_result"):
            await supplement_settlement(engine, member)
        if (not engine.ended or not engine.resolution or
                not engine.coverage["complete_window"] and not getattr(args, "allow_incomplete", False)):
            raise ValueError("COMPLETE_SETTLED_GRID_INPUT_REQUIRED")
        context = engine.research_extension.research
        results = {}
        for name, manager in managers.items():
            signals = [context.registry.facts[i] for i in manager.published]
            results[name] = {
                "parameters": manager.frequency_parameters,
                "eligible_strategies": sorted(getattr(manager, "targeted_strategies", None)
                                                or manager.enabled_strategies
                                                or ("M5", "M6", "M7", "M8", "M9", "M10")),
                "counts": dict(Counter(f.values["strategy"] for f in signals)),
                "signals": [{"id": f.id, "strategy": f.values["strategy"], "token": f.token,
                             "confirmed": f.confirmed, "deadline": f.deadline,
                             "anchor_price": str(f.anchor)} for f in signals],
            }
        await engine.close()
        engine.retire_runtime()
        engine.brokers.clear()
        engine.last_books = engine.tick_verification = None
        engine.marks.clear()
        engine.mark_targets.clear()
        # Fanout and manager locals also reference the shared registry.
        del context, managers, primary, ext, manager, signals
        gc.collect()
        row = {"market": member["market"], "profiles": results,
               "complete_window": engine.coverage["complete_window"],
               "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
        completed.append(row)
        write(args.output / "progress.json", {"markets": completed})
        print(json.dumps({"market": member["market"], "counts": {k:v["counts"] for k,v in results.items()}}), flush=True)
        if row["peak_rss_mib"] > 512:
            raise RuntimeError("GRID_MEMORY_CAPACITY_EXHAUSTED")
    write(args.output / "completion.json", {"status": "PASS", "markets": len(completed),
          "run_identity": run_id,
          "complete_windows": sum(r['complete_window'] for r in completed),
          "incomplete_markets": [r['market'] for r in completed if not r['complete_window']],
          "scope": "signal frequency calibration only; final chosen profile requires complete economic replay"})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--extension-dir", type=Path, required=True)
    p.add_argument("--profiles", type=Path)
    p.add_argument("--control-id", default="control_v3")
    p.add_argument("--signal-variant")
    p.add_argument("--shadow-targets", nargs="+", choices=("M7", "M9", "M10"))
    p.add_argument("--profile-targets", type=Path,
                   help="JSON mapping of each shadow profile to its evaluated target strategies")
    p.add_argument("--frequency-parameters", type=Path,
                   help="Actual baseline parameters, inherited before per-profile target overrides")
    p.add_argument("--strategies", nargs="+", choices=("M5", "M6", "M7", "M8", "M9", "M10"))
    p.add_argument("--allow-incomplete", action="store_true")
    args = p.parse_args()
    args.source_root = args.source_root.resolve()
    sys.path[:0] = [str(args.extension_dir.resolve()), str(args.source_root / "src")]
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
