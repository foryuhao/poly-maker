"""Verify and materialize a captured simulator source bundle without touching src."""

import hashlib
import json
import tarfile
from pathlib import Path


def materialize(source_run, cache):
    source_run, cache = Path(source_run), Path(cache)
    expected = json.loads((source_run / "launch/implementation.json").read_text())
    encoded = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    identity = hashlib.sha256(encoded).hexdigest()
    root = cache / identity[:16]
    if not root.exists():
        root.mkdir(parents=True)
        with tarfile.open(source_run / "launch/source.tar.gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.name not in {*expected, "pyproject.toml"}:
                    raise ValueError("UNEXPECTED_ARCHIVED_SOURCE:" + member.name)
                if Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                    raise ValueError("INVALID_ARCHIVED_PATH")
            archive.extractall(root, filter="data")
    actual = {str(p.relative_to(root)) for p in (root / "src/polymaker").rglob("*.py")}
    if actual != set(expected):
        raise ValueError("FROZEN_SOURCE_FILE_SET_MISMATCH")
    for name, wanted in expected.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != wanted:
            raise ValueError("FROZEN_SOURCE_HASH_MISMATCH:" + name)
    return root.resolve(), identity
