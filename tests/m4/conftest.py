"""M4 acceptance isolation: synthetic state and no real network access."""
import socket
import sys
from pathlib import Path

import pytest


def pytest_sessionstart(session):
    here = Path(__file__).resolve().parent
    if session.config.rootpath == here:
        for argument in session.config.args:
            path = Path(argument.split('::', 1)[0]).resolve()
            if not path.is_relative_to(here):
                raise pytest.UsageError('M4 suite accepts only tests/m4 paths: '+str(path))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('M4 acceptance forbids network connections')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def pytest_collection_finish(session):
    # Apply the strict boundary only when using this suite's own config. A
    # separate repository-wide run may still collect the historical research.
    here = Path(__file__).resolve().parent
    if session.config.rootpath != here:
        return
    outside = [str(item.path) for item in session.items if not item.path.is_relative_to(here)]
    for module in tuple(sys.modules.values()):
        path = getattr(module, '__file__', None)
        if not isinstance(path, str):
            continue
        path = Path(path).resolve()
        if path.name.startswith('test_') and path.is_relative_to(here.parent) and not path.is_relative_to(here):
            outside.append(str(path))
    if outside:
        raise pytest.UsageError('Non-M4 test imported/collected: '+', '.join(sorted(set(outside))))
