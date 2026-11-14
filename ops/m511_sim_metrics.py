"""Committed metrics for retained M1–M4 and separate M5–M11 accounts."""

import argparse
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

from m511_sample_quality import read_gate
from m511_sim import GROUPS, M5_M10_GROUPS, M5_M10_REVISION, M5_M10_THROUGH_REVISION, selected_groups

spec = importlib.util.spec_from_file_location(
    "m511_counts_implementation", Path(__file__).with_name("m1234_sim_metrics.py")
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

base.STRATEGIES = ("M1", "M2", "M3", "M4", *GROUPS)
base.EVENTS.update({s: "CANDIDATE" for s in GROUPS})


def collect(run, cache):
    result = base.collect(run, cache)
    plan = json.loads((Path(run) / "plan.json").read_text())
    research_only = plan.get("settings", {}).get("m5_m11", {}).get("revision") in (
        M5_M10_REVISION, M5_M10_THROUGH_REVISION)
    research_groups = (selected_groups(plan.get("settings", {}).get("m5_m11", {}).get("groups"))
                       if research_only else GROUPS)
    if plan.get("settings", {}).get("m5_m11", {}).get("revision") == M5_M10_THROUGH_REVISION:
        result["definitions"]["submitted_orders"] = "SUBMIT in the single research/trade-through account"
        result["definitions"]["unresolved_orders"] = "missing evidence may leave additional fills unknown; positive known fills retained"
        result["fill_model"] = plan["settings"]["m5_m11"]["fill_model"]
    cached = json.loads(Path(cache).read_text())
    excluded = []
    counted = {s["market"] for s in result["sources"] if not s["invalid"]}
    for name, state in cached["markets"].items():
        if name not in counted:
            continue
        try:
            gate = read_gate(Path(run) / name / "simulation.sqlite3")
        except Exception as error:
            gate = {"status": "UNAVAILABLE", "reason": str(error)}
        if gate["status"] == "ELIGIBLE":
            continue
        excluded.append({"market": name, **gate})
        for strategy in research_groups:
            result["strategies"][strategy]["signals"] -= len(state["signals"][strategy])
        for order in state["orders"].values():
            s = order["strategy"]
            if s not in research_groups:
                continue
            group = "/".join((s, order["profile"], order["scenario"]))
            subtract = {
                "submitted_orders": 1,
                "filled_orders": int(order["fill_events"] > 0),
                "unresolved_orders": int(order["unresolved"]),
                "fill_events": order["fill_events"],
                "filled_shares": order["filled_shares"],
            }
            for target in (result["strategies"][s], result["by_profile_scenario"][group]):
                for field, value in subtract.items():
                    target[field] = (
                        str(Decimal(target[field]) - Decimal(value))
                        if field == "filled_shares"
                        else target[field] - value
                    )
    result["excluded_research_samples"] = excluded
    result["research_scope"] = "EXCLUDES_KNOWN_LOCAL_M4_SELF_CONTAMINATED_AND_UNREVIEWED_MARKETS"
    if excluded:
        result["status"] = "PARTIAL"
    modes = {}
    for mode in ("research",) if research_only else ("main", "baseline", "research"):
        strategies = research_groups if mode == "research" else ("M1", "M2", "M3", "M4")
        totals = {
            s: {**base.empty_counts(), "signals": result["strategies"][s]["signals"]}
            for s in strategies
        }
        profiles = {}
        for k, v in result["by_profile_scenario"].items():
            s, profile, scenario = k.split("/", 2)
            if s not in strategies:
                continue
            chosen = (
                scenario.startswith("research/")
                if mode == "research"
                else "/" not in scenario
                if mode == "baseline"
                else s == "M1" or scenario.startswith("main/")
            )
            if not chosen:
                continue
            profiles[k] = v
            for field, value in v.items():
                totals[s][field] = (
                    str(Decimal(totals[s][field]) + Decimal(value))
                    if field == "filled_shares"
                    else totals[s][field] + value
                )
        modes[mode] = {"strategies": totals, "by_profile_scenario": profiles}
    return {k: v for k, v in result.items() if k not in ("strategies", "by_profile_scenario")} | {
        "modes": modes,
        "primary_mode": "research" if research_only else "main + research",
        "independent_accounts": "Do not add profiles, scenarios, filters or controls as a portfolio.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--cache", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(collect(a.run, a.cache), ensure_ascii=False, indent=2))
