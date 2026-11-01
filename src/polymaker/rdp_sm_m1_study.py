"""Bounded public-only calibration followed by 50 fixed-parameter paper markets."""

import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from polymaker.dashboard.settlement import fetch_resolution
from polymaker.rdp_sm_ca import plain
from polymaker.rdp_sm_m1_observe import (
    OUTPUT_ROOT,
    PublicClob,
    load_settings,
    lower_priority,
    observe,
    observer_lock,
)
from polymaker.rdp_sm_m1_paper import MODEL, settle_paper

CODE_FILES = ("rdp_sm_m1_signal.py", "rdp_sm_m1_observe.py", "rdp_sm_m1_writer.py", "rdp_sm_m1_paper.py", "rdp_sm_m1_study.py")
GRID = (
    {"profile": "cal-16", "round_window_sec": .5, "ap_window_sec": .75, "rf_rounds": 2},
    {"profile": "cal-17-study", "round_window_sec": .5, "ap_window_sec": 1.5, "rf_rounds": 2},
    {"profile": "cal-18-study", "round_window_sec": .25, "ap_window_sec": .75, "rf_rounds": 2},
    {"profile": "cal-19-study", "round_window_sec": .5, "ap_window_sec": .75, "rf_rounds": 3},
    {"profile": "cal-20-study", "round_window_sec": .75, "ap_window_sec": .75, "rf_rounds": 2},
)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2))
    os.replace(temporary, path)


def code_hashes():
    directory = Path(__file__).parent
    hashes = {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in CODE_FILES}
    verifier = directory.parents[1]/"docs/research/tools/verify_m1_marker.py"
    hashes["verify_m1_marker.py"] = hashlib.sha256(verifier.read_bytes()).hexdigest()
    return hashes


def toml_tables(value, prefix=""):
    lines = [f"[{prefix}]"] if prefix else []
    lines += [f"{key} = {json.dumps(v, ensure_ascii=False)}" for key, v in value.items()
              if not isinstance(v, dict) and v is not None]
    for key, child in value.items():
        if isinstance(child, dict):
            lines += ["", *toml_tables(child, f"{prefix}.{key}" if prefix else key)]
    return lines


