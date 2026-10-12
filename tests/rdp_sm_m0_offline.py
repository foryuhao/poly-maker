"""Run selected M0 evidence with empty credentials/CWD and denied network I/O.

Invoke with the repository venv Python. No package installation or live setup.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

NETWORK_ATTEMPTS = []


def deny_network(*_args, **_kwargs):
    NETWORK_ATTEMPTS.append(True)
    raise AssertionError("M0 forbids network/DNS access")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    # Do this BEFORE pytest/production imports; no inherited trading settings.
    retained = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    os.environ.clear()
    os.environ.update(retained)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    def protect_credentials(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).absolute()
            if path == root / ".env" or root / ".aws" in path.parents:
                raise AssertionError("M0 forbids reading workspace credentials")

    sys.addaudithook(protect_credentials)
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, deny_network)
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, name, deny_network)
    sys.path[:0] = [str(root), str(root / "src")]
    import pytest

    args = sys.argv[1:] or ["tests/test_rdp_sm_m0_execution.py", "-q"]
    args = [str(root / arg) if arg.startswith("tests/") else arg for arg in args]
    with tempfile.TemporaryDirectory(prefix="rdp-sm-m0-offline-") as cwd:
        os.chdir(cwd)  # default Config() cannot read the workspace .env
        result = pytest.main(["-p", "pytest_asyncio.plugin", "-p", "no:cacheprovider", *args])
    if NETWORK_ATTEMPTS:
        print(f"M0 NETWORK VIOLATION: {len(NETWORK_ATTEMPTS)} denied attempts")
        return 1
    print("M0 offline guard: 0 network/DNS attempts; workspace credentials denied")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
