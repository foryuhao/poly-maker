"""Durable market opening and account-wide reservations for the Taker runner."""

import json
import sqlite3
import time
import tomllib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_state import M4Ledger, decimal
from polymaker.m4_types import TAKER_LEDGER_IDS

POLICY = "SHARED_TAKER_POOL_BEFORE_T240_CLOSE_PLUS_120_V2"
ADMISSION_CUTOFF_SEC = 240
STRATEGIES = TAKER_LEDGER_IDS


def membership(project):
    directory = Path(project) / "config/m4-live"
    names = []
    # The prior runner hot-reads strategies.toml and rejects unfamiliar names.
    # Put additions for the new runner in a separate list during cutover.
    for filename in ("strategies.toml", "strategies-additions.toml"):
        path = directory / filename
        if not path.is_file():
            continue
        raw = tomllib.loads(path.read_text())
        enabled = raw.get("enabled")
        if (set(raw) != {"enabled"} or not isinstance(enabled, list)
                or any(not isinstance(name, str) or name not in STRATEGIES for name in enabled)):
            raise ValueError("TAKER_POOL_MEMBERSHIP_INVALID")
        names.extend(enabled)
    if len(set(names)) != len(names):
        raise ValueError("TAKER_POOL_MEMBERSHIP_INVALID")
    return tuple(STRATEGIES[name] for name in names)


def account_cash(account):
    cash = min(decimal(account[k]) for k in ("official_balance_usd", "applicable_allowance_usd"))
    if cash < 0:
        raise ValueError("TAKER_POOL_CAPITAL_INVALID")
    return cash


