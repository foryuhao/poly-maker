"""Continue an active three-strategy public baseline with M12 and M21."""

import argparse
import asyncio
import fcntl
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from m1223_shared72 import (
    MARKET_SECONDS,
    SCHEMA,
    RecordedMarketEngines,
    SharedRun,
    configuration,
    seal_market,
)
from m1223_shared_storage import HANDOFF_FILE, PAIR, PHASE_DIRECTORY, prune_expired_tapes, storage_status
from polymaker.m1234_sim.events import write_json


ORIGINAL_UNIT = "polymaker-m122124-baseline-168h-extension.service"


def runtime_rows(run):
    with (run / "runtime.jsonl").open() as source:
        for line in source:
            if line.endswith("\n"):
                yield json.loads(line)


def backlog(run, cutoff):
    complete, archived = set(), {}
    for row in runtime_rows(run):
        if row["event"] == "MARKET_END" and row["reason"] == "T300_OBSERVED":
            complete.add(row["market"])
        elif row["event"] == "MARKET_TAPE_CLOSED":
            archived[row["market"]] = row["input_seq"]
    return [(slug, archived[slug]) for slug in sorted(complete & archived.keys())
            if int(slug.rsplit("-", 1)[1]) < cutoff
            and not (run / slug / "market-report.json").is_file()]


