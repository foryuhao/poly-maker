import json
import os
import sqlite3

import pytest

from m12_follow.existing_wallet import existing_funder, existing_secrets
from polymaker.m4_types import M4_LEDGER, M21_LEDGER


FUNDER = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
SYNTHETIC_KEY = "01" * 32


def save_identity(project, lane, strategy, funder, mode="LIVE"):
    directory = project / "research_outputs/m4_live" / lane
    directory.mkdir(exist_ok=True, parents=True)
    with sqlite3.connect(directory / "btc-updown-5m-1795400400.sqlite3") as db:
        db.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT OR REPLACE INTO metadata VALUES('identity',?)", (json.dumps({
            "strategy": strategy, "mode": mode, "funder": funder}),))


def wallet_file(project, body):
    path = project / "legacy.env"
    path.write_text(body)
    path.chmod(0o600)
    return path


def test_reads_only_selected_wallet_and_connections(tmp_path, monkeypatch):
    save_identity(tmp_path, "ledgers", M4_LEDGER, FUNDER)
    save_identity(tmp_path, "m21-ledgers", M21_LEDGER, FUNDER)
    monkeypatch.setenv("PK", "another-account")
    monkeypatch.setenv("BROWSER_ADDRESS", OTHER)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://manager.invalid")
    path = wallet_file(tmp_path, f"PK={SYNTHETIC_KEY}\nPOLYMARKET_WALLET_ADDRESS='{FUNDER}'\n"
                       "ALL_PROXY=http://original.invalid\nHTTPS_PROXY=http://file.invalid\n"
                       "WALLET_TEST_UNRELATED=ignore-me\n")
    credentials = existing_secrets(tmp_path, path, existing_funder(tmp_path))
    assert credentials.pk == "0x" + SYNTHETIC_KEY and credentials.browser_address == FUNDER
    assert os.environ["ALL_PROXY"] == "http://original.invalid"
    assert os.environ["HTTPS_PROXY"] == "http://manager.invalid"
    assert os.environ["PK"] == "another-account" and os.environ["BROWSER_ADDRESS"] == OTHER
    assert "WALLET_TEST_UNRELATED" not in os.environ


def test_requires_two_original_live_lanes(tmp_path):
    save_identity(tmp_path, "ledgers", M4_LEDGER, FUNDER)
    with pytest.raises(ValueError, match="unavailable"):
        existing_funder(tmp_path)
    for strategy, mode, funder in ((M4_LEDGER, "LIVE", FUNDER),
                                   (M21_LEDGER, "VIRTUAL", FUNDER),
                                   (M21_LEDGER, "LIVE", OTHER)):
        save_identity(tmp_path, "m21-ledgers", strategy, funder, mode)
        with pytest.raises(ValueError):
            existing_funder(tmp_path)


def test_wallet_reuse_entrypoint_without_trial_window(tmp_path, monkeypatch):
    from m12_follow import live
    from polymaker import m4_run

    save_identity(tmp_path, "ledgers", M4_LEDGER, FUNDER)
    save_identity(tmp_path, "m21-ledgers", M21_LEDGER, FUNDER)
    (tmp_path / "research_outputs/m4_live/runner.lock").touch()
    config = tmp_path / "config/m4-live"
    config.mkdir(parents=True)
    (config / "balance-guard.toml").touch()
    path = wallet_file(tmp_path, f"PK={SYNTHETIC_KEY}\nBROWSER_ADDRESS={FUNDER}")
    monkeypatch.setenv("M12_FOLLOW_REUSE_EXISTING_WALLET_FILE", str(path))
    monkeypatch.delenv("M12_FOLLOW_TWO_MARKETS_FROM_EPOCH", raising=False)
    monkeypatch.chdir(tmp_path)
    called = []

    async def run(project, *, live, once, follow):
        assert live and not once and follow.trial_window is None
        assert follow.secrets().browser_address == FUNDER
        called.append(project)
        return {"status": "OFFLINE"}

    monkeypatch.setattr(m4_run, "run", run)
    live.main(["--live", "--source-dir", str(tmp_path / "channel"), "--run-id", "m12",
               "--existing-project", str(tmp_path)])
    assert called == [live.FRAMEWORK]


def test_wrong_wallet_key_or_permissions_fail_without_secret_output(tmp_path):
    save_identity(tmp_path, "ledgers", M4_LEDGER, FUNDER)
    save_identity(tmp_path, "m21-ledgers", M21_LEDGER, FUNDER)
    for body in (f"PK={SYNTHETIC_KEY}\nBROWSER_ADDRESS={OTHER}",
                 f"PK={SYNTHETIC_KEY}\nPOLYMARKET_PRIVATE_KEY={'aa' * 32}\nBROWSER_ADDRESS={FUNDER}",
                 f"PK=bad-secret\nBROWSER_ADDRESS={FUNDER}"):
        path = wallet_file(tmp_path, body)
        with pytest.raises(ValueError) as err:
            existing_secrets(tmp_path, path, FUNDER)
        assert SYNTHETIC_KEY not in str(err.value) and "bad-secret" not in str(err.value)
    path.chmod(0o606)
    with pytest.raises(ValueError, match="untrusted"):
        existing_secrets(tmp_path, path, FUNDER)
