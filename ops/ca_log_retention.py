#!/usr/bin/env python3
"""Retain 24 hours of completed M1 raw logs. Historical entry-point name kept.

Never delete risk/virtual databases, recovery JSON, dashboard data or research
studies (including the frozen 300-market study). No trading imports or network.
"""

import importlib.util
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "retention_core", Path(__file__).with_name("log_retention.py")
)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


class M1Cleaner(core.Cleaner):
    research_roots = (("research_outputs", "m1_virtual", "sessions"),)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers = {}
        self.virtual_slugs = {}
        self._recovery_cache = {}

    def ledger_path(self, value, directory):
        if not isinstance(value, str):
            raise ValueError("M1 recovery ledger path missing")
        path = Path(value)
        if (
            not path.is_absolute()
            or path.parent != directory
            or path != path.resolve()
            or re.fullmatch(r"m1_live_[0-9a-f]{64}\.sqlite3", path.name) is None
        ):
            raise ValueError("M1 recovery ledger outside journal scope")
        return path

    def recovery(self, directory):
        # A native M1 state_path marker is not a malformed legacy session fence.
        children = self.children(directory)
        signature = {
            n: core.identity(s)
            for n, s in children.items()
            if n.startswith(("rdp_obi_market_", "rdp_obi_account_")) and n.endswith(".json")
        }
        key = (signature, frozenset(n for n in children if n.endswith(".jsonl")))
        cached = self._recovery_cache.get(directory)
        if cached and cached[0] == key:
            return cached[1]
        protected, sessions = set(), set()
        for name in signature:
            obj = self.read_json(directory, name)
            if not isinstance(obj, dict):
                raise ValueError("Unrecognized recovery state")
            if obj.get("strategy") == "m1_live_v1" and not obj.get("session_id"):
                self.ledger_path(obj.get("state_path"), directory)
                continue  # Native journals additionally require a completed ledger.
            if not isinstance(obj.get("session_id"), str) or not obj["session_id"]:
                raise ValueError(f"Incomplete recovery state: {directory / name}")
            sessions.add(obj["session_id"])
            if name.startswith("rdp_obi_market_"):
                value = obj.get("journal_path")
                path = Path(value).resolve() if isinstance(value, str) else None
                if path is None or path.parent != directory or not path.name.startswith("rdp_sm_"):
                    raise ValueError("Unexpected recovery journal")
                protected.add(path.name)
        for name in children:
            if name.endswith(".jsonl") and any(name.endswith("_" + s + ".jsonl") for s in sessions):
                protected.add(name)
        self._recovery_cache[directory] = (key, protected)
        return protected

    def header(self, directory, name):
        with self.directory(directory) as fd:
            handle = os.open(name, core.FILE_FLAGS, dir_fd=fd)
            with os.fdopen(handle, "rb") as stream:
                s = os.fstat(stream.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                    return None
                line = stream.readline(131073)
        try:
            row = json.loads(line)
            data, h = row["data"], row["data"]["payload"]
            if (
                len(line) > 131072
                or not line.endswith(b"\n")
                or row["kind"] != "sm_session"
                or h.get("normal_entry_mode") != "m1_v1"
                or h.get("strategy") != "m1_live_v1"
                or not isinstance(data["session_id"], str)
                or not name.endswith("_" + data["session_id"] + ".jsonl")
            ):
                return None
            return data["session_id"], h
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def completed_native(self, directory, sid, h):
        try:
            path = self.ledger_path(h.get("state_path"), directory)
            s = path.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                return False
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
                db.execute("BEGIN")
                metadata = dict(
                    db.execute(
                        "SELECT key,value FROM metadata WHERE key IN ('complete','session_id','identity')"
                    )
                )
                if (
                    json.loads(metadata.get("complete", "false")) is not True
                    or json.loads(metadata.get("session_id", "null")) != sid
                    or json.loads(metadata.get("identity", "{}")).get("market_id")
                    != h.get("market_id")
                ):
                    return False
                if db.execute(
                    "SELECT 1 FROM opportunities WHERE state IN ('UNKNOWN','RESERVED','PREPARED') LIMIT 1"
                ).fetchone():
                    return False
                if db.execute("SELECT 1 FROM uncertain_posts LIMIT 1").fetchone():
                    return False
            return True
        except (ValueError, OSError, sqlite3.Error, AttributeError):
            return False  # Unknown/unreadable/missing risk evidence stays on disk.

    def completed_virtual(self, slug):
        path = self.base / "research_outputs/m1_virtual/state.sqlite3"
        try:
            if path != path.resolve() or not stat.S_ISREG(path.lstat().st_mode):
                return False
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
                row = db.execute("SELECT state,epoch FROM markets WHERE slug=?", (slug,)).fetchone()
                return bool(
                    row
                    and row[0] in {"CLOSED", "INTERRUPTED"}
                    and row[1] == int(slug.rsplit("-", 1)[1])
                    and row[1] + 300 < self.cutoff + core.RETENTION_SECONDS
                )
        except (ValueError, OSError, sqlite3.Error):
            return False

    def discover(self):
        for rel in ("journal", "livecfg/journal"):
            directory = self.base / rel
            protected = self.recovery(directory)
            self.protected_recovery.update(str(directory / n) for n in protected)
            for name, info in self.children(directory).items():
                if (
                    not name.startswith("rdp_sm_")
                    or not name.endswith(".jsonl")
                    or not self.old_regular(info)
                ):
                    continue
                try:
                    value = self.header(directory, name)
                except FileNotFoundError:
                    continue
                if not value:
                    continue
                sid, h = value
                if name in protected or not self.completed_native(directory, sid, h):
                    self.protected_recovery.add(str(directory / name))
                    continue
                self.headers[(directory, name)] = value
                self.add(directory, "trading_journal", {name: info})
        parent = self.base / "research_outputs/m1_virtual/sessions"
        for name, info in self.children(parent).items():
            match = re.fullmatch(r"(btc-updown-5m-\d{10})-\d+", name)
            if not match or not stat.S_ISDIR(info.st_mode):
                continue
            directory = parent / name
            contents = self.children(directory)
            if (
                contents
                and not set(contents) - {"signals.jsonl"}
                and self.completed_virtual(match[1])
            ):
                self.virtual_slugs[directory] = match[1]
                self.add(directory, "m1_virtual_session_log", contents, whole=True)

    def safe_group(self, group):
        if not super().safe_group(group):
            return False
        if group["category"] == "trading_journal":
            return all(
                self.completed_native(group["directory"], *self.headers[(group["directory"], n)])
                for n in group["files"]
            )
        return self.completed_virtual(self.virtual_slugs[group["directory"]])

    def run(self, *, apply=False, receipt=None):
        report = super().run(apply=apply, receipt=receipt)
        report["scope"] = "M1_COMPLETED_RAW_LOGS_ONLY"
        report["preserved"] = [
            "M1_300_MARKET_STUDY",
            "ALL_SQLITE_LEDGERS",
            "RECOVERY_STATE",
            "OPEN_RECENT_CHANGED_FILES",
            "PENDING_SUBMISSION_RISK",
            "DASHBOARD",
        ]
        return report


if __name__ == "__main__":
    core.main(M1Cleaner, default_data_root=Path("/home/ubuntu/polymaker"))
