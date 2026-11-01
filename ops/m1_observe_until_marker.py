"""Run independent read-only M1 market sessions until an actual WOULD_POST.

Each child uses the public-data observer, never the trading CLI/account lock.
Config and results are archived per session. Ctrl-C stops only this observer.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path


def observation_config(path):
    raw = path.read_bytes()
    cfg = tomllib.loads(raw.decode())
    strategy = cfg.get("rdp_sm", {})
    if strategy.get("normal_entry_mode") != "m1_v1" or strategy.get("m1", {}).get("observation_only") is not True:
        raise ValueError("Runner requires explicit M1 observation_only = true")
    return raw


def verify_markers(result):
    session = Path(result["session_dir"]).resolve()
    root = Path("research_outputs/m1_isolated").resolve()
    if root not in session.parents:
        raise ValueError("Marker audit requires an isolated M1 session directory")
    verifier = Path(__file__).resolve().parents[1]/"docs/research/tools/verify_m1_marker.py"
    checked = subprocess.run([sys.executable, str(verifier), str(session)],
                             capture_output=True, text=True, timeout=30)
    print(checked.stdout.rstrip(), flush=True)
    if checked.returncode:
        print("M1 marker failed independent audit; retained as rejected evidence.", flush=True)
    return checked.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config-m1"))
    parser.add_argument("--output-dir", type=Path, default=Path("research_outputs/m1_isolated/runner"))
    parser.add_argument("--continue-after-marker", action="store_true")
    parser.add_argument("--sessions", type=int, default=0, help="0 runs until stopped")
    args = parser.parse_args()
    root = Path("research_outputs/m1_isolated").resolve()
    if args.output_dir.resolve() != root and root not in args.output_dir.resolve().parents:
        parser.error("--output-dir must be under research_outputs/m1_isolated")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for index in range(1, 1000000):
        raw = observation_config(args.config_dir / "config.toml")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        prefix = args.output_dir / f"{index:04}-{stamp}"
        prefix.with_suffix(".toml").write_bytes(raw)
        stdout_path, stderr_path = prefix.with_suffix(".result.json"), prefix.with_suffix(".console.log")
        print(f"M1 observation session {index}: {prefix.name}", flush=True)
        child = None
        try:
            with stdout_path.open("x") as out, stderr_path.open("x") as err:
                child = subprocess.Popen(
                    [sys.executable, "-m", "polymaker.rdp_sm_m1_observe",
                     "--config-dir", str(args.config_dir)],
                    stdout=out, stderr=err, start_new_session=True)
                offset = 0
                while child.poll() is None:
                    time.sleep(.5)
                    with stderr_path.open() as display:
                        display.seek(offset)
                        for line in display:
                            if line.startswith(("M1 Heartbeat |", "M1 WOULD_POST")):
                                print(line.rstrip(), flush=True)
                        offset = display.tell()
                code = child.wait()
        except KeyboardInterrupt:
            if child is not None and child.poll() is None:
                os.killpg(child.pid, signal.SIGINT)
                child.wait(timeout=30)
            print("Observation runner stopped; child given normal close/cleanup.", flush=True)
            return 130
        try:
            result = json.loads(stdout_path.read_text())
        except (ValueError, OSError):
            result = {}
        counts = (result.get("m1_observation") or {}).get("counts", {})
        markers = counts.get("WOULD_POST", 0)
        print(f"Session result: exit={code} status={result.get('execution_status')} "
              f"reason={result.get('stop_reason')} AP_SELL={counts.get('AP_CONFIRMED_SELL', 0)} "
              f"RF={counts.get('RF_CONFIRMED', 0)} EX={counts.get('EX_CONFIRMED', 0)} "
              f"WOULD_POST={markers}", flush=True)
        if not code and markers and verify_markers(result) and not args.continue_after_marker:
            print(f"PUBLIC_SIGNAL_WOULD_POST_RECORDED (account not checked): {stdout_path}", flush=True)
            return 0
        if args.sessions and index >= args.sessions:
            return 0 if not code else 1
        if "BUDGET_REACHED" in str(result.get("stop_reason")):
            print("M1 observation output budget reached; runner stopped.", flush=True)
            return 1
        failures = failures+1 if code else 0
        if failures >= 3:
            raise RuntimeError(f"Three failed observation sessions; inspect {stderr_path}")
        if code:
            time.sleep(5)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
