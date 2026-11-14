"""Read-only sample gates must close SQLite handles on success and failure."""

import json
import sqlite3

import m511_sample_quality as quality
import pytest


@pytest.mark.parametrize("reader,broken", [("own", False), ("own", True),
                                          ("audit", False), ("audit", True)])
def test_gate_closes_read_connection(tmp_path, monkeypatch, reader, broken):
    if reader == "own":
        path = tmp_path / "research_outputs/m4_live/ledgers/market.sqlite3"
    else:
        path = tmp_path / "audit.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        if not broken and reader == "own":
            connection.execute("CREATE TABLE metadata(key TEXT,value TEXT)")
            connection.execute("INSERT INTO metadata VALUES('identity',?)",
                               (json.dumps({"mode": "LIVE"}),))
            connection.execute("CREATE TABLE opportunities(token TEXT, posts INT, started REAL)")
        elif not broken:
            connection.execute("CREATE TABLE metadata(name TEXT,payload TEXT)")
            connection.execute("INSERT INTO metadata VALUES('research_sample_eligibility',?)",
                               (json.dumps({"status": "ELIGIBLE"}),))
        connection.commit()
    finally:
        connection.close()

    opened = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def tracked(*args, **kwargs):
        result = connect(*args, factory=TrackedConnection, **kwargs)
        opened.append(result)
        return result

    monkeypatch.setattr(quality.sqlite3, "connect", tracked)
    if reader == "audit" and broken:
        with pytest.raises(sqlite3.Error):
            quality.read_gate(path)
    else:
        result = quality.own_market_gate(tmp_path, "market") if reader == "own" else quality.read_gate(path)
        assert result["status"] == ("UNAVAILABLE" if broken else "ELIGIBLE")
    assert len(opened) == 1 and opened[0].closed
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_retirement_backup_closes_destination_connection(tmp_path, monkeypatch):
    from polymaker.m1234_sim import store as storage

    store = storage.Store(tmp_path / "source.sqlite3")
    store.snapshot("state", {"kept": "durable economic state"})
    connect, opened = sqlite3.connect, []

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def tracked(*args, **kwargs):
        result = connect(*args, factory=TrackedConnection, **kwargs)
        opened.append(result)
        return result

    monkeypatch.setattr(storage.sqlite3, "connect", tracked)
    try:
        store.backup(tmp_path / "snapshot.sqlite3")
        assert len(opened) == 1 and opened[0].closed
        destination = connect(tmp_path / "snapshot.sqlite3")
        try:
            assert destination.execute("SELECT payload FROM snapshots").fetchone()[0] == (
                '{"kept":"durable economic state"}')
        finally:
            destination.close()
    finally:
        store.close()
