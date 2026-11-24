import fcntl
import json
import sqlite3

import pytest

from m12_follow.live import FollowConfig


OLD = "0x" + "1" * 40
NEW = "0x" + "2" * 40


def deployment(tmp_path):
    root = tmp_path / "research_outputs/m4_live"
    lane = root / "m421-ledgers"
    lane.mkdir(parents=True)
    (root / "runner.lock").touch()
    with sqlite3.connect(lane / "btc-updown-5m-1795400400.sqlite3") as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO metadata VALUES('identity', ?)", (json.dumps({"mode":"LIVE","funder": OLD}),))
    return root


def test_owned_lock_and_verified_wallet(tmp_path):
    root = deployment(tmp_path)
    with (root / "runner.lock").open("rb") as old:
        fcntl.flock(old, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with FollowConfig(tmp_path, "r", NEW, tmp_path, OLD).writer_ownership():
            pass
        with pytest.raises(ValueError, match="still owns"):
            with FollowConfig(tmp_path, "r", OLD, tmp_path, OLD).writer_ownership():
                pass
        with pytest.raises(ValueError, match="does not match"):
            with FollowConfig(tmp_path, "r", OLD, tmp_path, NEW).writer_ownership():
                pass


def test_empty_deployment_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="unavailable"):
        with FollowConfig(tmp_path, "r", NEW, tmp_path, OLD).writer_ownership():
            pass


def test_old_order_proofs_block_only_their_own_market(tmp_path):
    root = deployment(tmp_path)
    path, = (root / "m421-ledgers").glob("*.sqlite3")
    follow = FollowConfig(tmp_path, "run", OLD, tmp_path, OLD)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE opportunities(started, posts)")
        db.execute("INSERT INTO opportunities VALUES(1,1)")
        db.execute("CREATE TABLE taker_attempts(started,state,order_id,proof)")
        db.execute("INSERT INTO taker_attempts VALUES(1,'UNKNOWN','old1',NULL)")
    next_slug = "btc-updown-5m-1795400700"
    with follow.writer_ownership():
        assert follow.market_entry_block(next_slug) is None
        assert follow.market_entry_block(path.stem) == "OLD_MARKET_ORDER_UNRESOLVED"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE taker_attempts SET state='TERMINAL'")
    assert follow.market_entry_block(next_slug) is None
    assert follow.market_entry_block(path.stem) == "OLD_MARKET_ALREADY_TRADED"

    with sqlite3.connect(path) as db:
        db.execute("UPDATE taker_attempts SET state='UNSENT'")
    assert follow.market_entry_block(path.stem) == "OLD_MARKET_ORDER_UNRESOLVED"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE taker_attempts SET proof=?", (json.dumps({
            "kind": "LOCAL_NATIVE_POST_BLOCKED", "order_id": "old1", "native_post_sent": False}),))
    assert follow.market_entry_block(path.stem) is None

    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO metadata VALUES('market_halt_reason',?)", (json.dumps("SERIOUS"),))
    assert follow.market_entry_block(path.stem) == "OLD_MARKET_HALT"
    assert follow.market_entry_block(next_slug) is None


def test_foreign_history_is_never_opened_for_market_admission(tmp_path, monkeypatch):
    root = deployment(tmp_path)
    old, = (root / "m421-ledgers").glob("*.sqlite3")
    old.with_suffix(".halt.json").touch()
    follow = FollowConfig(tmp_path, "r", OLD, tmp_path, OLD)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("opened an unrelated ledger"))
    assert follow.market_entry_block("btc-updown-5m-1795400700") is None
    assert follow.market_entry_block(old.stem) == "OLD_MARKET_HALT"
