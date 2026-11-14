"""Lossless compression of completed baseline journals and bounded report inputs."""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import closing
from pathlib import Path

from polymaker.m4_control import atomic_json

DEFAULT_POLICY = {
    "journal_compress_after_sec": 3600,
    "report_detail_retention_sec": 86400,
    "max_raw_journal_bytes": 128 * 1024**2,
    "max_single_journal_bytes": 16 * 1024**2,
    "max_archive_bytes": 2 * 1024**3,
    "min_free_disk_bytes": 5 * 1024**3,
}
REPORT_DETAILS = {
    "snapshot.json",
    "snapshot.log",
    "render.log",
    "market-pnl.json",
    "market-pnl-coverage.csv",
}


def file_identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("M4_MAINTENANCE_REGULAR_FILE_REQUIRED")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def compress_verified(path, archive):
    """Only unlink the unchanged source after its gzip has passed a full checksum."""
    before = file_identity(path)
    target = archive / (path.name + ".gz")
    with path.open("rb") as source:
        expected = digest(source)
    if not target.exists():
        fd, temporary = tempfile.mkstemp(prefix=".compress-", dir=archive)
        try:
            with os.fdopen(fd, "wb") as output:
                with (
                    gzip.GzipFile(
                        filename="", fileobj=output, mode="wb", compresslevel=6, mtime=0
                    ) as packed,
                    path.open("rb") as source,
                ):
                    shutil.copyfileobj(source, packed)
                output.flush()
                os.fsync(output.fileno())
            if file_identity(path) != before:
                raise ValueError("M4_JOURNAL_CHANGED_DURING_COMPRESSION")
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    file_identity(target)
    with gzip.open(target, "rb") as packed:
        if digest(packed) != expected:
            raise ValueError("M4_COMPRESSED_JOURNAL_CHECKSUM_MISMATCH")
    if file_identity(path) != before:
        raise ValueError("M4_JOURNAL_CHANGED_DURING_COMPRESSION")
    for directory in [archive, archive.parent]:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    compressed_bytes = target.stat().st_size
    path.unlink()
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {
        "source": str(path),
        "archive": str(target),
        "sha256": expected,
        "raw_bytes": before[2],
        "compressed_bytes": compressed_bytes,
    }


def eligible_journal(path, runtime, cutoff, active_epoch, open_inodes):
    info = file_identity(path)
    if info[:2] in open_inodes or info[3] / 1e9 > cutoff:
        return False
    with path.open() as stream:
        header = json.loads(stream.readline(131073))
    payload = header["data"]["payload"]
    session_id = header["data"]["session_id"]
    slug = payload["slug"]
    if (
        header["kind"] != "sm_session"
        or payload["mode"] != "VIRTUAL"
        or not re.fullmatch(r"m4_virtual_[0-9a-f]{32}", session_id)
        or not re.fullmatch(r"btc-updown-5m-\d{10}", slug)
        or not path.name.endswith("_" + session_id + ".jsonl")
    ):
        raise ValueError("M4_BASELINE_JOURNAL_IDENTITY_REQUIRED")
    epoch = int(slug.rsplit("-", 1)[1])
    if epoch >= active_epoch or epoch + 300 > cutoff:
        return False
    ledger = runtime / "ledgers" / (slug + ".sqlite3")
    if ledger.resolve() != ledger or not ledger.is_file():
        return False
    with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as database:
        database.execute("BEGIN")
        metadata = {
            key: json.loads(value)
            for key, value in database.execute("SELECT key,value FROM metadata")
        }
    settlement = metadata.get("modeled_settlement") or {}
    resolution = settlement.get("resolution") or {}
    return (
        metadata.get("complete") is True
        and metadata.get("session_id") == session_id
        and metadata["identity"].get("mode") == "VIRTUAL"
        and metadata["identity"].get("market_id") == payload["market_id"]
        and resolution.get("closed") is True
        and resolution.get("resolution_status") == "resolved"
    )


