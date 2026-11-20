"""One selected SOP baseline; reuse the bounded public capture/ledger engine."""
import argparse
import asyncio
import json
from pathlib import Path

from m1223_baseline_contract import (
    atomic_json,
    sha256,
    validate_cohort,
    validate_plan,
    verify_runtime,
)


def setup(runtime, strategy):
    from m1223_sim_forward import configure

    from polymaker.m1234_sim import runner

    runtime = Path(runtime).resolve()
    fingerprint = verify_runtime(runtime)
    if runtime != runner.ROOT:
        raise ValueError("BASELINE_FROZEN_IMPORT_REQUIRED")
    plan = validate_plan(json.loads((runtime / "config/m1223-baseline-plan.json").read_text()))
    check, cfg = configure(runner, runtime / "config/m1223-24h.toml",
                           runtime / "config/m1234-sim-contract.json",
                           parameter_file=runtime / f"config/m1223-parameters-{strategy}.json")
    rule = cfg["sop_execution"]
    if (rule.get("quantity_step") != "0.1" or rule.get("session_budget_usdc") != "10"
            or rule.get("price_filter") is not None or rule.get("taker_fallback") is not False
            or cfg["sop"]["variant_id"] != plan["variants"][strategy]
            or cfg["max_markets"] != plan["markets_per_strategy"]
            or rule["maximum_submissions_per_market_token_version"] != 1):
        raise ValueError("BASELINE_EXECUTION_CONTRACT_CHANGED")
    cfg["sop_baseline"] = {"plan": plan, "runtime_sha256": fingerprint,
                            "plan_sha256": sha256(runtime / "config/m1223-baseline-plan.json")}
    check.update(strategy=strategy, variant_id=plan["variants"][strategy],
                 quantity_step="0.1", session_budget_usdc="10", frequency_gate="NOT_APPLIED",
                 runtime_sha256=fingerprint)
    return runner, check, cfg, plan, fingerprint


def run_class(runner, epochs):
    from m1223_sim_forward import startup_public_clock

    class FixedBaseline(runner.LiveRun):
        async def run(self):
            public_clock = self.api.clock
            checked = False

            async def clock():
                nonlocal checked
                value = (await startup_public_clock(public_clock, first_epoch=epochs[0], log=self.log)
                         if not checked else await public_clock())
                if not checked:
                    first = int(value[0] // 300) * 300
                    if value[0] - first >= 5:
                        first += 300
                    if first != epochs[0]:
                        raise ValueError("BASELINE_START_MISSED_NO_COHORT_RESELECTION")
                    checked = True
                return value

            self.api.clock = clock
            return await super().run()

        async def market(self, meta):
            index = len(self.engines)
            if index >= len(epochs) or meta.slug != f"btc-updown-5m-{epochs[index]}":
                raise ValueError("BASELINE_MARKET_OUTSIDE_FIXED_COHORT")
            return await super().market(meta)

    return FixedBaseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--strategy", choices=("M23", "M12"), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    runner, check, cfg, plan, fingerprint = setup(args.runtime, args.strategy)
    if args.check:
        print(json.dumps(check, ensure_ascii=False))
        return
    if check["status"] != "PASS":
        raise ValueError("BASELINE_PREFLIGHT_FAILED:" + json.dumps(check))
    if args.output is None or args.cohort is None:
        raise ValueError("BASELINE_OUTPUT_AND_COHORT_REQUIRED")
    output = args.output.resolve()
    # A distinct research root; never reuse M6 data or any live directory.
    if output.parent.parent.name != "m1223_baseline" or output.parent.name != "runs":
        raise ValueError("BASELINE_OUTPUT_NAMESPACE_REQUIRED")
    runner.OUTPUT = output.parent
    member = validate_cohort(json.loads(args.cohort.read_text()), args.strategy, plan, fingerprint)
    cfg["sop_cohort"] = member
    cfg["sop_cohort_sha256"] = sha256(args.cohort)
    result = asyncio.run(run_class(runner, member["market_epochs"])(output, cfg).run())
    atomic_json(output / "baseline-completion.json", result)
    print(json.dumps({k: result.get(k) for k in ("status", "reason", "markets", "complete_windows")}))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
