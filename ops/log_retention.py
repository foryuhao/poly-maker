#!/usr/bin/env python3
"""Keep 24 hours of trading logs, preserving recovery evidence and all Shadow data.

Standalone standard-library tool. Dry-run by default; see ops/README.md.
"""

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import stat
import time
import uuid
from pathlib import Path

RETENTION_SECONDS = 24 * 60 * 60
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
SYNC_FILES = {"manifest.json", "summary.json", "events.jsonl", "snapshots.jsonl", "strategy_refs.jsonl"}
RUN_FILES = {"raw.log", "events.jsonl", "chat_report.md", "run.json"}
SETTLEMENT_FILES = {"raw.log", "settlement_report.md", "sweep.json"}
ROOT_LOG_FILES = {"paper.jsonl", "live.jsonl", "favorite-first-live.jsonl", "filltest.jsonl"}


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink)


def open_inodes():
    """Require visibility of all processes; never infer safety from a denied read."""
    result = set()
    for proc in Path("/proc").glob("[0-9]*/fd"):
        try:
            for fd in proc.iterdir():
                try:
                    s = fd.stat()
                    result.add((s.st_dev, s.st_ino))
                except (FileNotFoundError, ProcessLookupError):
                    pass
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


class Cleaner:
    research_roots = (("research_outputs", "periodic_sync"),)

    def __init__(self, base, *, now=None, open_reader=open_inodes):
        self.base = Path(base).absolute()
        if self.base.resolve() != self.base:
            raise ValueError("Data root must be a canonical directory, without symlink ancestors")
        self.cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
        self.open_reader = open_reader
        self.groups = []
        self.warnings = []
        self.protected_recovery = set()

    @contextlib.contextmanager
    def directory(self, path):
        """Anchor every access to directory descriptors; follow no symlinks."""
        parts = path.relative_to(self.base).parts
        if not parts or parts[0] not in {"journal", "logs", "livecfg", "research_outputs"}:
            raise ValueError("Outside cleanup scope")
        if parts[0] == "research_outputs" and not any(parts[:len(root)] == root for root in self.research_roots):
            raise ValueError("Outside allowed research log directories")
        if parts[0] == "livecfg" and (len(parts) < 2 or parts[1] not in {"journal", "logs"}):
            raise ValueError("Only logs and journal are allowed under livecfg")
        fd = os.open(self.base, DIRECTORY_FLAGS)
        try:
            for part in parts:
                following = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = following
            yield fd
        finally:
            os.close(fd)

    def children(self, path):
        try:
            with self.directory(path) as fd:
                return {name: os.stat(name, dir_fd=fd, follow_symlinks=False) for name in os.listdir(fd)}
        except FileNotFoundError:
            return {}

    def read_json(self, directory, name):
        with self.directory(directory) as fd:
            handle = os.open(name, FILE_FLAGS, dir_fd=fd)
            with os.fdopen(handle) as stream:
                s = os.fstat(stream.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_size > 1_000_000:
                    raise ValueError(f"Unexpected state file: {directory / name}")
                return json.load(stream)

    def recovery(self, directory):
        """Every fence reference survives, including fences left after a clean exit."""
        protected, sessions = set(), set()
        for name in self.children(directory):
            if name.startswith(("rdp_obi_market_", "rdp_obi_account_")) and name.endswith(".json"):
                obj = self.read_json(directory, name)
                if not isinstance(obj, dict) or not obj.get("session_id"):
                    raise ValueError(f"Incomplete recovery state: {directory / name}")
                sessions.add(str(obj["session_id"]))
                if name.startswith("rdp_obi_market_"):
                    journal = Path(obj["journal_path"]).resolve()
                    if journal.parent != directory or not journal.name.startswith("rdp_sm_"):
                        raise ValueError(f"Unexpected recovery journal: {directory / name}")
                    protected.add(journal.name)
        for name in self.children(directory):
            if name.endswith(".jsonl") and any(name.endswith(f"_{s}.jsonl") for s in sessions):
                protected.add(name)
        return protected

    def old_regular(self, s):
        return stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_mtime < self.cutoff

    def add(self, directory, category, files, *, whole=False):
        if files and all(self.old_regular(s) for s in files.values()):
            self.groups.append(dict(directory=directory, category=category, whole=whole,
                                    files={name: identity(s) for name, s in files.items()}))

    def settlement_safe(self, directory):
        obj = self.read_json(directory, "sweep.json")
        if not isinstance(obj, dict) or not obj.get("finished_at"):
            return False
        actions = obj.get("actions", [])
        if not isinstance(actions, list):
            return False
        return all(isinstance(a, dict) and a.get("submission_status") != "SUBMISSION_UNKNOWN"
                   and a.get("terminal_status") != "UNKNOWN"
                   and a.get("verification_status") not in {
                       "INCOMPLETE_RECONCILIATION", "POST_STATE_CHANGED_TX_UNKNOWN"
                   } for a in actions)

    def discover(self):
        for rel in ("journal", "livecfg/journal"):
            directory = self.base / rel
            protected = self.recovery(directory)
            self.protected_recovery.update(str(directory / name) for name in protected)
            for name, s in self.children(directory).items():
                if name.startswith("rdp_sm_") and name.endswith(".jsonl") and name not in protected:
                    self.add(directory, "trading_journal", {name: s})
        for rel in ("logs", "livecfg/logs"):
            root = self.base / rel
            for name, s in self.children(root).items():
                if name in ROOT_LOG_FILES:
                    self.add(root, "application_log", {name: s})
            for kind, allowed, required in (("runs", RUN_FILES, "run.json"),
                                            ("settlement", SETTLEMENT_FILES, "sweep.json")):
                parent = root / kind
                for name, s in self.children(parent).items():
                    if not stat.S_ISDIR(s.st_mode):
                        continue
                    directory = parent / name
                    files = self.children(directory)
                    if required not in files or set(files) - allowed:
                        continue  # pending.json, locks, unknown files and nested directories survive.
                    if kind == "settlement":
                        try:
                            if not self.settlement_safe(directory):
                                continue
                        except (ValueError, OSError):
                            self.warnings.append(f"Unreadable settlement state retained: {directory}")
                            continue
                    self.add(directory, kind, files, whole=True)
        parent = self.base / "research_outputs/periodic_sync"
        for name, s in self.children(parent).items():
            if stat.S_ISDIR(s.st_mode):
                directory = parent / name
                files = self.children(directory)
                if "summary.json" in files and not set(files) - SYNC_FILES:
                    self.add(directory, "completed_periodic_sync", files, whole=True)

    def safe_group(self, group):
        """Recheck recovery references immediately before any deletion."""
        return (group['category'] != 'trading_journal'
                or not set(group['files']) & self.recovery(group['directory']))

    def run(self, *, apply=False, receipt=None):
        self.discover()  # Malformed recovery state aborts before any deletion.
        report = dict(cutoff_utc=dt.datetime.fromtimestamp(self.cutoff, dt.UTC).isoformat(),
                      retention_hours=24, apply=apply, deleted_files=0, deleted_bytes=0,
                      candidate_files=sum(len(g["files"]) for g in self.groups),
                      candidate_bytes=sum(s[2] for g in self.groups for s in g["files"].values()),
                      protected_recovery_files=len(self.protected_recovery), skipped_groups=0,
                      warnings=self.warnings, categories={})
        for group in self.groups:
            directory = group["directory"]
            files = group["files"]
            try:
                current = self.children(directory)
                if group["whole"] and set(current) != set(files):
                    report["skipped_groups"] += 1
                    continue
                if any(name not in current or identity(current[name]) != expected for name, expected in files.items()):
                    report["skipped_groups"] += 1
                    continue
                if not self.safe_group(group):
                    report["skipped_groups"] += 1
                    continue
                opened = self.open_reader()
                if any((s.st_dev, s.st_ino) in opened for name, s in current.items()
                       if group["whole"] or name in files):
                    report["skipped_groups"] += 1
                    continue
                if not apply:
                    continue
                with self.directory(directory) as fd:
                    if not self.safe_group(group):
                        report["skipped_groups"] += 1
                        continue
                    for name, expected in files.items():
                        s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        if identity(s) != expected or not self.old_regular(s):
                            report["skipped_groups"] += 1
                            break
                        path = str(directory / name)
                        if receipt:
                            receipt.write(json.dumps(dict(event="delete_intent", path=path, bytes=s.st_size)) + "\n")
                            receipt.flush()
                            os.fsync(receipt.fileno())
                        os.unlink(name, dir_fd=fd)
                        report["deleted_files"] += 1
                        report["deleted_bytes"] += s.st_size
                        counts = report["categories"].setdefault(group["category"], dict(files=0, bytes=0))
                        counts["files"] += 1
                        counts["bytes"] += s.st_size
                        if receipt:
                            receipt.write(json.dumps(dict(event="deleted", path=path)) + "\n")
                            receipt.flush()
                if group["whole"]:
                    with self.directory(directory.parent) as fd, contextlib.suppress(OSError):
                        os.rmdir(directory.name, dir_fd=fd)  # Empty directories only.
            except FileNotFoundError:
                report["skipped_groups"] += 1
        return report


def main(cleaner_class=Cleaner, *, default_data_root=Path("/home/ubuntu/poly-maker")):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root)
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Delete eligible logs (default is dry-run)")
    args = parser.parse_args()
    if args.state_dir is None:
        args.state_dir = args.data_root / "logs" / "retention"
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.state_dir / "cleanup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        with (args.state_dir / f"run-{run_id}.jsonl").open("x") as receipt:
            report = cleaner_class(args.data_root).run(apply=args.apply, receipt=receipt)
            receipt.write(json.dumps(dict(event="summary", **report)) + "\n")
            receipt.flush()
            os.fsync(receipt.fileno())
        temporary = args.state_dir / "latest.json.tmp"
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.state_dir / "latest.json")
        # Keep the cleaner's own receipts bounded to the latest 288 runs (~24 hours).
        for path in sorted(args.state_dir.glob("run-*.jsonl"))[:-288]:
            path.unlink()
        print(json.dumps(report))


if __name__ == "__main__":
    main()
