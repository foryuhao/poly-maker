"""Lossless shared tapes, sealed SQLite ledgers and storage accounting."""

import hashlib
import io
import json
import os
import shutil
import subprocess
from contextlib import contextmanager, suppress
from pathlib import Path

from m1223_capture_recording import Recorder as BoundedRecorder

from polymaker.m1234_sim import INPUT_SCHEMA

GIB = 1024**3
RUN_BUDGET = 20 * GIB
FREE_RESERVE = 5 * GIB
STOP_HEADROOM = 256 * 1024**2
ZSTD = ("zstd", "-q", "--single-thread", "-6", "--long=24", "--check", "-c")


@contextmanager
def open_tape(path, _mode, *, encoding, compresslevel):
    # Flushing the Python stream feeds zstd without flushing its dictionary.
    with (
        Path(path).open("xb") as target,
        subprocess.Popen(ZSTD, stdin=subprocess.PIPE, stdout=target) as process,
    ):
        try:
            with io.TextIOWrapper(process.stdin, encoding=encoding) as source:
                yield source
            if process.wait(timeout=5):
                raise OSError("SHARED_TAPE_COMPRESSION_FAILED")
            target.flush()
            os.fsync(target.fileno())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


class Recorder(BoundedRecorder):
    def __init__(self, path, capacity):
        super().__init__(path, capacity, opener=open_tape)


def read_tape(path):
    with subprocess.Popen(
        ("zstd", "-q", "-dc", "--", str(path)), stdout=subprocess.PIPE, text=True, encoding="utf-8"
    ) as process:
        try:
            for seq, line in enumerate(process.stdout, 1):
                row = json.loads(line)
                if row.get("schema") != INPUT_SCHEMA or row.get("seq") != seq:
                    raise ValueError("SHARED_INPUT_SEQUENCE_OR_SCHEMA")
                yield row
            if process.wait():
                raise OSError("SHARED_TAPE_INCOMPLETE")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def verify_tape(path):
    subprocess.run(("zstd", "-q", "-t", "--", str(path)), check=True, timeout=30)
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def seal_ledger(path):
    """Atomically replace a closed SQLite file with a reversible, checked copy."""
    path = Path(path)
    sealed = path.with_suffix(".sqlite3.zst")
    temporary = sealed.with_suffix(".zst.tmp")
    wal = path.with_suffix(".sqlite3-wal")
    if sealed.exists() or (wal.exists() and wal.stat().st_size):
        raise ValueError("SHARED_LEDGER_NOT_READY_FOR_COMPACTION")
    try:
        with path.open("rb") as original:
            expected = hashlib.file_digest(original, "sha256").hexdigest()
        with temporary.open("xb") as output:
            subprocess.run((*ZSTD, "--", str(path)), stdout=output, check=True, timeout=30)
            output.flush()
            os.fsync(output.fileno())
        with subprocess.Popen(
            ("zstd", "-q", "-dc", "--", str(temporary)), stdout=subprocess.PIPE
        ) as check:
            actual = hashlib.file_digest(check.stdout, "sha256").hexdigest()
            if check.wait() or actual != expected:
                raise OSError("SHARED_LEDGER_ROUND_TRIP_FAILED")
        temporary.replace(sealed)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
            path.unlink()
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)
    return {"file": sealed.name, "sqlite_sha256": expected, "bytes": sealed.stat().st_size}


def allocated_bytes(directory):
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            # Active compaction can atomically rename or remove a file.
            with suppress(FileNotFoundError):
                total += (Path(root) / name).stat().st_blocks * 512
    return total


def storage_status(output_root, project_root, *, admission=False, used_bytes=0,
                   allow_storage_limited=False):
    output_free = shutil.disk_usage(output_root).free
    project_free = shutil.disk_usage(project_root).free
    output_reserve = (
        FREE_RESERVE if Path(output_root).stat().st_dev == Path(project_root).stat().st_dev else 0
    )
    if admission and not allow_storage_limited:
        required = RUN_BUDGET + output_reserve
        reason = (
            "PROJECT_DISK_BUDGET"
            if project_free < FREE_RESERVE
            else "OUTPUT_DISK_BUDGET"
            if output_free < required
            else None
        )
    else:
        required = output_reserve + STOP_HEADROOM
        reason = (
            "PROJECT_DISK_RESERVE"
            if project_free <= FREE_RESERVE + STOP_HEADROOM
            else "OUTPUT_DISK_RESERVE"
            if output_free <= required
            else "OUTPUT_BUDGET"
            if used_bytes >= RUN_BUDGET - STOP_HEADROOM
            else None
        )
    return dict(
        output_free_bytes=output_free,
        project_free_bytes=project_free,
        output_required_bytes=required,
        output_budget_bytes=RUN_BUDGET,
        available_run_headroom_bytes=max(
            0, min(RUN_BUDGET - STOP_HEADROOM, output_free - output_reserve - STOP_HEADROOM)
        ),
        project_reserve_bytes=FREE_RESERVE,
        stop_headroom_bytes=STOP_HEADROOM,
        used_bytes=used_bytes,
        reason=reason,
    )
