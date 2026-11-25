"""Read committed admissions from the running, independently owned M12 baseline."""

from contextlib import closing
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import struct
import time

from polymaker.m12_live import M12_VERSION
from polymaker.m4_types import M21_VERSION

from .transport import Snapshot


INOTIFY_EVENT = struct.Struct("iIII")
# Modify, move, create and delete. SQLite read-only handles can emit CLOSE_WRITE
# when they close a WAL descriptor, so that event cannot signal a baseline write.
INOTIFY_CHANGES = 0xFC2
BASELINE_VERSIONS = {"M12": M12_VERSION, "M21": M21_VERSION}


def verify_run(path, *, strategy=None):
    path = path.resolve(strict=True)
    data = json.loads((path / "plan.json").read_bytes())
    if any(data.get(k) != v for k, v in (("path", str(path)),
            ("schema", "m122124-shared-public-72h-v1"), ("mode", "SIMULATION_ONLY_PUBLIC_DATA"))):
        raise ValueError("expected the authoritative simulation baseline directory")
    if strategy is not None:
        if data.get("strategy", strategy) != strategy:
            raise ValueError(f"baseline plan does not belong to strategy {strategy}")
        definition = data.get("settings", {}).get("strategy_definitions", {}).get(strategy, {})
        if definition.get("version") != BASELINE_VERSIONS[strategy]:
            raise ValueError(f"baseline plan does not declare a supported {strategy} signal version")
    return path


def market_source(root, slug, *, archived=False):
    """Resolve this market's owner from the baseline's committed pair handoff."""
    phase = root / "m12-m21-phase"
    try:
        handoff = json.loads((phase / "m12-m21-handoff.json").read_bytes())
    except FileNotFoundError:
        return root
    cutoff = handoff["cutoff_epoch"]
    if (type(cutoff) is not int or cutoff % 300 or handoff["strategies"] != ["M12", "M21"]
            or handoff["state"] not in ("SWITCHING", "ACTIVE") or phase.resolve() != phase):
        raise ValueError("invalid M12 baseline handoff")
    epoch = int(slug.rsplit("-", 1)[1])
    if epoch < cutoff:
        recovered = phase / "recovered"
        if (archived and handoff["state"] == "ACTIVE" and not (root / slug / "market-report.json").is_file()
                and (recovered / slug / "market-report.json").is_file()):
            return recovered
        return root
    if handoff["state"] != "ACTIVE":
        raise ValueError("M12 baseline handoff not yet committed")
    return verify_run(phase)


class SQLiteIntents:
    """WAL notifications wake read-only queries; a notification is not a commit."""

    sqlite_source = True
    follow_limit = True

    def __init__(self, path, meta, max_overpay_ticks, *, strategy="M12"):
        self.root = path
        self.run = path
        self.strategy = strategy
        self.version = BASELINE_VERSIONS[strategy]
        self.check_handoff_at = float("-inf")
        self.meta = meta
        self.max_overpay_ticks = max_overpay_ticks
        self.path = path / meta.slug / strategy / "simulation.sqlite3"
        self.database_id = self.latest = self.snapshot = None
        self.watermark = 0
        self.pending_admissions = []
        self.recheck_at = self.health_at = float("-inf")
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.watched = {}
        self.wake_names = {os.fsencode(name) for name in (
            meta.slug, self.path.parent.name, self.path.name, self.path.name + "-wal",
        )}

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _watch(self):
        for directory in (self.run, self.path.parent.parent, self.path.parent):
            try:
                st = directory.stat()
            except FileNotFoundError:
                continue
            identity = st.st_dev, st.st_ino
            if self.watched.get(directory) == identity:
                continue
            wd = self.libc.inotify_add_watch(self.fd, os.fsencode(directory), INOTIFY_CHANGES)
            if wd >= 0:
                self.watched[directory] = identity

    def _source_changed(self):
        try:
            events = os.read(self.fd, 65536)
        except BlockingIOError:
            return False
        offset = 0
        while offset < len(events):
            _, _, _, name_size = INOTIFY_EVENT.unpack_from(events, offset)
            offset += INOTIFY_EVENT.size
            name = events[offset:offset + name_size].rstrip(b"\0")
            # An unnamed event covers a watched directory itself or queue overflow.
            if not name or name in self.wake_names:
                return True
            offset += name_size
        return False

    def read(self, *, force=False):
        if self.fd is None:
            return None
        now = time.monotonic()
        source_changed = self._source_changed()
        if source_changed:
            # A spill or WAL frame write can precede visibility to the reader.
            self.recheck_at = now + 0.020
        if not (force or source_changed or now >= min(self.recheck_at, self.health_at)):
            return self.snapshot
        if now >= self.recheck_at:
            self.recheck_at = float("inf")
        if now >= self.health_at or source_changed:
            self._watch()
        self.health_at = now + 1  # Repair missed/replaced watches and detect missing source.
        try:
            self.snapshot = self._snapshot()
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            self.snapshot = None
        return self.snapshot

    def _snapshot(self):
        now = time.monotonic()
        if self.run == self.root and now >= self.check_handoff_at:
            owner = market_source(self.root, self.meta.slug)
            if owner != self.run:
                if self.latest is not None:
                    raise ValueError("M12 baseline owner changed after admission")
                self.run = owner
                self.path = owner / self.meta.slug / self.strategy / "simulation.sqlite3"
                self.database_id = None
                self.watermark = 0
                self._watch()
            self.check_handoff_at = now + 1
        p = self.path
        if p.resolve() != p or not p.is_file():
            return None
        with closing(sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=0.025)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            meta = dict(db.execute("SELECT name,payload FROM metadata WHERE name IN ('identity','run_owner')"))
            identity, owner = (json.loads(meta[k]) for k in ("identity", "run_owner"))
            database_id = identity["database_id"]
            if (not isinstance(database_id, str) or not database_id
                    or identity["market"] != self.meta.slug or identity["condition_id"] != self.meta.condition_id
                    or owner["mode"] != "SIMULATION_ONLY_PUBLIC_DATA" or owner["path"] != str(self.run)
                    or (self.database_id is not None and database_id != self.database_id)):
                raise ValueError("baseline identity changed")
            high, = db.execute("SELECT COALESCE(MAX(seq), 0) FROM audit").fetchone()
            if high < self.watermark:
                raise ValueError("baseline sequence regressed")
            latest = self.latest
            admissions = []
            for seq, inputs, raw in db.execute(
                "SELECT seq,input_seq,payload FROM audit WHERE seq>? AND kind='INTENT' ORDER BY seq",
                (self.watermark,),
            ):
                payload = json.loads(raw)
                if payload["scenario"] != "sop/anchor":
                    continue
                i = payload["intent"]
                if (i["market"], i["strategy"], i["profile"], i["side"], i["order_type"]) != (
                        self.meta.slug, self.strategy, self.strategy, "BUY", "ASSUMED_FIXED_ANCHOR"):
                    raise ValueError("foreign admission")
                if i["signal_version"] != self.version:
                    raise ValueError("unsupported baseline signal version")
                latest = {**i, "version": i["signal_version"], "root_id": i["opportunity"],
                          "audit_seq": seq, "input_seq": inputs}
                admissions.append(latest)
            self.pending_admissions.extend(admissions)
            self.database_id, self.watermark, self.latest = database_id, high, latest
            rows = (latest,) if latest else ()
            return Snapshot("sqlite:" + database_id, high, rows,
                            frozenset(row["opportunity"] for row in rows), time.monotonic())
