#!/usr/bin/env python3
"""Public-data forward run with research signals and T240 execution."""

import argparse
import asyncio
import hashlib
import json
import sys
from functools import partial
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--mode", choices=("check", "run"), default="run")
    p.add_argument("--scope", choices=("additive", "m5-m10", "m5-m6-m9-m10"),
                   default="m5-m6-m9-m10")
    p.add_argument("--signal-variant")
    p.add_argument("--frequency-parameters", type=Path)
    for name in ("config", "contract", "output"):
        p.add_argument("--" + name, required=True)
    args = p.parse_args()
    # Historical offline replays retain their explicit scopes. New forward
    # collection follows the user's four-strategy retirement decision.
    if args.mode == 'run' and args.scope != 'm5-m6-m9-m10':
        raise ValueError('NEXT_TEST_SCOPE_REQUIRES_M5_M6_M9_M10')
    sys.path.insert(0, str(args.source_root.resolve() / "src"))
    # Local development and frozen extension layouts both contain maker_signals.
    research = Path(__file__).resolve().parents[1] / "research"
    if research.is_dir():
        sys.path.insert(0, str(research))
    from m234_sim_main import experiment_definition
    from m234_sim_main_storage import storage_definition
    from m511_sim import NEXT_RESEARCH_GROUPS, ResearchEngine, ResearchOnlyEngine, definition
    from maker_signals.strategies import M5_M10_NEXT

    from polymaker.m1234_sim import runner

    if args.source_root.resolve() != runner.ROOT:
        raise RuntimeError("FROZEN_SOURCE_IMPORT_MISMATCH")
    research_only = args.scope in ("m5-m10", "m5-m6-m9-m10")
    if not research_only and (args.signal_variant or args.frequency_parameters):
        raise ValueError("FREQUENCY_CONFIGURATION_REQUIRES_M5_M10_SCOPE")
    frequency_values = (json.loads(args.frequency_parameters.read_text())
                        if args.frequency_parameters else None)
    frequency_kwargs = {"frequency_parameters": frequency_values} if args.frequency_parameters else {}
    if args.scope == "m5-m6-m9-m10":
        frequency_kwargs["enabled_strategies"] = NEXT_RESEARCH_GROUPS
    signal_variant = (args.signal_variant or M5_M10_NEXT) if research_only else None
    research_definition = definition(signal_variant, only_m5_m10=research_only, **frequency_kwargs)
    if research_only:
        from polymaker.m1234_sim.broker import SimBroker
        if (not hasattr(SimBroker, "join_live_order")
                or "retire_runtime" not in runner.LiveRun.retire_market.__code__.co_consts):
            raise RuntimeError("M510_REQUIRES_NEW_FROZEN_BASE_WITH_RETIREMENT_AND_FILL_HOOKS")
    runner.Engine = (partial(ResearchOnlyEngine, signal_variant=signal_variant, **frequency_kwargs)
                     if research_only else ResearchEngine)
    if research_only:
        runner.OUTPUT = runner.ROOT / "research_outputs/m511_sim"
    check = runner.preflight(args.config, args.contract)
    cfg = runner.settings(args.config, args.contract)
    cfg.update(
        m5_m11=research_definition,
        audit_storage=storage_definition(),
        execution_wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    if research_only:
        from m510_trade_through import SCENARIO_SETTINGS
        cfg["scenarios"] = SCENARIO_SETTINGS
        cfg["profiles"] = {}
        cfg["initial_tick_read_attempts"] = 3
        check["profiles"] = cfg["m5_m11"]["groups"]
        check["scenarios"] = ["research/" + name for name in cfg["scenarios"]]
    else:
        cfg["m234_execution_main"] = experiment_definition()
    if args.mode == "check":
        result = {**check, "m5_m11": cfg["m5_m11"]}
    else:
        if check["status"] != "PASS":
            raise RuntimeError("PREFLIGHT_FAILED:" + json.dumps(check))
        result = asyncio.run(runner.LiveRun(args.output, cfg).run())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
    if result.get("status") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
