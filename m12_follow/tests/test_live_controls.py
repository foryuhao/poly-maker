import fcntl
import json
import sqlite3
from dataclasses import replace

import pytest

from m12_follow import live
from m12_follow.live import FollowConfig
from m12_follow.sqlite_source import BASELINE_VERSIONS
from polymaker.m4_control import StopControl
from polymaker.m4_run import paths, runner_ownership


OLD = "0x" + "1" * 40
NEW = "0x" + "2" * 40


@pytest.fixture(autouse=True)
def wallet_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "wallet_lock_directory", lambda: tmp_path / "wallet-locks")


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


def test_followers_isolate_instance_state_and_share_wallet_lock(tmp_path):
    deployment(tmp_path)
    first = FollowConfig(tmp_path, "r12", NEW, tmp_path, OLD, strategy="M12", instance_id="first",
                         state_dir=tmp_path / "first")
    second = replace(first, run_id="r21", funder=OLD, strategy="M21", instance_id="second",
                     state_dir=tmp_path / "second")
    with first.writer_ownership():
        with pytest.raises(ValueError, match="funding wallet lock"):
            with replace(second, funder=NEW).writer_ownership():
                pytest.fail("the second instance acquired the same wallet")
        with second.writer_ownership():
            first_root, second_root = (paths(f.state_dir, True) for f in (first, second))
            for root in (first_root, second_root):
                root.mkdir(parents=True)
            with runner_ownership(first_root, None), runner_ownership(second_root, None):
                first.bind_instance(first_root)
                second.bind_instance(second_root)
                with pytest.raises(ValueError, match="different instance or baseline"):
                    replace(second, state_dir=first.state_dir).bind_instance(first_root)
            first_control, second_control = (StopControl(tmp_path, live=True, state_dir=f.state_dir)
                                             for f in (first, second))
            first_control.manual_path.parent.mkdir(parents=True)
            first_control.manual_path.touch()
            assert first.stop_control(first_control).inhibited()
            assert not second.stop_control(second_control).inhibited()


def test_check_rejects_wrong_baseline_and_uses_one_policy(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("M12_FOLLOW_CHASE_FAK_EXTRA_TICKS", raising=False)
    checks = {}
    for strategy, version in BASELINE_VERSIONS.items():
        baseline = tmp_path / strategy
        baseline.mkdir()
        (baseline / "plan.json").write_text(json.dumps({
            "path": str(baseline), "schema": "m122124-shared-public-72h-v1",
            "mode": "SIMULATION_ONLY_PUBLIC_DATA", "strategy": strategy,
            "settings": {"strategy_definitions": {strategy: {"version": version}}},
        }))
        instance = tmp_path / f"state-{strategy}"
        options = ["--check", "--instance-id", strategy, "--state-dir", str(instance),
                   "--baseline-db-run", str(baseline)]
        live.main([*options, "--strategy", strategy])
        checks[strategy] = json.loads(capsys.readouterr().out)
        assert checks[strategy]["source"] == f"{strategy}_BASELINE_INTENT"
        assert checks[strategy]["state_dir"] == str(instance)
        assert checks[strategy]["chase_fak_extra_ticks"] == 5
        assert not instance.exists()
        with monkeypatch.context() as patch:
            patch.setenv("M12_FOLLOW_CHASE_FAK_EXTRA_TICKS", "0")
            live.main([*options, "--strategy", strategy])
            assert json.loads(capsys.readouterr().out)["chase_fak_extra_ticks"] == 0
            live.main([*options, "--strategy", strategy, "--chase-fak-extra-ticks", "1"])
            assert json.loads(capsys.readouterr().out)["chase_fak_extra_ticks"] == 1
        wrong_strategy = "M12" if strategy == "M21" else "M21"
        with pytest.raises(SystemExit) as error:
            live.main([*options, "--strategy", wrong_strategy])
        assert error.value.code == 2
        assert "does not belong to strategy" in capsys.readouterr().err
    for field in ("price_risk", "order_budget", "market_budget", "max_overpay_ticks"):
        assert checks["M12"][field] == checks["M21"][field]


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
