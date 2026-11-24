"""Local, explicit reuse of the original M4/M21 funding identity and connection settings."""

import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from dotenv.parser import parse_stream


_PRIVATE_KEYS = ("PK", "POLYMARKET_PRIVATE_KEY")
_WALLETS = ("BROWSER_ADDRESS", "POLYMARKET_WALLET_ADDRESS")
_PROXIES = ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
            "all_proxy", "https_proxy", "http_proxy", "no_proxy")
_FIELDS = frozenset((*_PRIVATE_KEYS, *_WALLETS, *_PROXIES))


def _address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value) or not int(value, 16):
        raise ValueError("invalid existing M4/M21 funding address")
    return value.lower()


def existing_funder(project: Path) -> str:
    """Both original live lanes must identify the same nonzero wallet on this host."""
    from polymaker.m4_types import M4_LEDGER, M21_LEDGER

    root = project.resolve(strict=True) / "research_outputs/m4_live"
    addresses = set()
    for lane, strategy in (("ledgers", M4_LEDGER), ("m21-ledgers", M21_LEDGER)):
        path = max((root / lane).glob("btc-updown-5m-*.sqlite3"), default=None)
        if path is None or path != path.resolve() or not path.is_file():
            raise ValueError("existing M4/M21 wallet ledger unavailable")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        ident = json.loads(row[0]) if row else {}
        if ident.get("mode") != "LIVE" or ident.get("strategy") != strategy:
            raise ValueError("existing M4/M21 ledger identity mismatch")
        addresses.add(_address(ident.get("funder")))
    if len(addresses) != 1:
        raise ValueError("existing M4/M21 funding identities disagree")
    return addresses.pop()


def _wallet_fields(env_file: Path):
    # Never import the environment file wholesale or run it as shell code.
    # Paths explicitly chosen by the operator may be secure symlinks; open the
    # resolved regular file, checking the actual descriptor before reading it.
    if not env_file.is_absolute():
        raise ValueError("an absolute existing wallet configuration path is required")
    path = env_file.resolve(strict=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    fields = {}
    with os.fdopen(fd, encoding="utf-8") as stream:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in (0, os.geteuid()) or st.st_mode & 0o002:
            raise ValueError("untrusted existing wallet configuration")
        for binding in parse_stream(stream):
            if binding.key in _FIELDS and binding.value is not None:
                fields[binding.key] = binding.value.strip()
    return fields


def existing_secrets(project: Path, env_file: Path, expected_funder: str):
    """Read the selected wallet in memory. Only standard proxy variables are inherited."""
    from polymaker.config import Secrets

    expected = _address(expected_funder)
    if existing_funder(project) != expected:
        raise ValueError("requested wallet disagrees with original live ledgers")
    fields = _wallet_fields(env_file)
    wallets = {_address(fields[name]) for name in _WALLETS if name in fields}
    if wallets != {expected}:
        raise ValueError("existing wallet configuration disagrees with original live ledgers")

    keys = {fields[name].removeprefix("0x").lower() for name in _PRIVATE_KEYS if name in fields}
    if len(keys) != 1:
        raise ValueError("existing signing key missing or inconsistent")
    key = keys.pop()
    if (not re.fullmatch(r"[0-9a-f]{64}", key)
            or not 0 < int(key, 16) < 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141):
        raise ValueError("existing signing key is invalid")

    # Match the original load_dotenv(override=False) for the HTTP/WS clients.
    for name in _PROXIES:
        if name in fields:
            os.environ.setdefault(name, fields[name])
    # Both values are supplied by this file and validated above. Bypass
    # BaseSettings' fallback to ambient, possibly unrelated account credentials.
    return Secrets.model_construct(pk="0x" + key, browser_address=expected)
