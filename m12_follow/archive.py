"""Track old follower ledgers with orders still owned by this runner."""

import asyncio
from contextlib import closing
import sqlite3


LANES = ("ledgers", "m421-ledgers")
OWNED_PENDING = "SELECT 1 FROM taker_attempts WHERE started!=0 AND state NOT IN ('TERMINAL','UNSENT') LIMIT 1"


def epoch(slug):
    prefix = "btc-updown-5m-"
    suffix = slug.removeprefix(prefix)
    if suffix == slug or not suffix.isdecimal():
        raise ValueError("M12_FOLLOW_ARCHIVE_MARKET_INVALID")
    return int(suffix)


def has_pending(path):
    if path.is_symlink() or path.resolve() != path:
        raise ValueError("M12_FOLLOW_ARCHIVE_LEDGER_REDIRECTED")
    if not path.exists():
        return False
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
        db.execute("PRAGMA query_only=ON")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='taker_attempts'").fetchone():
            return False  # The ledger had not reached order tracking initialization.
        return db.execute(OWNED_PENDING).fetchone() is not None


class PendingArchive:
    def __init__(self, root):
        self.root = root
        self.candidates = {}
        self.discovered = False

    def register_market(self, slug):
        start = epoch(slug)
        for lane in LANES:
            self.candidates[self.root / lane / f"{slug}.sqlite3"] = start

    def ready(self, slug):
        end = epoch(slug) - 300
        return not self.discovered or any(start <= end for start in self.candidates.values())

    def discard(self, path):
        self.candidates.pop(path, None)

    def discover(self, end):
        candidates = {}
        for lane in LANES:
            for path in (self.root / lane).glob("btc-updown-5m-*.sqlite3"):
                try:
                    start = epoch(path.stem)
                except ValueError:
                    continue
                if start > end:
                    continue
                try:
                    if not has_pending(path):
                        continue
                except (OSError, sqlite3.Error, ValueError):
                    pass  # The coordinator will report the failing ledger and retry it.
                candidates[path] = start
        return candidates

    async def paths(self, slug):
        end = epoch(slug) - 300
        if not self.discovered:
            self.candidates.update(await asyncio.to_thread(self.discover, end))
            self.discovered = True
        return sorted(path for path, start in self.candidates.items() if start <= end)
