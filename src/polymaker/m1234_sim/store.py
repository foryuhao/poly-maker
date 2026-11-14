"""Append-only simulation audit trail; no access to old research databases."""

import json
import sqlite3
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from uuid import uuid4

from polymaker.m1234_sim.events import encode, write_json
from polymaker.m1234_sim.validity import (
    database_validity,
    evidence_record,
    make_binding,
    tracker_path,
)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise ValueError("SIM_OUTPUT_EXISTS_USE_REPLAY_RECOVERY")
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE audit(seq INTEGER PRIMARY KEY, input_seq INTEGER NOT NULL,
                             kind TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE snapshots(name TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE metadata(name TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)
        self.input_seq = 0
        self.count = 0
        self.identity = None
        self.closed = False
        self.run_owner = None

    def bind(self, definition):
        self.identity = make_binding(definition, str(uuid4()))
        self.db.execute("INSERT INTO metadata VALUES('identity',?)", (encode(self.identity),))
        if self.run_owner is not None:
            self.db.execute("INSERT INTO metadata VALUES('run_owner',?)", (encode(self.run_owner),))
        self.db.commit()
        write_json(tracker_path(self.path, self.identity),
                   evidence_record(self.identity, 0, {"status": "TRACKING", "failures": []}))

    def record_failure(self, validity):
        if self.identity is None:
            raise ValueError("SIM_FAILURE_DATABASE_IDENTITY_UNAVAILABLE")
        record = evidence_record(self.identity, self.input_seq, validity)
        write_json(tracker_path(self.path, self.identity), record)

    def emit(self, kind, payload):
        self.db.execute(
            "INSERT INTO audit(input_seq,kind,payload) VALUES(?,?,?)",
            (self.input_seq, kind, encode(payload)),
        )
        self.count += 1

    def snapshot(self, name, payload):
        self.db.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?)", (name, encode(payload)))
        self.db.commit()

    def commit(self):
        self.db.commit()

    def backup(self, path):
        self.db.commit()
        with closing(sqlite3.connect(path)) as target, target:
            self.db.backup(target)
        if self.identity is not None and Path(path).parent.resolve() != self.path.parent.resolve():
            row = self.db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()
            state = json.loads(row[0])
            validity = database_validity(self.path, state)
            watermarks = [record["input_seq"] for record in validity["evidence"]["records"]]
            validity_state = {"status": "INVALID" if validity["status"] == "INVALID" else "TRACKING",
                              "failures": validity["failures"]}
            if validity["status"] == "VALID":
                record = json.loads(tracker_path(self.path, self.identity).read_text())
            else:
                record = evidence_record(self.identity, max([state["input_seq"], *watermarks]), validity_state)
            record["evidence_errors"] = validity["evidence"]["errors"]
            write_json(tracker_path(path, self.identity), record)

    def close(self, *, commit=True):
        try:
            if commit:
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            self.db.close()
            self.closed = True


@contextmanager
def read_transaction(path):
    db = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def read_audit(path, *, connection=None):
    with (read_transaction(path) if connection is None else nullcontext(connection)) as db:
        for seq, input_seq, kind, payload in db.execute("SELECT * FROM audit ORDER BY seq"):
            yield {"seq": seq, "input_seq": input_seq, "kind": kind, "payload": json.loads(payload)}