def write_profile(directory, base, obi, ba, changes):
    signal = base.signal.model_copy(update=changes)
    cfg = base.model_copy(update={"signal": signal, "observation_start_sec": 15, "observation_only": True})
    data = {"rdp_sm": {"normal_entry_mode": "m1_v1", "m1": cfg.model_dump(mode="json"),
                       "obi": obi.model_dump(mode="json"), "ba": ba.model_dump(mode="json")}}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/"config.toml"
    path.write_text("\n".join(toml_tables(data))+"\n")
    load_settings(directory)  # Validate the complete frozen TOML before using it.
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_verifier():
    source = Path(__file__).resolve().parents[2]/"docs/research/tools/verify_m1_marker.py"
    spec = importlib.util.spec_from_file_location("m1_independent_verifier", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify


def frequency_target(row):
    return row.get("complete_window") and row.get("audit_valid") and 1 <= row.get("signals", 0) <= 2


def choose_profile(calibration, current):
    current_rows = [r for r in calibration if r.get("profile_index") == current]
    if len(current_rows) < 5 or frequency_target(current_rows[-1]):
        return current
    tried = {r.get("profile_index") for r in calibration}
    for index in range(len(GRID)):
        if index not in tried:
            return index
    # Only signal-frequency evidence is used for selection, never settlement PnL.
    def score(index):
        sample = [r for r in calibration if r.get("profile_index") == index]
        return sum(bool(frequency_target(r)) for r in sample)/len(sample), -index
    return max(range(len(GRID)), key=score)


def frequency_streak(calibration, profile_index):
    streak = 0
    for row in reversed(calibration):
        if row.get("profile_index") != profile_index or not frequency_target(row):
            break
        streak += 1
    return streak


def summarize_study(state):
    summary = {"status": state["status"], "phase": state["phase"], "study_dir": state["study_dir"],
               "evaluation_target": state["evaluation_target"], "window": [15, 240], "model": MODEL,
               "frozen_config_sha256": state.get("frozen_config_sha256"), "groups": {}}
    for phase in ("CALIBRATION", "EVALUATION"):
        sample = [r for r in state["sessions"] if r["phase"] == phase]
        settled = [r for r in sample if r.get("settlement", {}).get("status", "").startswith("RESOLVED")]
        unknown = [r for r in sample if r.get("error") or not r.get("paper")
                   or not r.get("complete_window") or not r.get("audit_valid")]
        def total(key, source):
            return str(sum((Decimal(r["settlement"][key]) for r in source), Decimal(0)))
        summary["groups"][phase] = {"markets": len(sample), "settled": len(settled),
            "pending": len(sample)-len(settled), "unavailable_markets": len(unknown),
            "signal_distribution": {str(k): sum(r.get("signals") == k for r in sample) for k in range(6)},
            "signals_more_than_five": sum(r.get("signals", 0) > 5 for r in sample),
            "target_frequency_markets": sum(bool(frequency_target(r)) for r in sample),
            "simulated_posts": sum(r.get("paper", {}).get("post_attempts", 0) for r in sample),
            "estimated_filled_orders": sum(r.get("paper", {}).get("filled_orders", 0) for r in sample),
            "pnl_lower_usdc": total("pnl_lower_usdc", settled) if settled and len(settled) == len(sample) and not unknown else None,
            "pnl_upper_usdc": total("pnl_upper_usdc", settled) if settled and len(settled) == len(sample) and not unknown else None,
            "settled_subtotal_lower_usdc": total("pnl_lower_usdc", settled) if settled else None,
            "settled_subtotal_upper_usdc": total("pnl_upper_usdc", settled) if settled else None,
            "pnl_if_no_unobserved_fills_usdc": total("pnl_if_no_unobserved_fills_usdc", settled) if settled else None,
            "result_scope": "QUEUE_MODEL_ESTIMATE_EXCLUDES_PENDING_AND_UNAVAILABLE_MARKETS"}
    return summary


def save(state, directory):
    atomic_json(directory/"study.json", state)
    atomic_json(directory/"summary.json", summarize_study(state))
    temporary = directory/"sessions.csv.tmp"
    columns = ("epoch", "phase", "profile", "signals", "audit_valid", "complete_window", "posts", "filled_orders",
               "spent_usdc", "settlement_status", "pnl_lower_usdc", "pnl_upper_usdc", "session_dir", "error")
    with temporary.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in state["sessions"]:
            paper, settlement = row.get("paper", {}), row.get("settlement", {})
            writer.writerow({**{k: row.get(k) for k in columns if k in row},
                "posts": paper.get("post_attempts"), "filled_orders": paper.get("filled_orders"),
                "spent_usdc": paper.get("spent_usdc"), "settlement_status": settlement.get("status"),
                "pnl_lower_usdc": settlement.get("pnl_lower_usdc"), "pnl_upper_usdc": settlement.get("pnl_upper_usdc")})
    os.replace(temporary, directory/"sessions.csv")


async def resolve_pending(state, directory, once=False, changed=None):
    while True:
        for row in state["sessions"]:
            if row.get("settlement", {}).get("status", "").startswith("RESOLVED") or not row.get("paper"):
                continue
            if time.time() < row["epoch"]+300 or time.time() < row.get("next_resolution_read", 0):
                continue
            row["next_resolution_read"] = time.time()+30
            try:
                # Existing dashboard route handles closed markets; the active
                # market-list query can omit them. This GET has no account auth.
                resolution = await asyncio.to_thread(fetch_resolution, row["identity"])
                row["settlement"] = settle_paper(row["paper"], resolution)
                row.pop("resolution_error", None)
                if resolution is not None:
                    print(f"M1 PAPER RESOLVED | {row['slug']} phase={row['phase']} "
                          f"pnl={row['settlement']['pnl_usdc']} "
                          f"bounds=[{row['settlement']['pnl_lower_usdc']},{row['settlement']['pnl_upper_usdc']}]", flush=True)
            except Exception as exc:
                row["resolution_error"] = type(exc).__name__+":"+str(exc)[:200]
            save(state, directory)
            if changed is not None:
                changed.set()
            await asyncio.sleep(.2)
        if once:
            return
        await asyncio.sleep(5)


async def wait_open_after(last_epoch):
    public = PublicClob()
    try:
        official, _, _ = await public.clock()
    finally:
        await public.close()
    epoch = int(official//300)*300
    if official-epoch > 2 or last_epoch is not None and epoch <= last_epoch:
        epoch = max(epoch+300, (last_epoch or 0)+300)
    while time.time() < epoch:  # noqa: ASYNC110 - recheck wall-clock boundary after clock jumps.
        await asyncio.sleep(min(5, epoch-time.time()))
    return epoch


async def run_study(config_dir, directory, *, evaluation_sessions=50, output_budget_mib=1024,
                    resume=False, upgrade_calibration=False):
    base, obi, ba, _ = load_settings(config_dir)
    if not resume:
        directory.mkdir(parents=True, exist_ok=False)
    hashes = code_hashes()
    state = {"status": "RUNNING", "phase": "CALIBRATION", "study_dir": str(directory.resolve()),
        "created_utc": datetime.now(UTC).isoformat(), "evaluation_target": evaluation_sessions,
        "frequency_target": {"min": 1, "max": 2, "consecutive_calibration_markets": 5},
        "window": [15, 240], "sessions": [], "source_sha256": hashes, "profile_grid": list(GRID),
        "calibration_revision": 0,
        "paper_model": {"model": MODEL, "post_latency_ms": 400, "cancel_latency_ms": 200,
                        "maker_fees_usdc": 0, "rebates_usdc": 0, "chain_costs_included": False}}
    profile_index, last_epoch, failures = 0, None, 0
    if resume:
        state = json.loads((directory/"study.json").read_text())
        if state["evaluation_target"] != evaluation_sessions:
            raise RuntimeError("M1_STUDY_RESUME_IDENTITY_MISMATCH")
        if state["source_sha256"] != hashes:
            if not upgrade_calibration or state["phase"] != "CALIBRATION" or any(r["phase"] == "EVALUATION" for r in state["sessions"]):
                raise RuntimeError("M1_STUDY_RESUME_IDENTITY_MISMATCH")
            history = state.setdefault("source_history", [{"revision": state.get("calibration_revision", 0),
                                                           "sha256": state["source_sha256"]}])
            for row in state["sessions"]:
                row.setdefault("calibration_revision", state.get("calibration_revision", 0))
            if "active_market" in state:
                state["active_market"].setdefault("calibration_revision", state.get("calibration_revision", 0))
            state["calibration_revision"] = state.get("calibration_revision", 0)+1
            history.append({"revision": state["calibration_revision"], "sha256": hashes,
                            "upgraded_utc": datetime.now(UTC).isoformat(), "reason": "CALIBRATION_ONLY_MODEL_FIX"})
            state["source_sha256"] = hashes
            state["paper_model"]["model"] = MODEL
        if "active_market" in state:
            interrupted = state.pop("active_market")
            interrupted.update(error="INTERRUPTED_MARKET_UNOBSERVED", complete_window=False, audit_valid=False)
            state["sessions"].append(interrupted)
        profile_index = state.get("current_profile_index", 0)
        last_epoch = max((r["epoch"] for r in state["sessions"]), default=None)
        if (directory/"config/config.toml").is_file():
            base, obi, ba, _ = load_settings(directory/"config")
        state["status"] = "RUNNING"
        state.pop("error", None)
    state["pid"] = os.getpid()
    save(state, directory)
    verifier = load_verifier()
    changed = asyncio.Event()
    worker = asyncio.create_task(resolve_pending(state, directory, changed=changed), name="m1-paper-resolution")
    try:
        while sum(r["phase"] == "EVALUATION" for r in state["sessions"]) < evaluation_sessions:
            if hashes != code_hashes():
                raise RuntimeError("M1_STUDY_SOURCE_CHANGED_RESTART_REQUIRED")
            if shutil.disk_usage(directory).free < 2*1024**3:
                raise RuntimeError("M1_STUDY_DISK_RESERVE_REACHED")
            epoch = await wait_open_after(last_epoch)
            last_epoch = epoch
            config = directory/"config"
            config_sha = write_profile(config, base, obi, ba, GRID[profile_index])
            if state["phase"] == "EVALUATION" and config_sha != state["frozen_config_sha256"]:
                raise RuntimeError("M1_STUDY_FROZEN_CONFIG_CHANGED")
            row = {"epoch": epoch, "phase": state["phase"], "profile_index": profile_index,
                   "profile": GRID[profile_index]["profile"], "config_sha256": config_sha,
                   "calibration_revision": state.get("calibration_revision", 0)}
            state["active_market"] = row.copy()
            state["current_profile_index"] = profile_index
            save(state, directory)
            print(f"M1 STUDY MARKET | phase={row['phase']} epoch={epoch} profile={row['profile']}", flush=True)
            try:
                result = await observe(config, paper_mode=True, output_root=directory/"markets",
                    max_output_mib=output_budget_mib, post_latency_ms=400, cancel_latency_ms=200)
                session = Path(result["session_dir"])
                metadata = json.loads((session/"metadata.json").read_text())
                market = metadata["market"]
                identity = {"slug": result["slug"], "condition_id": market["condition_id"],
                            "token_ids": {"YES": market["tokens"][0]["token_id"], "NO": market["tokens"][1]["token_id"]}}
                signals = result["m1_observation"]["counts"].get("WOULD_POST", 0)
                audit = verifier(session)
                atomic_json(session/"marker_audit.json", audit)
                audit_valid = len(audit["proofs"]) == signals and not any(not p["passed"] for p in audit["proofs"])
                row.update(session_dir=str(session), slug=result["slug"], identity=identity,
                    signals=signals, paper=result["paper"], audit_valid=audit_valid,
                    complete_window=result["feed_start_elapsed"] <= 15 and result["stop_reason"] == "M1_OBSERVATION_CUTOFF"
                        and not result["writer"]["failure"] and result["writer"]["dropped"] == 0,
                    settlement={"status": "PENDING_OFFICIAL_RESOLUTION", "pnl_usdc": None})
                if result["market_epoch"] != epoch:
                    raise RuntimeError("M1_STUDY_MARKET_EPOCH_MISMATCH")
                failures = 0
            except Exception as exc:
                row.update(error=type(exc).__name__+":"+str(exc)[:200], complete_window=False, audit_valid=False)
                failures += 1
            state["sessions"].append(row)
            state.pop("active_market", None)
            print(f"M1 STUDY RESULT | phase={row['phase']} profile={row['profile']} "
                  f"signals={row.get('signals')} posts={row.get('paper', {}).get('post_attempts')} "
                  f"filled={row.get('paper', {}).get('filled_orders')} audit={row.get('audit_valid')}", flush=True)
            calibration = [r for r in state["sessions"] if r["phase"] == "CALIBRATION"
                           and r.get("calibration_revision", 0) == state.get("calibration_revision", 0)]
            if state["phase"] == "CALIBRATION":
                if frequency_streak(calibration, profile_index) >= 5:
                    state.update(phase="EVALUATION", frozen_config_sha256=config_sha,
                                 frozen_at_epoch=epoch, frozen_profile=GRID[profile_index])
                    (directory/"frozen-config.toml").write_bytes((config/"config.toml").read_bytes())
                    print("M1 STUDY FROZEN | five consecutive 1-2 signal markets; starting 50 NEW markets", flush=True)
                else:
                    profile_index = choose_profile(calibration, profile_index)
            state["current_profile_index"] = profile_index
            save(state, directory)
            if failures >= 3:
                raise RuntimeError("M1_STUDY_THREE_CONSECUTIVE_FAILURES")
        state["status"] = "WAITING_FINAL_RESOLUTIONS"
        save(state, directory)
        while any(r["phase"] == "EVALUATION" and r.get("paper")
                  and not r.get("settlement", {}).get("status", "").startswith("RESOLVED") for r in state["sessions"]):
            await changed.wait()
            changed.clear()
        state["status"] = "COMPLETE" if not any(r.get("error") or not r.get("complete_window")
            or not r.get("audit_valid") for r in state["sessions"] if r["phase"] == "EVALUATION") else "COMPLETE_WITH_UNAVAILABLE_MARKETS"
    except BaseException as exc:
        state["status"] = "INTERRUPTED" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else "FAILED"
        state["error"] = type(exc).__name__+":"+str(exc)[:200]
        raise
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        save(state, directory)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config-m1"))
    parser.add_argument("--study-dir", type=Path)
    parser.add_argument("--evaluation-sessions", type=int, default=50)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--upgrade-calibration", action="store_true",
                        help="Retain old calibration rows, reset the streak after a model fix; forbidden in evaluation")
    args = parser.parse_args()
    directory = args.study_dir or OUTPUT_ROOT/"studies"/datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    if args.status:
        print((directory/"summary.json").read_text())
        return 0
    if OUTPUT_ROOT.resolve() not in directory.resolve().parents:
        parser.error("study directory must be inside research_outputs/m1_isolated")
    if not 1 <= args.evaluation_sessions <= 1000:
        parser.error("--evaluation-sessions must be in [1, 1000]")
    lower_priority()
    try:
        with observer_lock():
            result = asyncio.run(run_study(args.config_dir, directory,
                evaluation_sessions=args.evaluation_sessions, resume=args.resume,
                upgrade_calibration=args.upgrade_calibration))
    except KeyboardInterrupt:
        return 130
    print(json.dumps(summarize_study(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
