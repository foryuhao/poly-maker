"""Bounded, restartable JSONL tailer; writes exclusively to its own output root."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from polymaker.dashboard.model import Record, compact, summarize

KINDS = {
    "sm_session", "sm_run_result", "sm_placement_result", "sm_trade", "sm_ba_taker_trade",
    "sm_network_exit_trade", "sm_ba_taker_post_returned", "sm_network_exit_post_returned",
    "sm_strategy_window", "sm_ba_execution_state", "sm_opposite_ba_episode", "sm_ba_episode",
    "sm_opposite_ba_execution", "sm_ca_normal_execution", "sm_ca_wait", "sm_obi_wait",
    "sm_quote_skipped", "sm_close_barrier", "sm_cancel_ack", "sm_merge_result", "sm_cooldown",
    "sm_network_transition", "sm_ba_taker_result", "sm_network_exit_result", "sm_ws_diagnostics",
    "sm_ba_maker_closed", "sm_ba_maker_stop_requested", "sm_ca_final_decision",
    "sm_ca_close_requested", "sm_restored_tp_ba_handoff", "sm_tp_ba_handoff_closed",
    "sm_repair_episode", "sm_ba_taker_state", "sm_network_exit_state",
    "sm_window_result",
}
MAX_LINE = 4 * 1024 * 1024
DIAGNOSTIC_PREFIX = re.compile(rb'^\{"ts": ([0-9.eE+\-]+), "kind": "(sm_sync_cache_read|sm_prepared)", "data": ')
WRITER_SEQUENCE = re.compile(rb'"writer_sequence": ([0-9]+),')
WRITER_DROPPED = re.compile(rb'"diagnostics_dropped": ([0-9]+)')


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def atomic_json(path: Path, value: object) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(dumps(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Collector:
    """One writer, short-lived read handles, bounded CPU and input per poll."""

    def __init__(self, project: Path, output: Path, *, history_hours: float = 24,
                 journal_dirs: list[Path] | None = None, budget_bytes: int = 8 * 1024 * 1024):
        self.project = project.resolve()
        self.output = output.resolve()
        self.journal_dirs = [p.resolve() for p in (journal_dirs or [self.project / "journal", self.project / "livecfg/journal"])]
        # Never let an output override write inside live logs, config, or the trading database.
        if (self.output == self.project or self.output in self.project.parents
                or any(self.output == p or p in self.output.parents for p in self.journal_dirs)
                or any(p in self.output.parents or self.output == p
                       for p in (self.project / "config", self.project / "livecfg", self.project / "logs", self.project / "src"))):
            raise ValueError("dashboard output must be a separate derived-data directory")
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = (self.output / "observer.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("an archive observer already owns this output directory") from None
        self.db = sqlite3.connect(self.output / "observer.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY, sid TEXT NOT NULL,
                kind TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(path TEXT NOT NULL, offset INTEGER NOT NULL,
                sid TEXT NOT NULL, event TEXT NOT NULL, PRIMARY KEY(path,offset));
            CREATE INDEX IF NOT EXISTS events_sid ON events(sid);
            CREATE TABLE IF NOT EXISTS summaries(sid TEXT PRIMARY KEY, snapshot TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settlements(sid TEXT PRIMARY KEY, evidence TEXT NOT NULL);
        """)
        self.history_hours = history_hours
        self.budget_bytes = budget_bytes
        self.last_discovery = 0.0
        self.round_robin = 0
        self.changed: set[str] = set()
        row = self.db.execute("SELECT value FROM metadata WHERE key='excluded_session_ids'").fetchone()
        self.excluded_session_ids = set(json.loads(row[0])) if row else set()
        # Refresh persisted summaries when the accounting classification changes.
        for row in self.db.execute("SELECT sid,snapshot FROM summaries").fetchall():
            cached = json.loads(row["snapshot"])
            if cached.get("pnl_attribution_version") != 2 or "post_counts" not in cached or "condition_id" not in cached:
                sid = row["sid"]
                result = summarize(sid, self.events(sid), self.sources(sid))
                self.db.execute("UPDATE summaries SET snapshot=? WHERE sid=?", (dumps(result), sid))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
        self.lock.close()

    def add_source(self, path: Path, sid: str, kind: str) -> None:
        existing = self.db.execute("SELECT sid FROM sources WHERE path=?", (str(path),)).fetchone()
        if existing and existing["sid"] != sid:
            raise ValueError("a CA source is referenced by multiple execution sessions")
        state = {"path": str(path), "kind": kind, "offset": 0, "seq": 0,
                 "errors": 0, "gaps": 0, "replaced": 0, "last_ts": 0,
                 "caught_up": False, "partial": False, "missing": False}
        self.db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?)", (str(path), sid, kind, dumps(state)))

    def discover(self, now: float) -> None:
        if now - self.last_discovery < 5:
            return
        self.last_discovery = now
        for directory in self.journal_dirs:
            try:
                paths = list(directory.glob("rdp_sm_*.jsonl"))
            except OSError:
                continue
            for path in paths:
                try:
                    if path.stem.rsplit("_", 1)[-1] in self.excluded_session_ids:
                        continue
                    if path.is_symlink() or path.stat().st_mtime < now - self.history_hours * 3600:
                        continue
                    self.add_source(path, path.stem.rsplit("_", 1)[-1], "risk")
                except FileNotFoundError:
                    continue

    def events(self, sid: str) -> list[Record]:
        result = [json.loads(row[0]) for row in self.db.execute("SELECT event FROM events WHERE sid=?", (sid,))]
        row = self.db.execute("SELECT evidence FROM settlements WHERE sid=?", (sid,)).fetchone()
        if row:
            evidence = json.loads(row[0])
            result.append({"kind": "dashboard_market_resolution", "source_kind": "resolution",
                           "payload": evidence, "ts": (evidence.get("result") or {}).get("observed_at", 0)})
        return result

    def sources(self, sid: str) -> list[Record]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT state FROM sources WHERE sid=?", (sid,))]

    def snapshots(self) -> list[Record]:
        result = [json.loads(row[0]) for row in self.db.execute("SELECT snapshot FROM summaries")]
        return sorted(result, key=lambda s: (s.get("market_start") or 0, s["session_id"]))

    def _ca_source(self, payload: Record, sid: str) -> None:
        runtime = payload.get("ca_observation", {}).get("runtime", {})
        path = runtime.get("writer", {}).get("path")
        if not path:
            path = runtime.get("snapshot", {}).get("writer", {}).get("path")
        if path:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.project / candidate
            # A header is evidence, never an arbitrary filesystem read instruction.
            candidate = candidate.resolve()
            if candidate.is_relative_to(self.project) and candidate.suffix == ".jsonl":
                self.add_source(candidate, sid, "ca")

    def _tail(self, row: sqlite3.Row, allowance: int, deadline: float) -> int:
        state = json.loads(row["state"])
        before = dumps(state)
        path, sid, kind = Path(row["path"]), row["sid"], row["kind"]
        used = 0
        try:
            # O_NOFOLLOW also prevents a replaced source from redirecting reads.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = [stat.st_dev, stat.st_ino]
                if state.get("identity") not in (None, identity) or stat.st_size < state["offset"]:
                    state.update(offset=0, seq=0, last_ts=0, replaced=state["replaced"] + 1)
                    self.db.execute("DELETE FROM events WHERE path=?", (str(path),))
                state.update(identity=identity, missing=False, partial=False, size=stat.st_size)
                stream.seek(state["offset"])
                while used < allowance and time.monotonic() < deadline:
                    offset = stream.tell()
                    line = stream.readline(MAX_LINE + 1)
                    if not line:
                        break
                    used += len(line)
                    if not line.endswith(b"\n"):
                        if len(line) > MAX_LINE:
                            state["errors"] += 1
                            state["oversize"] = True
                            state["offset"] = stream.tell()
                            continue
                        state["partial"] = True
                        break  # Retry from the last complete line on the next poll.
                    state["offset"] = stream.tell()
                    if state.pop("oversize", False):
                        continue
                    # These voluminous cache/preparation diagnostics are not trading
                    # evidence. Read their envelope without decoding nested payloads.
                    # Relevant order, fill, signal and barrier records still get full
                    # JSON validation; the skipped diagnostic count is explicit.
                    diagnostic = DIAGNOSTIC_PREFIX.match(line) if kind == "risk" else None
                    tail = line[-512:]
                    sequence = WRITER_SEQUENCE.search(tail) if diagnostic else None
                    dropped = WRITER_DROPPED.search(tail) if diagnostic else None
                    if diagnostic and sequence and dropped and line.endswith(b"}}\n"):
                        fast_seq = int(sequence[1])
                        self._sequence(state, fast_seq, kind)
                        state["last_ts"] = max(state["last_ts"], float(diagnostic[1]))
                        state["diagnostics_dropped"] = max(state.get("diagnostics_dropped", 0), int(dropped[1]))
                        state["diagnostic_payloads_not_decoded"] = state.get("diagnostic_payloads_not_decoded", 0) + 1
                        continue
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            raise ValueError("record is not an object")
                        if kind == "risk":
                            data = raw["data"]
                            if data.get("session_id") != sid:
                                raise ValueError("journal session mismatch")
                            seq = data.get("writer_sequence")
                            ts = float(raw["ts"])
                            name = raw["kind"]
                            payload = data["payload"]
                        else:
                            seq, ts = raw.get("sequence"), float(raw["observed_wall"])
                            name, payload = raw["event"], raw["payload"]
                        if not isinstance(payload, dict):
                            raise ValueError("payload is not an object")
                        if isinstance(seq, int):
                            self._sequence(state, seq, kind)
                        state["last_ts"] = max(state["last_ts"], ts)
                        if kind == "risk" and name == "sm_session":
                            self._ca_source(payload, sid)
                        dropped = raw.get("data", {}).get("diagnostics_dropped", 0)
                        state["diagnostics_dropped"] = max(state.get("diagnostics_dropped", 0), dropped)
                        if name in KINDS or kind == "ca" or (name == "sm_network_request" and payload.get("method") == "POST"):
                            event = {"kind": name, "payload": compact(name, payload), "ts": ts,
                                     "source_kind": kind, "offset": offset, "sequence": seq,
                                     "monotonic": raw.get("data", {}).get("occurred_monotonic", raw.get("observed_monotonic"))}
                            self.db.execute("INSERT OR REPLACE INTO events VALUES(?,?,?,?)",
                                            (str(path), offset, sid, dumps(event)))
                    except (ValueError, KeyError, TypeError, OverflowError):
                        state["errors"] += 1
                state["caught_up"] = state["offset"] >= stat.st_size or state["partial"]
        except OSError:
            state["missing"] = True
            state["caught_up"] = False
        if dumps(state) != before:
            self.db.execute("UPDATE sources SET state=? WHERE path=?", (dumps(state), str(path)))
            self.changed.add(sid)
        return used

    @staticmethod
    def _sequence(state: Record, seq: int, kind: str) -> None:
        if state["seq"] and seq != state["seq"] + 1:
            if kind == "ca" or seq <= state["seq"]:
                state["gaps"] += 1
            else:
                # flush consumes a sequence but emits no JSONL record.
                state["writer_sequence_holes"] = state.get("writer_sequence_holes", 0) + seq - state["seq"] - 1
        state["seq"] = seq

    def poll(self, now: float | None = None) -> Record:
        now = now if now is not None else time.time()
        self.changed.clear()
        self.discover(now)
        rows = list(self.db.execute("SELECT * FROM sources"))
        # Always prioritize the latest actual file, including during initial backfill.
        def modified(row: sqlite3.Row) -> float:
            try:
                return Path(row["path"]).stat().st_mtime
            except OSError:
                return 0

        newest = sorted((r for r in rows if r["kind"] == "risk"), key=modified, reverse=True)[:2]
        latest_ids = {r["sid"] for r in newest}
        priority = newest + [r for r in rows if r["kind"] == "ca" and r["sid"] in latest_ids]
        remainder = [r for r in rows if r not in priority]
        if remainder:
            index = self.round_robin % len(remainder)
            remainder = remainder[index:] + remainder[:index]
            self.round_robin += 1
        remaining, deadline = self.budget_bytes, time.monotonic() + .15
        for row in priority + remainder:
            if remaining <= 0 or time.monotonic() >= deadline:
                break
            used = self._tail(row, min(remaining, 1024 * 1024), deadline)
            remaining -= used
        for sid in self.changed:
            result = summarize(sid, self.events(sid), self.sources(sid))
            self.db.execute("INSERT OR REPLACE INTO summaries VALUES(?,?)", (sid, dumps(result)))
        self.db.commit()
        sessions = self.snapshots()
        source_states = [json.loads(row[0]) for row in self.db.execute("SELECT state FROM sources")]
        active = [s for s in sessions if s.get("market_start") is not None
                  and s["market_start"] <= now < s["market_end"]]
        current = max(active or sessions, key=lambda s: (s.get("market_start") or 0, s["last_event_at"]), default=None)
        return {"schema_version": 1, "observed_at": now, "pid": os.getpid(),
                "history_hours": self.history_hours, "sessions": sessions,
                "current_session_id": current["session_id"] if current else None,
                "source_count": self.db.execute("SELECT count(*) FROM sources").fetchone()[0],
                "catching_up_sources": sum(not s.get("caught_up") and not s.get("missing") for s in source_states),
                "isolation": "LOCAL_READ_ONLY; NO_CONFIG_KEYS_SDK_NETWORK_OR_TRADING_DB"}


def public_snapshot(snapshot: Record) -> Record:
    """Avoid rewriting every historical config and source manifest every second."""
    sessions = []
    for s in snapshot["sessions"]:
        s = {**s, "normal_entry_mode": s["header"].get("normal_entry_mode")}
        if s["session_id"] == snapshot.get("current_session_id"):
            sessions.append(s)
        else:
            sessions.append({k: v for k, v in s.items() if k not in {"header", "sources"}})
    return {**snapshot, "sessions": sessions}