def maintain(project, state_dir, policy, active_pid, *, now=None):
    project, state_dir = Path(project).resolve(), Path(state_dir).resolve()
    runtime = project / "research_outputs/m4_virtual"
    if runtime.resolve() != runtime:
        raise ValueError("M4_BASELINE_DIRECTORY_MUST_BE_CANONICAL")
    now = time.time() if now is None else now
    active_epoch = int(now // 300) * 300
    policy = {**DEFAULT_POLICY, **policy}
    if any(type(value) is not int or value <= 0 for value in policy.values()):
        raise ValueError("M4_LOG_MAINTENANCE_POLICY_INVALID")
    result = {
        "observed_at": now,
        "policy": policy,
        "compressed": [],
        "removed_report_details": [],
        "warnings": [],
    }
    with (runtime / ".cleanup.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        opened = set()
        if active_pid and str(active_pid) != "0":
            for descriptor in Path("/proc", str(active_pid), "fd").iterdir():
                try:
                    info = descriptor.stat()
                    opened.add((info.st_dev, info.st_ino))
                except FileNotFoundError:
                    continue
        archive = runtime / "journal-archive"
        archive.mkdir(exist_ok=True)
        if archive.resolve() != archive:
            raise ValueError("M4_ARCHIVE_DIRECTORY_MUST_BE_CANONICAL")
        for path in sorted((runtime / "journal").glob("*.jsonl")):
            try:
                if eligible_journal(
                    path, runtime, now - policy["journal_compress_after_sec"], active_epoch, opened
                ):
                    result["compressed"].append(compress_verified(path, archive))
            except (OSError, ValueError, KeyError, sqlite3.Error) as error:
                result["warnings"].append({"path": str(path), "error_type": type(error).__name__})
        latest_path = state_dir / "latest.json"
        latest_directory = None
        if latest_path.exists():
            latest_directory = json.loads(latest_path.read_text()).get("directory")
        for directory in sorted((state_dir / "reports").iterdir()):
            if (
                directory.is_symlink()
                or not directory.is_dir()
                or str(directory) == latest_directory
            ):
                continue
            record = directory / "report.json"
            if not record.is_file() or record.is_symlink():
                continue
            report = json.loads(record.read_text())
            if (
                report.get("directory") != str(directory)
                or report.get("status") not in {"OK", "ATTENTION"}
                or "PNL_REPORT_GENERATION_FAILED" in report.get("problems", [])
                or report.get("observed_at", now) >= now - policy["report_detail_retention_sec"]
            ):
                continue
            for name in REPORT_DETAILS:
                path = directory / name
                if path.exists():
                    identity = file_identity(path)
                    if identity[3] / 1e9 < now - policy["report_detail_retention_sec"]:
                        path.unlink()
                        result["removed_report_details"].append(
                            {"path": str(path), "bytes": identity[2]}
                        )
        raw_files = list((runtime / "journal").glob("*.jsonl"))
        result["raw_journal_bytes"] = sum(path.stat().st_size for path in raw_files)
        result["archive_bytes"] = sum(path.stat().st_size for path in archive.glob("*.gz"))
        result["free_disk_bytes"] = shutil.disk_usage(project).free
        if result["raw_journal_bytes"] > policy["max_raw_journal_bytes"]:
            result["warnings"].append({"reason": "M4_RAW_JOURNAL_VOLUME_HIGH"})
        if any(path.stat().st_size > policy["max_single_journal_bytes"] for path in raw_files):
            result["warnings"].append({"reason": "M4_SINGLE_JOURNAL_SIZE_HIGH"})
        if result["archive_bytes"] > policy["max_archive_bytes"]:
            result["warnings"].append({"reason": "M4_ARCHIVE_VOLUME_HIGH"})
        if result["free_disk_bytes"] < policy["min_free_disk_bytes"]:
            result["warnings"].append({"reason": "M4_DISK_SPACE_LOW"})
        atomic_json(state_dir / "log-maintenance.json", result)
    return result
