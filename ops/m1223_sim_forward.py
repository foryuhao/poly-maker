"""M12 single-SOP forward simulation over a predeclared contiguous cohort."""

import argparse
import asyncio
import hashlib
import json
import time
from functools import partial
from pathlib import Path


async def startup_public_clock(public_clock, *, first_epoch, log, wall_now=time.time,
                               sleep=asyncio.sleep):
    """Retry transport failures only before collection, without moving the cohort.

    Each underlying request retains its original5s timeout. Never manufacture
    a public timestamp from local time; that clock only refuses late retries.
    Successful reads still pass the original fixed-cohort identity check.
    Runtime refreshes, malformed clocks and cancellation do not use this retry.
    """
    import httpx

    for attempt in range(1, 4):
        try:
            return await public_clock()
        except httpx.TransportError as exc:
            log("SOP_STARTUP_CLOCK_TRANSPORT_FAILURE", attempt=attempt, maximum_attempts=3,
                error_type=type(exc).__name__, fixed_first_epoch=first_epoch)
            if attempt == 3 or wall_now() + 20 >= first_epoch:
                raise
            await sleep(.25 * attempt)


def execution_configuration():
    path = Path(__file__).resolve().parents[1] / "config/m1223-execution.json"
    return {
        **json.loads(path.read_text()),
        "configuration_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def configure(runner, config, contract, *, parameter_file=None):
    import m1223_capture_diagnostics
    import m1223_capture_recording
    from m1223_report import install_report_compatibility
    from m1223_sim import SopEngine, definition
    from sop_signals.parameters import load_revision

    install_report_compatibility()

    check = runner.preflight(config, contract)
    cfg = runner.settings(config, contract)
    rule = execution_configuration()
    parameter_file = (Path(parameter_file) if parameter_file is not None else
                      Path(__file__).resolve().parents[1] / "config/m1223-parameters.json")
    parameter_document = (
        json.loads(parameter_file.read_text())
        if parameter_file.exists()
        else {
            "strategy": "M12",
            "revision_round": 0,
            "hypothesis": "ORIGINAL_SOP_BASELINE",
            "pnl_used_for_selection": False,
            "parameters": {},
        }
    )
    parameters, revision = load_revision(parameter_document)
    if (
        rule["budget_usdc"] != "5"
        or rule["execution_mode"] != "MAKER"
        or rule["legacy_400ms_admission"]
        or rule["legacy_t240_only_order_lifetime"]
    ):
        raise ValueError("SOP_EXECUTION_SELECTION_CHANGED")
    expected = {
        "trade-through": {
            "post_ms": rule["post_ms"],
            "ack_ms": rule["ack_ms"],
            "cancel_ms": rule["cancel_ms"],
            "ahead_cancel_fraction": "0",
        }
    }
    if cfg["scenarios"] != expected:
        raise ValueError("SOP_MODEL_LATENCY_CHANGED")
    cfg.update(
        profiles={},
        sop=definition(parameters),
        sop_parameter_revision=revision,
        sop_execution=rule,
        initial_tick_read_attempts=3,
        execution_wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        capture_diagnostics={
            "version": m1223_capture_diagnostics.VERSION,
            "scope": "TRANSPORT_PROVENANCE_ONLY_NOT_SIGNAL_INPUT",
            "source_sha256": hashlib.sha256(
                Path(m1223_capture_diagnostics.__file__).read_bytes()
            ).hexdigest(),
        },
        sop_recording=m1223_capture_recording.recording_contract(cfg["writer_queue_records"]),
    )
    if cfg["sop"]["strategy"] == "M16":
        from polymaker.rdp_sm_marketdata import SessionFeed
        from polymaker.m1234_sim.events import encode
        from sop_signals.m16_clock import capture_clock_evidence, check_clock_evidence

        cfg["sop_receiver_clock"] = capture_clock_evidence(
            receiver_class=SessionFeed, capture_class=runner.CaptureFeed, encoder=encode)
        if check_clock_evidence(cfg["sop_receiver_clock"])["status"] != "LOCAL_QUANTIZATION_PROVEN":
            raise ValueError("M16_CAPTURE_RECEIVER_CLOCK_NOT_PROVEN")
        check["sop_receiver_clock"] = cfg["sop_receiver_clock"]
    runner.CaptureFeed = m1223_capture_diagnostics.with_transport_diagnostics(runner.CaptureFeed)
    runner.Recorder = m1223_capture_recording.Recorder
    runner.Engine = partial(SopEngine, parameters=parameters)
    runner.OUTPUT = runner.ROOT / "research_outputs/m1223_sim"
    check.update(profiles=cfg["sop"]["groups"], scenarios=["sop/trade-through", "sop/assumed"], sop=cfg["sop"])
    return check, cfg


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cohort", type=Path, required=True)
    p.add_argument("--selection", type=Path)
    p.add_argument("--independent-report", type=Path)
    p.add_argument("--user-direct-admission", type=Path)
    p.add_argument("--mode", choices=("check", "run"), default="check")
    args = p.parse_args()
    import sys

    sys.path.insert(0, str(args.source_root.resolve() / "src"))
    research = Path(__file__).resolve().parents[1] / "research"
    if research.exists():
        sys.path.insert(0, str(research))
    from polymaker.m1234_sim import runner

    if args.source_root.resolve() != runner.ROOT:
        raise ValueError("SOP_FROZEN_SOURCE_IMPORT_MISMATCH")
    if args.mode == "run":
        manifest = args.source_root / "frozen-manifest.json"
        if not manifest.is_file():
            raise ValueError("SOP_RUN_REQUIRES_FROZEN_SOURCE_MANIFEST")
        for name, expected in json.loads(manifest.read_text()).items():
            source = (args.source_root / name).resolve()
            if (
                not source.is_relative_to(args.source_root.resolve())
                or hashlib.sha256(source.read_bytes()).hexdigest() != expected
            ):
                raise ValueError("SOP_FROZEN_SOURCE_CHANGED:" + name)
    check, cfg = configure(runner, args.config, args.contract)
    cohort = json.loads(args.cohort.read_text())
    epochs = cohort["market_epochs"]
    from m1223_gates import phase_admission

    gate = phase_admission(
        cohort,
        cfg,
        selection=json.loads(args.selection.read_text()) if args.selection else None,
        independent_report=json.loads(args.independent_report.read_text())
        if args.independent_report
        else None,
        user_direct_document=json.loads(args.user_direct_admission.read_text())
        if args.user_direct_admission else None,
    )
    cfg["sop_phase_admission"] = gate
    check["sop_phase_admission"] = gate
    cfg["sop_cohort"] = cohort
    cfg["sop_cohort_sha256"] = hashlib.sha256(args.cohort.read_bytes()).hexdigest()
    for name, path in (
        ("selection", args.selection),
        ("independent_report", args.independent_report),
        ("user_direct_admission", args.user_direct_admission),
    ):
        if path:
            cfg["sop_" + name + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if args.mode == "check":
        print(json.dumps(check, ensure_ascii=False, indent=2))
        return
    if check["status"] != "PASS":
        raise ValueError("SOP_PREFLIGHT_FAILED:" + json.dumps(check))

    class FixedCohortRun(runner.LiveRun):
        def capacity(self):
            previous = self.last_resource
            super().capacity()
            if self.last_resource != previous:
                rows = {name: recorder.diagnostic_snapshot()
                        for name, recorder in self.recorders.items()
                        if recorder.thread.is_alive() and hasattr(recorder, "diagnostic_snapshot")}
                self.log("SOP_RECORDER_PROGRESS", recordings=rows,
                         scope="LOCAL_WRITER_TIMING_ONLY_NOT_SIGNAL_INPUT")

        async def run(self):
            public_clock = self.api.clock
            checked = False

            async def clock():
                nonlocal checked
                result = (await startup_public_clock(public_clock, first_epoch=epochs[0], log=self.log)
                          if not checked else await public_clock())
                if not checked:
                    server = result[0]
                    first = int(server // 300) * 300
                    if server - first >= 5:
                        first += 300
                    if first != epochs[0]:
                        raise ValueError("SOP_COHORT_START_MISMATCH_NO_RESELECTION")
                    checked = True
                return result

            self.api.clock = clock
            return await super().run()

        async def market(self, meta):
            expected = epochs[len(self.engines)] if len(self.engines) < len(epochs) else None
            if meta.slug != f"btc-updown-5m-{expected}":
                raise ValueError("SOP_MARKET_OUTSIDE_PREDECLARED_COHORT")
            return await super().market(meta)

    result = asyncio.run(FixedCohortRun(args.output, cfg).run())
    print(
        json.dumps(
            {k: result.get(k) for k in ("status", "reason", "markets", "complete_windows")},
            ensure_ascii=False,
        )
    )
    if result.get("status") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
