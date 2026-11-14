"""Predeclare local-AP profiles from completed public-AP count evidence only."""

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from m510_final_selection import m7_pause_is_exhaustion, select


def write_immutable(path, value):
    text = json.dumps(value, indent=2)+"\n"
    if path.exists() and path.read_text() != text:
        raise ValueError("EXISTING_LOCAL_AP_PLAN_MUST_NOT_BE_OVERWRITTEN:"+path.name)
    path.write_text(text)


def profiles_from(baseline, control):
    needed = [s for s in ("M7", "M10") if baseline["expected_counts"][s] < 10]
    profiles, targets, priority = {"control_v7": {}}, {}, {s: [] for s in needed}
    options = {}
    for strategy in needed:
        chosen = deepcopy(baseline["frequency_parameters"][strategy])
        if strategy == "M7":
            choices = [("control", deepcopy(control[strategy])), ("selected", chosen)]
        else:
            choices = [("selected", chosen), ("fast", {**chosen, "round_seconds": .25,
                                                       "hold_seconds": .01})]
        unique = []
        for label, parameters in choices:
            parameters.pop("ap_window_seconds", None)
            if not any(parameters == p for _, p in unique):
                unique.append((label, parameters))
        options[strategy] = unique
    for duration, tag in ((.5, "500"), (.375, "375"), (.25, "250")):
        for strategy in needed:
            for label, original in options[strategy]:
                decays = (".99", ".999") if strategy == "M10" else (None,)
                for decay in decays:
                    parameters = {**original, "ap_window_seconds": duration}
                    if decay:
                        parameters["decay_fraction"] = decay
                    if strategy == "M7" and not m7_pause_is_exhaustion(parameters):
                        raise ValueError("M7_PAUSE_MUST_BE_WEAKER_THAN_ACTUAL_AP_RATE")
                    name = strategy.lower()+"_ap"+tag+"_"+label+("_d"+decay[1:] if decay else "")
                    profiles[name] = {strategy: parameters}
                    targets[name] = [strategy]
                    priority[strategy].append(name)
    return profiles, targets, priority


def build(root):
    baseline = select(root, emit=False, include_local=False)
    control = json.loads((root / "frequency-grid-phase5/progress.json").read_text())["markets"][0]["profiles"]["control_v6"]["parameters"]
    profiles, targets, priority = profiles_from(baseline, control)
    if not priority:
        return {"status": "NO_LOCAL_AP_EXPERIMENT_REQUIRED", "public_counts": baseline["expected_counts"]}
    baseline_path = root / "pre-local-selection-v6.json"
    write_immutable(baseline_path, baseline)
    write_immutable(root / "phase6-local-ap-profiles.json", profiles)
    write_immutable(root / "phase6-local-ap-targets.json", targets)
    rule = {"status": "FIXED_BEFORE_ANY_LOCAL_AP_REAL_REPLAY",
            "cohort_sha256": baseline["cohort_sha256"],
            "signal_variant": "m5-m10-local-ap-calibration-v7", "primary": "control_v7",
            "profiles": profiles, "profile_targets": targets, "priority_by_strategy": priority,
            "baseline_selection_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "selection_uses_pnl": False,
            "selection": "Prefer passing existing public-AP profiles; otherwise first predeclared local profile>=10. Longer AP window first, original/control RF before alternative at equal AP duration, strict .99 before .999. If none passes, highest-count/earlier-priority result is review-only, never a target pass.",
            "invariants": [
                "Only initially missing M7/M10 targets are evaluated in shadows; M9 independent result stays separate.",
                "Shared AP/EX, source quality and M5/M6/M8 thresholds unchanged; primary all6 must match independent completed v4 control.",
                "AP500/375/250ms retains fixed minimum volume,65% directional share,2+prints,2+of3 bins includinglast, singleprint<=95%, frozeninitialdepth, fullcoverage/watermark/50ms backlog guards.",
                "M7 actual support/pause/freshAP2 sequence, weaker pause BUY rate, and M10 two actual strict absolute/normalized decreasing returns remain. No failed-round skipping.",
                "New AP duration is explicit temporal calibration, not old signal-set identity equivalence or a profit claim.",
                "Full selected economic replay, exact same-config retirement invariance and actual288 final-resource replay still required."],
            "services_or_timers_created": False, "running_frozen_sources_modified": False}
    write_immutable(root / "phase6-local-ap-rule.json", rule)
    record = {"status": rule["status"], "at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
              "profiles": len(profiles), "targets": list(priority),
              "public_counts": baseline["expected_counts"], "new_replay_started": False}
    (root / "phase6-plan-build.json").write_text(json.dumps(record, indent=2)+"\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.directory.resolve()), indent=2))
