"""Lossless compression of repeated UNAVAILABLE diagnostics, preserving audit counts.

All other audit rows (signals, orders, fills, uncertainty, settlement) remain in
SQLite. Every relocated diagnostic remains in a gzip JSONL trail with input_seq.
Raw market recordings are unchanged. No existing run is rewritten or removed.
"""
import gzip
import hashlib
import os
from collections import Counter
from pathlib import Path

from m234_sim_main import MainEngine

from polymaker.m1234_sim.events import encode
from polymaker.m1234_sim.store import Store


class DiagnosticStore(Store):
    def __init__(self, path):
        super().__init__(path)
        self.archive_path = self.path.with_name("unavailable-diagnostics.jsonl.gz")
        self.archive_file = self.archive_path.open("xb")
        self.archive = gzip.GzipFile(fileobj=self.archive_file, mode="wb", compresslevel=1, mtime=0)
        self.diagnostic_counts = Counter()
        self.diagnostic_rows = 0

    def emit(self, kind, payload):
        if kind != "SIGNAL" or payload.get("event") != "UNAVAILABLE":
            return super().emit(kind, payload)
        row = {"input_seq": self.input_seq, "diagnostic_seq": self.diagnostic_rows + 1,
               "after_audit_seq": self.count,
               "kind": kind, "payload": payload}
        self.archive.write((encode(row) + "\n").encode())
        self.diagnostic_rows += 1
        fact = payload.get("payload", {})
        reason = fact.get("reason", "UNSPECIFIED")
        if reason is None:
            reason = "UNSPECIFIED"
        self.diagnostic_counts[(payload.get("strategy"), str(reason))] += 1

    def flush_archive(self, *, closed=False):
        if not closed:
            self.archive.flush()
            self.archive_file.flush()
            os.fsync(self.archive_file.fileno())
        record = {"file": self.archive_path.name, "rows": self.diagnostic_rows,
                  "input_seq": self.input_seq, "closed": closed,
                  "quality": [{"strategy": s, "reason": r, "count": n}
                              for (s, r), n in self.diagnostic_counts.items()]}
        if closed:
            record["sha256"] = hashlib.sha256(self.archive_path.read_bytes()).hexdigest()
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('diagnostic_archive',?)",
                        (encode(record),))

    def snapshot(self, name, payload):
        self.flush_archive()
        super().snapshot(name, payload)

    def commit(self):
        self.flush_archive()
        super().commit()

    def backup(self, path):
        self.flush_archive()
        super().backup(path)

    def close(self, *, commit=True):
        try:
            self.archive.close()
            self.archive_file.flush()
            os.fsync(self.archive_file.fileno())
            self.archive_file.close()
            if commit:
                self.flush_archive(closed=True)
        finally:
            super().close(commit=commit)


class ForwardEngine(MainEngine):
    def __init__(self, directory):
        super().__init__(directory)
        self.store.close(commit=False)
        self.store.path.unlink()  # This constructor's newly created empty DB only.
        self.store = DiagnosticStore(self.directory / "simulation.sqlite3")

    async def _ingest(self, event):
        if (event["kind"] == "INIT" and
                event["definition"]["settings"].get("audit_storage") != storage_definition()):
            raise ValueError("MAIN_STORAGE_DEFINITION_MISSING_OR_CHANGED")
        await super()._ingest(event)


def storage_definition():
    return {"revision": "lossless-unavailable-diagnostics-v1",
            "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sqlite": "all economic/candidate events; all diagnostics except SIGNAL UNAVAILABLE",
            "gzip": "every SIGNAL UNAVAILABLE payload with input_seq and archive sequence",
            "raw_market_data": "unchanged complete gzip recording",
            "quality_reports": "SQLite diagnostics plus committed archive counters"}
