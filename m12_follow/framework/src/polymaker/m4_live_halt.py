"""Market-local live buy inhibition, independent of SQLite and trading journals."""

import json
import os
import time
from pathlib import Path
from threading import RLock

from polymaker.m4_control import atomic_json


class MarketHalt:
    def __init__(self, ledger_path, slug, session_id=None):
        self.path = Path(ledger_path).with_suffix(".halt.json")
        self.slug, self.session_id = slug, session_id
        self.lock = RLock()

    @property
    def reason(self):
        # Existence is the latch. Empty/truncated/mismatched controls never reopen
        # a market, including a crash between creating the latch and its details.
        if not self.path.exists() and not self.path.is_symlink():
            return None
        try:
            if self.path.is_symlink() or self.path.resolve() != self.path.absolute():
                return "M4_MARKET_HALT_INVALID"
            row = json.loads(self.path.read_text())
            if (row["slug"] != self.slug or not isinstance(row["reason"], str)
                    or not row["reason"]):
                return "M4_MARKET_HALT_INVALID"
            return row["reason"]
        except (OSError, ValueError, KeyError, TypeError):
            return "M4_MARKET_HALT_UNREADABLE"

    def latch(self, reason):
        with self.lock:
            if self.reason is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            atomic_json(self.path, {
                "slug": self.slug, "session_id": self.session_id,
                "reason": str(reason), "created_at": time.time(),
                "scope": "MARKET", "buy_allowed": False,
                "recovery": "OWNED_ORDER_READS_ONLY",
            })
