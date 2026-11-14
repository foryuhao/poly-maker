#!/usr/bin/env python3
"""Run/check/replay the M234 primary mode using an explicit frozen base source."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("check", "run", "replay"), default="run")
    parser.add_argument("--config")
    parser.add_argument("--contract")
    parser.add_argument("--output")
    parser.add_argument("--input")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve() / "src"))
    from m234_sim_main import experiment_definition
    from m234_sim_main_storage import ForwardEngine, storage_definition

    from polymaker.m2_config import M2Config
    from polymaker.m1234_sim import runner

    if args.source_root.resolve() != runner.ROOT:
        raise RuntimeError("FROZEN_SOURCE_IMPORT_MISMATCH")
    runner.Engine = ForwardEngine
    if args.mode == "replay":
        result = asyncio.run(runner.replay(args.input, args.output))
    else:
        check = runner.preflight(args.config, args.contract)
        cfg = runner.settings(args.config, args.contract)
        policy = M2Config.model_validate(cfg["m234"])
        if policy.risk_usdc != 5 or policy.cutoff_sec != 240:
            raise ValueError("MAIN_REQUIRES_5_USDC_AND_T240")
        cfg["m234_execution_main"] = experiment_definition()
        cfg["audit_storage"] = storage_definition()
        cfg["execution_wrapper_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if args.mode == "check":
            result = {**check, "m234_execution_main": cfg["m234_execution_main"],
                      "audit_storage": cfg["audit_storage"],
                      "execution_wrapper_sha256": cfg["execution_wrapper_sha256"]}
        else:
            if check["status"] != "PASS":
                raise RuntimeError("MAIN_PREFLIGHT_FAILED:" + json.dumps(check))
            result = asyncio.run(runner.LiveRun(args.output, cfg).run())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
    if result.get("status") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
