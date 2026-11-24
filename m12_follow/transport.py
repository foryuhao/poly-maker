"""Same-host M12 baseline intent handoff. Only the producer opens files for writing."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


SCHEMA = "m12-baseline-live-intents-v1"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
HEARTBEAT_S = 0.020
MAX_AGE_S = 0.100
MAX_BYTES = 64 * 1024


def _identity(*parts):
    if not all(isinstance(part, str) and part for part in parts):
        raise ValueError("missing M12 source identity")
    raw = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def stable_id(run_id, market, version, opportunity):
    return "baseline-m12:" + _identity(run_id, market, version, opportunity)


def _stream(directory, run_id, market, condition_id):
    return Path(directory) / _identity(run_id, market, condition_id)


def _checked_intent(item):
    fields = ("opportunity", "root_id", "version", "token", "outcome")
    if not all(isinstance(item[key], str) and item[key] for key in fields):
        raise ValueError("invalid M12 intent identity")
    if item["outcome"] not in {"YES", "NO"}:
        raise ValueError("invalid M12 intent side")
    price, qty = Decimal(str(item["price"])), Decimal(str(item["quantity"]))
    if not (price.is_finite() and 0 < price < 1 and qty.is_finite() and qty > 0):
        raise ValueError("invalid M12 reference economics")
    clocks = tuple(float(item[key]) for key in ("confirmed", "available", "deadline"))
    if not all(map(math.isfinite, clocks)) or not (0 <= clocks[0] <= clocks[1] < clocks[2]):
        raise ValueError("invalid M12 validity window")
    return {**{key: item[key] for key in fields}, "price": str(price), "quantity": str(qty),
            "confirmed": clocks[0], "available": clocks[1], "deadline": clocks[2]}


@dataclass(frozen=True)
class Snapshot:
    generation: str
    input_seq: int
    intents: tuple[dict, ...]
    active_ids: frozenset[str]
    source_mono: float


class Producer:
    def __init__(self, directory, *, run_id, market, condition_id, tokens=None):
        self.prefix = _stream(directory, run_id, market, condition_id)
        self.prefix.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        permissions = self.prefix.parent.stat()
        if permissions.st_uid != os.geteuid() or permissions.st_mode & 0o022:
            raise PermissionError("M12 channel must be producer-owned and not group/world writable")
        self.identity = [run_id, market, condition_id]
        self.generation = uuid.uuid4().hex
        self.path = self.prefix.with_suffix(".json")
        self.lease = self.prefix.with_suffix("." + self.generation + ".lease")
        self.lease_file = None
        self.closed = False
        self.sequence = -1
        self.last_write = 0.0
        self.previous = None
        self.seen = {}
        self.retired = set()
        self.tokens = dict(tokens) if tokens is not None else None
        try:
            # Keep one permanent lock per channel, not one per five-minute market.
            # It serializes lease creation only, never frame or heartbeat processing.
            with (self.path.parent / ".publishers.lock").open("ab") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._reap_abandoned()
                self.lease_file = self.lease.open("xb")
                fcntl.flock(self.lease_file, fcntl.LOCK_EX)
                os.fchmod(self.lease_file.fileno(), 0o640)
            self._write((), (), False, 0.0)
        except BaseException:
            self.close()
            raise

    def _reap_abandoned(self):
        for lease in self.path.parent.glob("*.lease"):
            parts = lease.name.split(".")
            if (len(parts) != 3 or len(parts[0]) != 64 or len(parts[1]) != 32
                    or any(c not in "0123456789abcdef" for c in parts[0] + parts[1])):
                continue
            prefix = lease.parent / parts[0]
            try:
                with lease.open("rb") as other:
                    try:
                        fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError:
                        if prefix == self.prefix:
                            raise BlockingIOError("M12 market already has a live publisher")
                        continue
                    # No new lease can have been created for this prefix before
                    # the old lease was collected under the channel lock.
                    prefix.with_suffix(".json").unlink(missing_ok=True)
                    for temp in prefix.parent.glob(prefix.name + ".tmp.*"):
                        temp.unlink(missing_ok=True)
                    lease.unlink(missing_ok=True)
            except FileNotFoundError:
                continue  # The owner just released its lease during market close.

    def _write(self, intents, active, healthy, source_mono):
        now = time.monotonic()
        data = {"schema": SCHEMA, "identity": self.identity, "boot_id": BOOT_ID,
                "generation": self.generation, "seq": self.sequence, "source_mono": source_mono,
                "published_mono": now, "healthy": healthy, "intents": intents, "active": active}
        raw = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("oversized M12 snapshot")
        # A heartbeat is a liveness notification, not the durable trading ledger.
        # The private temporary file makes partial read/replace impossible.
        fd, temp = tempfile.mkstemp(prefix=self.prefix.name + ".tmp.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as file:
                os.fchmod(file.fileno(), 0o640)
                file.write(raw)
            os.replace(temp, self.path)
        finally:
            Path(temp).unlink(missing_ok=True)
        self.last_write = now

    def publish(self, input_seq, intents, active_ids, *, healthy, source_mono):
        if self.closed:
            raise RuntimeError("M12 producer closed")
        try:
            if type(input_seq) is not int or input_seq < 0 or input_seq <= self.sequence:
                raise ValueError("M12 input sequence did not advance")
            if type(healthy) is not bool or not math.isfinite(source_mono):
                raise ValueError("invalid M12 source health")
            active = tuple(sorted(active_ids))
            # Baseline supplies a complete market state. At most YES and NO can be admitted.
            rows = tuple(_checked_intent(item) for item in intents)
            by_id = {item["opportunity"]: item for item in rows}
            if (len(rows) > 2 or len(by_id) != len(rows)
                    or len({item["outcome"] for item in rows}) != len(rows)
                    or len(active) != len(set(active)) or not set(active).issubset(by_id)
                    or any(by_id.get(key) != value for key, value in self.seen.items())
                    or any(item["available"] > source_mono for item in rows)):
                raise ValueError("inconsistent M12 market intent snapshot")
            if self.tokens is not None and any(self.tokens.get(r["outcome"]) != r["token"] for r in rows):
                raise ValueError("M12 market token mismatch")
            if self.retired.intersection(active):
                raise ValueError("ended M12 intent became active again")
            self.retired.update(set(by_id).difference(active))
            self.seen = by_id
            self.sequence = input_seq
            state = (rows, active, healthy)
            if self.previous != state or time.monotonic() - self.last_write >= HEARTBEAT_S:
                self._write(rows, active, healthy, source_mono)
                self.previous = state
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.lease_file is not None:
            try:
                # Remove the snapshot while the lease is still held. The next
                # generation can create its snapshot after this lease is removed.
                self.path.unlink(missing_ok=True)
                self.lease.unlink(missing_ok=True)
            except OSError:
                pass  # Lease release below invalidates it; the next producer reaps it.
            finally:
                self.lease_file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Reader:
    def __init__(self, directory, *, run_id, market, condition_id):
        self.prefix = _stream(directory, run_id, market, condition_id)
        self.path = self.prefix.with_suffix(".json")
        self.identity = [run_id, market, condition_id]
        self.lock = threading.Lock()
        self.watermark = None

    def read(self):
        """Current eligible source state, or None. A reader never creates any files."""
        with self.lock:
            try:
                directory = self.prefix.parent.stat()
                if directory.st_mode & 0o022:
                    return None
                with self.path.open("rb") as f:
                    metadata = os.fstat(f.fileno())
                    if not self._trusted_file(metadata, directory):
                        return None
                    raw = f.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    return None
                data = json.loads(raw)
                if (data["schema"] != SCHEMA or data["boot_id"] != BOOT_ID
                        or data["identity"] != self.identity or data["healthy"] is not True):
                    return None
                generation = data["generation"]
                if uuid.UUID(hex=generation).hex != generation:
                    return None
                seq = data["seq"]
                if type(seq) is not int or seq < 0:
                    return None
                if self.watermark and self.watermark[0] == generation and seq < self.watermark[1]:
                    return None
                with self.prefix.with_suffix("." + generation + ".lease").open("rb") as lease:
                    if not self._trusted_file(os.fstat(lease.fileno()), directory):
                        return None
                    try:
                        fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass  # This particular generation still owns the exclusive lease.
                    else:
                        return None
                    now = time.monotonic()
                    source, published = data["source_mono"], data["published_mono"]
                    if not (now - MAX_AGE_S <= source <= published <= now):
                        return None
                    rows = tuple(_checked_intent(item) for item in data["intents"])
                    by_id = {item["opportunity"]: item for item in rows}
                    active = frozenset(data["active"])
                    if (len(rows) > 2 or len(by_id) != len(rows) or not active.issubset(by_id)
                            or any(r["available"] > source for r in rows)):
                        return None
                    self.watermark = (generation, seq)
                    active = frozenset(key for key in active if now < by_id[key]["deadline"])
                    return Snapshot(generation, seq, rows, active, source)
            except (OSError, KeyError, ValueError, TypeError, AttributeError, InvalidOperation, OverflowError):
                return None

    @staticmethod
    def _trusted_file(metadata, directory):
        return (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == directory.st_uid
                and not metadata.st_mode & 0o022)
