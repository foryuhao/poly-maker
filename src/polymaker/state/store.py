"""StateStore: the single owner of positions and open orders.

Replaces v1's module-level global dicts + the `performing`/`last_trade_update`
races. Three inputs, one arbitration rule (the README):

  * WS fill events apply immediately (optimistic),
  * REST reconciliation corrects drift ONLY for tokens with no in-flight trades,
  * on-chain balances are consulted only by the merger.

In-memory + typed, mirrored to SQLite on change so a crash-restart resumes.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path

from polymaker.domain import Fill, OpenOrder, OrderState, Position, Side
from polymaker.logging import get_logger

log = get_logger("state.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    token_id  TEXT PRIMARY KEY,
    size      REAL NOT NULL,
    avg_price REAL NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
    trade_id  TEXT PRIMARY KEY,
    token_id  TEXT, side TEXT, price REAL, size REAL, is_maker INT, ts REAL
);
CREATE TABLE IF NOT EXISTS order_log (
    order_id  TEXT PRIMARY KEY,
    token_id  TEXT, side TEXT, price REAL, size REAL, state TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS owned_orders (
    order_id TEXT PRIMARY KEY, condition_id TEXT, token_id TEXT, side TEXT,
    submitted_qty REAL, submitted_price REAL, created_ts REAL, source TEXT,
    unresolved INT NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pnl_snapshots (
    ts        REAL PRIMARY KEY,
    equity    REAL, net_cash REAL, inventory_value REAL, daily_pnl REAL
);
"""