def copy_primary(original, target, input_seq):
    target.mkdir()
    plain = original / "simulation.sqlite3"
    wal = original / "simulation.sqlite3-wal"
    if wal.exists() and wal.stat().st_size:
        raise ValueError("ORIGINAL_M12_WAL_NOT_CLOSED")
    db = target / plain.name
    if plain.exists():
        shutil.copyfile(plain, db)
    else:
        with db.open("xb") as out:
            subprocess.run(("zstd", "-qdc", "--", str(plain.with_suffix(".sqlite3.zst"))),
                           stdout=out, check=True, timeout=30)
    for evidence in original.glob("*.json"):
        shutil.copyfile(evidence, target / evidence.name)
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        state = json.loads(conn.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
        ended = conn.execute("SELECT 1 FROM audit WHERE kind='OBSERVATION_END'").fetchone()
    finally:
        conn.close()
    if state["input_seq"] != input_seq or not ended:
        raise ValueError("ORIGINAL_M12_SEAL_WATERMARK_MISMATCH")
    return {"seq": input_seq, "closed": True, "ended": True,
            "complete": state["coverage"]["complete_window"], "resolution": state["resolution"]}


class RecoveredPair(RecordedMarketEngines):
    def ledger_states(self):
        return {"M12": self.primary_state, **self.worker_states}


class PairRun(SharedRun):
    def __init__(self, original, settings, parameters, *, end, project=None):
        super().__init__(original / PHASE_DIRECTORY, project or original.parent, settings, parameters,
                         scheduled_end=end)
        self.original = original
        self.warmed = asyncio.Queue()
        self.cutoff = None

    def log(self, event, **payload):
        super().log(event, **payload)
        if event == "MARKET_WARMED_T15" and self.cutoff is None:
            self.warmed.put_nowait(int(payload["market"].rsplit("-", 1)[1]))

    async def handoff(self):
        deadline = time.monotonic() + 3 * MARKET_SECONDS + 25
        while (remaining := deadline - time.monotonic()) > 0:
            epoch = await asyncio.wait_for(self.warmed.get(), timeout=remaining)
            previous = f"btc-updown-5m-{epoch - MARKET_SECONDS}"
            while self.server_now() < epoch + 20 and not self.stop.is_set():
                if any(row["event"] == "MARKET_END" and row.get("market") == previous
                       and row.get("reason") == "T300_OBSERVED"
                       for row in await asyncio.to_thread(lambda: list(runtime_rows(self.original)))):
                    transition = {"cutoff_epoch": epoch, "strategies": list(PAIR),
                                  "state": "SWITCHING", "at": time.time()}
                    write_json(self.output / HANDOFF_FILE, transition)
                    proc = await asyncio.create_subprocess_exec(
                        "systemctl", "--user", "stop", ORIGINAL_UNIT,
                        stdin=asyncio.subprocess.DEVNULL,
                    )
                    if await proc.wait() != 0:
                        raise RuntimeError("ORIGINAL_BASELINE_STOP_FAILED")
                    self.cutoff = epoch
                    write_json(self.output / HANDOFF_FILE,
                               {**transition, "state": "ACTIVE", "at": time.time()})
                    self.log("STRATEGY_HANDOFF", first_market=f"btc-updown-5m-{epoch}",
                             previous_market=previous, strategies=list(PAIR))
                    return
                await asyncio.sleep(0.5)
        raise RuntimeError("PAIR_HANDOFF_NO_COMMON_WARM_BOUNDARY")

    def prune_tapes(self, now):
        if self.cutoff is None:
            return {"removed_tapes": 0, "removed_allocated_bytes": 0}
        old = prune_expired_tapes(self.original, now)
        new = prune_expired_tapes(self.output, now, strategies=PAIR, earliest_epoch=self.cutoff)
        return {key: old[key] + new[key] for key in old}

    def accept_capture_for_replay(self, capture):
        return int(capture.meta.slug.rsplit("-", 1)[1]) >= self.cutoff

    async def recover_one(self, slug, input_seq):
        source = self.original / slug
        recovered = self.output / "recovered"
        recovered.mkdir(exist_ok=True)
        target = recovered / slug
        if (target / "market-report.json").exists():
            return
        with tempfile.TemporaryDirectory(prefix=".recover-", dir=recovered) as temporary:
            stage = Path(temporary) / slug
            stage.mkdir()
            tape = stage / "input.jsonl.zst"
            os.link(source / tape.name, tape)
            group = RecoveredPair(stage, self.settings, self.parameters, self.owner)
            group.primary_state = await asyncio.to_thread(
                copy_primary, source / "M12", stage / "M12", input_seq
            )
            self.replaying = SimpleNamespace(meta=SimpleNamespace(slug=slug), group=group)
            await group.materialize(tape, original_plan=self.original / "plan.json")
            await asyncio.to_thread(seal_market, stage, tape, input_seq, slug, group)
            stage.rename(target)
        self.log("MARKET_SEALED", market=slug, input_seq=input_seq, recovered=True)

    async def materialize_closed_markets(self):
        try:
            await self.handoff()
        except Exception as exc:
            self.errors.append({"stage": "STRATEGY_HANDOFF", "error": str(exc)})
            self.request_stop("STRATEGY_HANDOFF_FAILED")
            return
        pending = await asyncio.to_thread(backlog, self.original, self.cutoff)
        self.log("PAIR_RECOVERY_STARTED", markets=[slug for slug, _ in pending])
        for slug, input_seq in pending:
            if self.stop.is_set():
                return
            try:
                await self.recover_one(slug, input_seq)
            except Exception as exc:
                failure = {"market": slug, "stage": "PAIR_BACKLOG_REPLAY", "error": str(exc)}
                self.errors.append(failure)
                self.log("MARKET_LEDGER_REPLAY_FAILED", **failure)
            finally:
                self.replaying = None
        await super().materialize_closed_markets()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    original = args.run_directory.resolve(strict=True)
    plan = json.loads((original / "plan.json").read_text())
    if (original.parent.name != "m122124_baseline" or plan["schema"] != SCHEMA
            or plan["mode"] != "SIMULATION_ONLY_PUBLIC_DATA" or plan["path"] != str(original)):
        parser.error("requires the active public simulation baseline")
    settings, parameters = configuration(PAIR)
    previous = plan["continuation_settings"]
    for key in ("implementation", "contract_hash", "sop_execution"):
        if settings[key] != previous[key]:
            parser.error(f"the original baseline differs on {key}")
    for key in ("strategy_definitions", "strategy_revisions"):
        if settings[key] != {name: previous[key][name] for name in PAIR}:
            parser.error(f"the original baseline differs on {key}")

    async def supervise():
        run = PairRun(original, settings, parameters, end=plan["market_epochs"][-1] + MARKET_SECONDS,
                      project=args.project_root.resolve(strict=True))
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, run.request_stop, "MANUAL_STOP")
        return await run.run()

    disk = storage_status(original, args.project_root.resolve(strict=True))
    if disk["reason"]:
        parser.error("baseline disk reserve is unavailable")
    subprocess.run(("systemctl", "--user", "is-active", "--quiet", ORIGINAL_UNIT), check=True)
    with (original.parent / "pair-cutover.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (original / PHASE_DIRECTORY).mkdir(exist_ok=False)
        result = asyncio.run(supervise())
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
