"""Shared live credential aliases fail closed before wallet initialization."""

from __future__ import annotations

import pytest
from eth_account import Account

from polymaker.config import Config, ConfigAliasError, Secrets

KEY_A = "11" * 32
KEY_B = "22" * 32
WALLET_A = "0x" + "ab" * 20
WALLET_B = "0x" + "cd" * 20
NAMES = ("PK", "POLYMARKET_PRIVATE_KEY", "BROWSER_ADDRESS",
         "POLYMARKET_WALLET_ADDRESS")


@pytest.fixture(autouse=True)
def isolated_aliases(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", ["PK", "POLYMARKET_PRIVATE_KEY"])
def test_single_private_key_alias(name, monkeypatch):
    monkeypatch.setenv(name, "0x" + KEY_A)
    assert Account.from_key(Secrets().pk).address == Account.from_key(KEY_A).address


def test_equal_private_key_aliases(monkeypatch):
    monkeypatch.setenv("PK", "0x" + KEY_A)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + KEY_A)
    assert Secrets().pk == "0x" + KEY_A


def test_equivalent_private_key_representations(monkeypatch):
    monkeypatch.setenv("PK", "  " + KEY_A + "  ")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + KEY_A)
    assert Account.from_key(Secrets().pk).address == Account.from_key(KEY_A).address


def test_conflicting_private_key_aliases_do_not_leak_values(monkeypatch):
    monkeypatch.setenv("PK", "0x" + KEY_A)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + KEY_B)
    with pytest.raises(ConfigAliasError, match="CONFIG_ALIAS_CONFLICT") as error:
        Secrets()
    message = str(error.value)
    assert "PK and POLYMARKET_PRIVATE_KEY" in message
    assert KEY_A not in message and KEY_B not in message


@pytest.mark.parametrize("name", ["BROWSER_ADDRESS", "POLYMARKET_WALLET_ADDRESS"])
def test_single_wallet_alias(name, monkeypatch):
    monkeypatch.setenv(name, WALLET_A)
    assert Secrets().browser_address.lower() == WALLET_A.lower()


def test_equal_wallet_aliases(monkeypatch):
    monkeypatch.setenv("BROWSER_ADDRESS", WALLET_A)
    monkeypatch.setenv("POLYMARKET_WALLET_ADDRESS", WALLET_A)
    assert Secrets().browser_address.lower() == WALLET_A.lower()


def test_equivalent_wallet_case(monkeypatch):
    monkeypatch.setenv("BROWSER_ADDRESS", "  " + WALLET_A + "  ")
    monkeypatch.setenv("POLYMARKET_WALLET_ADDRESS", WALLET_A.upper().replace("0X", "0x"))
    assert Secrets().browser_address.lower() == WALLET_A.lower()


def test_conflicting_wallet_aliases(monkeypatch):
    monkeypatch.setenv("BROWSER_ADDRESS", WALLET_A)
    monkeypatch.setenv("POLYMARKET_WALLET_ADDRESS", WALLET_B)
    with pytest.raises(ConfigAliasError, match="CONFIG_ALIAS_CONFLICT") as error:
        Secrets()
    assert "BROWSER_ADDRESS and POLYMARKET_WALLET_ADDRESS" in str(error.value)


@pytest.mark.parametrize("name", ["BROWSER_ADDRESS", "POLYMARKET_WALLET_ADDRESS"])
def test_invalid_wallet_address_aborts(name, monkeypatch):
    monkeypatch.setenv(name, "not-an-address")
    with pytest.raises(ConfigAliasError, match="CONFIG_INVALID_ADDRESS") as error:
        Secrets()
    assert "not-an-address" not in str(error.value)


@pytest.mark.parametrize("conflict", ["key", "wallet"])
def test_cross_product_conflict_precedes_wallet_initialization(conflict, monkeypatch):
    monkeypatch.setenv("PK", "0x" + KEY_A)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + (KEY_B if conflict == "key" else KEY_A))
    monkeypatch.setenv("BROWSER_ADDRESS", WALLET_A)
    monkeypatch.setenv("POLYMARKET_WALLET_ADDRESS",
                       WALLET_B if conflict == "wallet" else WALLET_A)
    with pytest.raises(ConfigAliasError, match="CONFIG_ALIAS_CONFLICT"):
        Config.load(load_env=False)


def test_environment_and_dotenv_alias_conflict(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("POLYMARKET_PRIVATE_KEY=0x" + KEY_B + "\n")
    monkeypatch.setenv("PK", "0x" + KEY_A)
    with pytest.raises(ConfigAliasError, match="CONFIG_ALIAS_CONFLICT"):
        Config.load(load_env=False)


def test_cli_conflict_stops_before_settlement_preflight(monkeypatch):
    from typer.testing import CliRunner

    import polymaker.settlement_sweep as settlement
    from polymaker.cli import app

    monkeypatch.setenv("PK", "0x" + KEY_A)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + KEY_B)
    monkeypatch.setattr(settlement, "preflight", lambda *args, **kwargs: pytest.fail(
        "settlement preflight reached after credential conflict"))
    result = CliRunner().invoke(app, ["settlement-sweep", "--preflight-only"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigAliasError)
    assert KEY_A not in result.output and KEY_B not in result.output