class StateStore:
    """Owns positions + open orders + a per-token in-flight guard."""

    def __init__(self, db_path: str | Path = "state.db") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(owned_orders)")}
        if "unresolved" not in columns:
            self._conn.execute("ALTER TABLE owned_orders ADD COLUMN unresolved INT NOT NULL DEFAULT 1")
        self._conn.commit()

        self.positions: dict[str, Position] = {}
        # order_id -> OpenOrder
        self.orders: dict[str, OpenOrder] = {}
        # token_id -> count of in-flight (MATCHED-not-CONFIRMED) trades; guards reconcile
        self._inflight: dict[str, int] = {}
        self._inflight_ts: dict[str, float] = {}  # oldest in-flight mark, for expiry
        self._last_fill_ts: dict[str, float] = {}
        self._load()

    def close(self) -> None:
        self._conn.close()

    def register_owned_order(self, order_id: str, condition_id: str, token_id: str,
                             side: Side, qty: float, price: float, source: str) -> None:
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("owned order requires a usable exchange order id")
        self._conn.execute("INSERT OR REPLACE INTO owned_orders"
            "(order_id,condition_id,token_id,side,submitted_qty,submitted_price,created_ts,source,unresolved)"
            " VALUES(?,?,?,?,?,?,?,?,1)",
            (order_id.strip(), condition_id, token_id, side.value, qty, price, time.time(), source))
        self._conn.commit()

    def owned_order(self, order_id: str) -> dict[str, object] | None:
        row = self._conn.execute("SELECT * FROM owned_orders WHERE order_id=?", (order_id,)).fetchone()
        return dict(row) if row else None

    def owned_orders(self) -> list[dict[str, object]]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM owned_orders")]

    def owned_orders_for_recovery(self, *, since_ts: float, active_ids: set[str],
                                  condition_ids: set[str], limit: int = 500) -> list[dict[str, object]]:
        scope = ""
        scope_args: list[object] = []
        if condition_ids:
            placeholders = ",".join("?" for _ in condition_ids)
            scope = f" AND condition_id IN ({placeholders})"
            scope_args = sorted(condition_ids)
        # Safety population is never capped. The cap applies only to recent,
        # already resolved history used as a bounded checksum/recovery window.
        rows = [dict(r) for r in self._conn.execute(
            f"SELECT * FROM owned_orders WHERE unresolved=1{scope}", scope_args)]
        found = {str(r["order_id"]) for r in rows}
        scoped_active = active_ids - found
        if scoped_active:
            placeholders = ",".join("?" for _ in scoped_active)
            active_scope = scope if condition_ids else ""
            rows.extend(dict(r) for r in self._conn.execute(
                f"SELECT * FROM owned_orders WHERE order_id IN ({placeholders}){active_scope}",
                (*sorted(scoped_active), *scope_args)))
        found = {str(r["order_id"]) for r in rows}
        recent = [dict(r) for r in self._conn.execute(
            f"SELECT * FROM owned_orders WHERE unresolved=0 AND created_ts>=?{scope} "
            "ORDER BY created_ts DESC LIMIT ?", (since_ts, *scope_args, limit))]
        rows.extend(r for r in recent if str(r["order_id"]) not in found)
        return rows

    def mark_owned_order_unresolved(self, order_id: str, unresolved: bool) -> None:
        self._conn.execute("UPDATE owned_orders SET unresolved=? WHERE order_id=?",
                           (int(unresolved), order_id))
        self._conn.commit()

    # ── positions ───────────────────────────────────────────────────────
    def position(self, token_id: str) -> Position:
        return self.positions.get(token_id, Position(token_id))

    def has_recent_fill(self, token_id: str, within_s: float = 5.0) -> bool:
        """Whether a just-applied fill may still lead the account snapshot."""
        return time.time() - self._last_fill_ts.get(token_id, 0.0) < within_s

    def apply_fill(self, fill: Fill) -> bool:
        """Apply a fill optimistically to inventory + avg price.

        IDEMPOTENT: the SQLite fills table is the dedupe gate (trade_id is the
        primary key). A replayed fill — WS redelivery after reconnect, a MATCHED
        arriving again after CONFIRMED, or a replay across process restarts —
        is detected by INSERT OR IGNORE and NOT applied twice. Returns False
        for duplicates so callers can skip their side effects too.
        """
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO fills(trade_id,token_id,side,price,size,is_maker,ts) VALUES(?,?,?,?,?,?,?)",
            (fill.trade_id, fill.token_id, fill.side.value, fill.price, fill.size,
             int(fill.is_maker), fill.ts),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            log.warning("duplicate_fill_ignored", trade_id=fill.trade_id,
                        token=fill.token_id[:12], side=fill.side.value, size=fill.size)
            return False

        pos = self.positions.setdefault(fill.token_id, Position(fill.token_id))
        signed = fill.size if fill.side is Side.BUY else -fill.size
        new_size = pos.size + signed
        if fill.side is Side.BUY:
            if pos.size <= 0:
                pos.avg_price = fill.price
            else:
                pos.avg_price = (pos.avg_price * pos.size + fill.price * fill.size) / (
                    pos.size + fill.size
                )
        # selling leaves avg_price unchanged
        pos.size = max(0.0, new_size)
        if pos.size <= 0:
            pos.avg_price = 0.0
        self._last_fill_ts[fill.token_id] = fill.ts
        self._persist_position(pos)
        log.info("fill", token=fill.token_id[:12], side=fill.side.value,
                 price=fill.price, size=fill.size, pos=round(pos.size, 2))
        return True

    def set_position(self, token_id: str, size: float, avg_price: float) -> None:
        pos = Position(token_id, max(0.0, size), avg_price if size > 0 else 0.0)
        self.positions[token_id] = pos
        self._persist_position(pos)

    def reconcile_positions(self, api_positions: dict[str, tuple[float, float]]) -> None:
        """Overwrite sizes from REST, skipping tokens with in-flight trades or
        a very recent fill (the optimistic value is more current there)."""
        now = time.time()
        for token_id, (size, avg) in api_positions.items():
            if self._inflight.get(token_id, 0) > 0:
                continue
            if now - self._last_fill_ts.get(token_id, 0.0) < 5.0:
                continue
            self.set_position(token_id, size, avg)

    # ── in-flight guard ─────────────────────────────────────────────────
    def mark_inflight(self, token_id: str) -> None:
        self._inflight[token_id] = self._inflight.get(token_id, 0) + 1
        self._inflight_ts.setdefault(token_id, time.time())

    def clear_inflight(self, token_id: str) -> None:
        if self._inflight.get(token_id, 0) > 0:
            self._inflight[token_id] -= 1
        if self._inflight.get(token_id, 0) == 0:
            self._inflight_ts.pop(token_id, None)

    def inflight(self, token_id: str) -> int:
        return self._inflight.get(token_id, 0)

    def expire_inflight(self, max_age_s: float) -> list[str]:
        """Force-clear in-flight guards older than max_age_s.

        A MATCHED whose CONFIRMED/FAILED never arrives (dropped WS event) would
        otherwise block reconciliation for that token forever. Returns the
        tokens cleared so the engine can force an authoritative REST reconcile.
        """
        now = time.time()
        stale = [t for t, ts in self._inflight_ts.items() if now - ts > max_age_s]
        for t in stale:
            age = round(now - self._inflight_ts[t])
            self._inflight[t] = 0
            self._inflight_ts.pop(t, None)
            log.warning("inflight_expired", token=t[:12], age_s=age)
        return stale

    # ── orders ──────────────────────────────────────────────────────────
    def orders_for(self, token_id: str) -> list[OpenOrder]:
        return [o for o in self.orders.values() if o.token_id == token_id]

    def upsert_order(self, order: OpenOrder) -> None:
        if order.state in (OrderState.CANCELED, OrderState.DONE, OrderState.REJECTED):
            self.orders.pop(order.order_id, None)
        else:
            self.orders[order.order_id] = order
        self._persist_order(order)

    def remove_order(self, order_id: str) -> None:
        self.orders.pop(order_id, None)

    def replace_open_orders(
        self, token_id: str, live: list[OpenOrder], *, grace_s: float = 10.0
    ) -> None:
        """Replace our view of a token's open orders from a REST snapshot.

        DOUBLE-ORDER GUARD: a REST snapshot can lag a placement by seconds. If we
        dropped a just-placed order because the snapshot didn't include it yet,
        the reconciler would immediately re-place it -> duplicate live orders.
        So local orders younger than `grace_s` survive even when absent from the
        snapshot (pass grace_s=0 to force an authoritative wipe, e.g. after the
        exchange auto-cancelled everything on a heartbeat gap).
        """
        now = time.time()
        live_ids = {o.order_id for o in live}
        for o in [o for o in self.orders.values() if o.token_id == token_id]:
            if o.order_id in live_ids:
                continue
            if now - o.created_ts < grace_s:
                continue  # too young to trust its absence from the snapshot
            self.orders.pop(o.order_id, None)
        for o in live:
            self.orders[o.order_id] = o

    def clear_orders(self) -> None:
        """Forget all local open orders (e.g. after a confirmed server-side wipe)."""
        self.orders.clear()

    # ── persistence ─────────────────────────────────────────────────────
    def _persist_position(self, pos: Position) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO positions(token_id,size,avg_price,updated_ts) VALUES(?,?,?,?)",
            (pos.token_id, pos.size, pos.avg_price, time.time()),
        )
        self._conn.commit()

    def _persist_order(self, o: OpenOrder) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO order_log(order_id,token_id,side,price,size,state,ts) VALUES(?,?,?,?,?,?,?)",
            (o.order_id, o.token_id, o.side.value, o.price, o.size, o.state.value, time.time()),
        )
        self._conn.commit()

    def _load(self) -> None:
        for row in self._conn.execute("SELECT token_id,size,avg_price FROM positions"):
            if row["size"] > 0:
                self.positions[row["token_id"]] = Position(
                    row["token_id"], row["size"], row["avg_price"]
                )

    def drop_untracked_positions(self, tracked: set[str]) -> list[str]:
        """Remove positions for tokens we don't trade (e.g. the operator's manual
        UI bets that leaked in via an earlier unscoped reconcile). They must not
        count toward exposure caps or PnL. Returns the dropped token ids."""
        dropped = [t for t in self.positions if t not in tracked]
        for t in dropped:
            self.positions.pop(t, None)
            with contextlib.suppress(sqlite3.Error):
                self._conn.execute("DELETE FROM positions WHERE token_id=?", (t,))
        if dropped:
            self._conn.commit()
            log.info("untracked_positions_dropped", n=len(dropped))
        return dropped

    def force_set_position(self, token_id: str, size: float, avg_price: float, source: str) -> None:
        """Overwrite a position unconditionally (used when on-chain is truth)."""
        prev = self.positions.get(token_id)
        self.set_position(token_id, size, avg_price)
        log.warning("position_forced", token=token_id[:12], source=source,
                    prev=round(prev.size, 2) if prev else 0.0, now=round(size, 2))

    # ── maintenance / reporting ─────────────────────────────────────────
    def checkpoint_wal(self) -> None:
        """Truncate the WAL so it can't grow without bound under high volume."""
        with contextlib.suppress(sqlite3.Error):
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def record_pnl(self, equity: float, net_cash: float, inv_value: float, daily_pnl: float) -> None:
        with contextlib.suppress(sqlite3.Error):
            self._conn.execute(
                "INSERT OR REPLACE INTO pnl_snapshots(ts,equity,net_cash,inventory_value,daily_pnl)"
                " VALUES(?,?,?,?,?)",
                (time.time(), equity, net_cash, inv_value, daily_pnl),
            )
            self._conn.commit()

    def snapshot(self) -> dict[str, object]:
        return {
            "positions": {k: json.loads(_pos_json(v)) for k, v in self.positions.items() if v.size > 0},
            "open_orders": len(self.orders),
        }


def _pos_json(p: Position) -> str:
    return json.dumps({"size": round(p.size, 4), "avg_price": round(p.avg_price, 4)})