class TakerPool:
    def __init__(self, path, project):
        self.path, self.project = Path(path).absolute(), Path(project)
        if self.path != self.path.resolve() or self.path.is_symlink():
            raise ValueError("TAKER_POOL_PATH_INVALID")
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS market_openings (
                    funder TEXT NOT NULL, epoch INTEGER NOT NULL, release_at INTEGER NOT NULL,
                    released_at REAL, payload TEXT NOT NULL, PRIMARY KEY(funder, epoch));
                CREATE TABLE IF NOT EXISTS strategy_blocks (
                    funder TEXT NOT NULL, epoch INTEGER NOT NULL, strategy TEXT NOT NULL,
                    reason TEXT NOT NULL, blocked_at REAL NOT NULL,
                    PRIMARY KEY(funder, epoch, strategy));
            """)

    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def release(self, funder, official_lower):
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE market_openings SET released_at=? WHERE funder=? AND release_at<=? "
                "AND released_at IS NULL", (official_lower, funder, official_lower),
            )

    def existing(self, funder, epoch):
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM market_openings WHERE funder=? AND epoch=?",
                             (funder, epoch)).fetchone()
        return Opening(self, funder, epoch, json.loads(row[0])) if row else None

    def decide(self, funder, epoch, market_id, settings, account, *, clock, read_error=None,
               account_valid_until=None, read_retries=0):
        """Commit the first final decision atomically. A stale read can be retried."""
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            lower, upper = clock()
            if lower < epoch:
                raise ValueError("TAKER_POOL_BEFORE_T0")
            db.execute("UPDATE market_openings SET released_at=? WHERE funder=? AND release_at<=? "
                       "AND released_at IS NULL", (lower, funder, lower))
            existing = db.execute("SELECT payload FROM market_openings WHERE funder=? AND epoch=?",
                                  (funder, epoch)).fetchone()
            if existing:
                payload = json.loads(existing[0])
                if payload["market_id"] != market_id:
                    raise ValueError("TAKER_POOL_MARKET_IDENTITY_CONFLICT")
                return Opening(self, funder, epoch, payload)
            older = []
            # Five-minute markets and a 120-second tail can overlap only the
            # prior epoch. Read its legacy M4 ledger without migrating it.
            previous = epoch - 300
            for saved_epoch, expiry, payload in db.execute(
                "SELECT epoch, release_at, payload FROM market_openings "
                "WHERE funder=? AND epoch<? AND released_at IS NULL ORDER BY epoch", (funder, epoch)
            ):
                previous = None if saved_epoch == previous else previous
                saved = json.loads(payload)
                total = sum((decimal(v) for v in saved["reserved"].values()), decimal(0))
                if total:
                    older.append({"epoch": saved_epoch, "release_at": expiry, "reserved": str(total)})
            if previous is not None and lower < epoch + 120:
                path = self.path.parent / "ledgers" / f"btc-updown-5m-{previous}.sqlite3"
                try:
                    legacy = M4Ledger.metadata(path)
                    if legacy:
                        ident = legacy["identity"]
                        if (ident["funder"] != funder or ident["mode"] != "LIVE"
                                or legacy["market"]["slug"] != f"btc-updown-5m-{previous}"):
                            raise ValueError("TAKER_POOL_LEGACY_IDENTITY_CONFLICT")
                        older.append({"epoch": previous, "release_at": epoch + 120,
                                      "reserved": str(decimal(ident["limit"])), "legacy": True})
                except (OSError, ValueError, KeyError, sqlite3.Error):
                    read_error = "TAKER_POOL_LEGACY_RESERVATION_UNVERIFIED"
            requested = {key: str(cfg.taker_strategy.market_budget_usdc) for key, cfg in settings.items()}
            total = sum((decimal(v) for v in requested.values()), decimal(0))
            cash = account_cash(account) if account is not None else None
            available = max(decimal(0), cash - sum((decimal(p["reserved"]) for p in older), decimal(0))) if (
                cash is not None) else None
            # Recheck the clock and the account snapshot after acquiring the DB
            # lock and reading the previous market's reservation.
            _, upper = clock()
            expired = upper >= epoch + ADMISSION_CUTOFF_SEC
            if (not expired and read_error is None and account_valid_until is not None
                    and time.monotonic() >= account_valid_until):
                return None
            reason = ("TAKER_POOL_T240_DEADLINE" if expired
                      else read_error if read_error else "TAKER_POOL_NO_ENABLED_STRATEGIES" if not settings
                      else "TAKER_POOL_ACCOUNT_READ_UNAVAILABLE" if available is None
                      else "TAKER_POOL_INSUFFICIENT_FUNDS" if available <= total else None)
            payload = {"policy": POLICY, "market_id": market_id,
                       "status": "OPEN" if reason is None else "SKIPPED", "reason": reason,
                       "account": account, "available": str(available) if available is not None else None,
                       "older": older, "requested": requested, "reserved": requested if reason is None else {},
                       "decided_at": time.time(), "official_upper": upper,
                       "entry_cutoff_sec": ADMISSION_CUTOFF_SEC, "opening_read_retries": read_retries,
                       "settings": {key: cfg.model_dump(mode="json") for key, cfg in settings.items()}}
            db.execute("INSERT INTO market_openings VALUES (?,?,?,NULL,?)",
                       (funder, epoch, epoch + 420, json.dumps(payload, sort_keys=True, allow_nan=False)))
            return Opening(self, funder, epoch, payload)


@dataclass(frozen=True)
class Opening:
    pool: TakerPool
    funder: str
    epoch: int
    payload: dict

    def settings(self, strategy):
        saved = self.payload["settings"].get(strategy)
        return M4LiveSettings.model_validate(saved) if saved is not None else None

    def entry_block(self, strategy):
        if self.payload["status"] != "OPEN":
            return self.payload["reason"]
        if strategy not in self.payload["reserved"]:
            return "TAKER_STRATEGY_NOT_IN_OPENING"
        name = next(name for name, identity in STRATEGIES.items() if identity == strategy)
        reason = None
        try:
            if strategy not in membership(self.pool.project):
                reason = "TAKER_STRATEGY_REMOVED"
        except (ValueError, OSError):
            reason = "TAKER_POOL_MEMBERSHIP_UNAVAILABLE"
        if (self.pool.project / f"runtime/trading-control/{name.lower()}-strategy-stop.json").exists():
            reason = "TAKER_STRATEGY_STOPPED"
        with closing(self.pool.connect()) as db, db:
            if reason:
                db.execute("INSERT OR IGNORE INTO strategy_blocks VALUES (?,?,?,?,?)",
                           (self.funder, self.epoch, strategy, reason, time.time()))
            latched = db.execute("SELECT reason FROM strategy_blocks WHERE funder=? AND epoch=? AND strategy=?",
                                 (self.funder, self.epoch, strategy)).fetchone()
        return latched[0] if latched else None

    def strategy_cash(self, strategy, cash, elapsed):
        older = sum((decimal(p["reserved"]) for p in self.payload["older"]
                     if self.epoch + elapsed < p["release_at"]), decimal(0))
        others = sum((decimal(v) for key, v in self.payload["reserved"].items() if key != strategy), decimal(0))
        return max(decimal(0), cash - older - others)
