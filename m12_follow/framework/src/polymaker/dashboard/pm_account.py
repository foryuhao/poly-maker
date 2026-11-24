"""Public, GET-only PM account observations, isolated from the trading process."""

from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import httpx

from polymaker.dashboard.model import number
from polymaker.dashboard.settlement import validate_resolution

BASE = "https://data-api.polymarket.com"
ZERO = Decimal(0)
EPS = Decimal(".00001")


class AccountReadTransport(httpx.BaseTransport):
    """Reject writes and unapproved routes before any socket is opened."""

    def __init__(self, inner=None):
        self.inner = inner or httpx.HTTPTransport()

    def handle_request(self, request):
        url = request.url
        allowed = ((url.host == "data-api.polymarket.com" and url.path in {"/v2/positions", "/v2/activity"})
            or (url.host == "gamma-api.polymarket.com" and url.path.startswith("/markets/slug/")))
        if request.method != "GET" or url.scheme != "https" or url.port not in {None, 443} or not allowed:
            raise RuntimeError("PM_ACCOUNT_READ_ONLY_ROUTE_REQUIRED")
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


def wallet_for(session):
    found = set()
    for source in session.get("sources", []):
        match = re.fullmatch(r"rdp_sm_(0x[0-9a-fA-F]{40})_"+re.escape(session["session_id"])+r"\.jsonl",
                             Path(source.get("path", "")).name)
        if match and source.get("kind") == "risk":
            found.add(match[1].lower())
    return next(iter(found)) if len(found) == 1 else None


def numeric(row, key, *, nonnegative=False):
    value = number(row.get(key))
    if isinstance(row.get(key), bool) or value is None or nonnegative and value < 0:
        raise ValueError("PM_ACCOUNT_NUMBER_UNAVAILABLE:"+key)
    return value


def pages(client, route, params):
    result, seen = [], set()
    query = {**params, "limit": 500}
    for _ in range(50):
        response = client.get(BASE+route, params=query)
        response.raise_for_status()
        payload = response.json()
        if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
                or not isinstance(payload.get("pagination"), dict) or "next_cursor" not in payload["pagination"]):
            raise ValueError("PM_ACCOUNT_PAGE_INVALID")
        result.extend(payload["data"])
        cursor = payload["pagination"]["next_cursor"]
        more = payload["pagination"].get("has_more")
        if cursor is None:
            if more is True:
                raise ValueError("PM_ACCOUNT_PAGE_INCOMPLETE")
            return result
        if not isinstance(cursor, str) or not cursor or cursor in seen or more is False:
            raise ValueError("PM_ACCOUNT_CURSOR_INVALID")
        seen.add(cursor)
        query["cursor"] = cursor
    raise ValueError("PM_ACCOUNT_PAGE_LIMIT")


def identity(row, wallet, sessions):
    if not isinstance(row, dict) or str(row.get("proxy_wallet", "")).lower() != wallet:
        raise ValueError("PM_ACCOUNT_WALLET_MISMATCH")
    condition = str(row.get("condition_id", "")).lower()
    session = sessions.get(condition)
    if session is None or row.get("slug") != session["slug"]:
        raise ValueError("PM_ACCOUNT_MARKET_MISMATCH")
    return condition


def normalize_position(row):
    fields = ("current_size", "current_value", "total_size", "total_cost_usdc", "entry_cost_usdc",
              "entry_fees_usdc", "avg_price", "realized_pnl", "unrealized_pnl", "total_pnl")
    result = {key: str(numeric(row, key, nonnegative=key not in {"realized_pnl", "unrealized_pnl", "total_pnl"}))
              for key in fields}
    if abs(Decimal(result["total_pnl"])-Decimal(result["realized_pnl"])-Decimal(result["unrealized_pnl"])) > EPS:
        raise ValueError("PM_ACCOUNT_PNL_CONFLICT")
    if Decimal(result["current_size"]) == 0 and Decimal(result["current_value"]) > EPS:
        raise ValueError("PM_ACCOUNT_POSITION_VALUE_CONFLICT")
    if not isinstance(row.get("token_id"), str) or not row["token_id"].isdigit():
        raise ValueError("PM_ACCOUNT_TOKEN_INVALID")
    result.update({key: row.get(key) for key in ("token_id", "opposite_token_id", "outcome_index", "status")})
    return result


def normalize_trade(row):
    quantity, amount = numeric(row, "size", nonnegative=True), numeric(row, "usdc_size", nonnegative=True)
    price, timestamp = numeric(row, "price", nonnegative=True), numeric(row, "timestamp", nonnegative=True)
    if quantity <= 0 or not 0 <= price <= 1 or row.get("side") not in {"BUY", "SELL"}:
        raise ValueError("PM_ACCOUNT_TRADE_INVALID")
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", row.get("transaction_hash", "")):
        raise ValueError("PM_ACCOUNT_TRANSACTION_INVALID")
    if not isinstance(row.get("token_id"), str) or not row["token_id"].isdigit():
        raise ValueError("PM_ACCOUNT_TOKEN_INVALID")
    clean = {key: row.get(key) for key in ("token_id", "side", "transaction_hash", "outcome_index")}
    clean.update(quantity=str(quantity), amount=str(amount), price=str(price), ts=float(timestamp))
    clean["id"] = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
    return clean


def token_sides(session, positions):
    sides = {token: side for side, token in session.get("token_ids", {}).items()}
    for p in positions:
        if p["token_id"] in sides and p.get("opposite_token_id"):
            opposite = p["opposite_token_id"]
            side = "NO" if sides[p["token_id"]] == "YES" else "YES"
            if opposite in sides and sides[opposite] != side:
                raise ValueError("PM_ACCOUNT_OUTCOME_CONFLICT")
            sides[opposite] = side
    return sides


def account_market(session, positions, trades, resolution, observed_at):
    sides = token_sides(session, positions)
    if resolution:
        for side, token in resolution["token_ids"].items():
            if token in sides and sides[token] != side:
                raise ValueError("PM_ACCOUNT_OUTCOME_CONFLICT")
            sides[token] = side
    inventory = {side: ZERO for side in ("YES", "NO")}
    for p in positions:
        side = sides.get(p["token_id"])
        if side is None:
            raise ValueError("PM_ACCOUNT_OUTCOME_UNAVAILABLE")
        inventory[side] += Decimal(p["current_size"])
    realized = sum((Decimal(p["realized_pnl"]) for p in positions), ZERO)
    floating = sum((Decimal(p["unrealized_pnl"]) for p in positions), ZERO)
    settled = None
    no_activity = (not positions and not trades and session.get("real_submitted_orders", 0) == 0
                   and all(s.get("caught_up") and not any(s.get(k) for k in ("errors", "gaps", "missing", "partial"))
                           for s in session.get("sources", [])))
    if positions and resolution:
        # Replace PM's price mark with the verified binary settlement payout.
        settled = sum((Decimal(p["total_pnl"])-Decimal(p["current_value"])
            +(Decimal(p["current_size"]) if sides[p["token_id"]] == resolution["winner"] else ZERO)
            for p in positions), ZERO)
    elif positions and all(Decimal(p["current_size"]) == 0 and p["status"] == "CLOSED" for p in positions):
        settled = realized
    elif no_activity and session["market_end"] <= observed_at:
        settled = ZERO
    for trade in trades:
        trade["outcome"] = sides.get(trade["token_id"], "—")
    return {"source": BASE+"/v2/positions", "activity_source": BASE+"/v2/activity",
        "observed_at": observed_at, "positions": positions, "trades": trades,
        "inventory": {k: str(v) for k, v in inventory.items()}, "resolution": resolution,
        "realized_pnl": str(realized) if positions else None,
        "unrealized_pnl": str(floating) if positions else None,
        "market_value": str(sum((Decimal(p["current_value"]) for p in positions), ZERO)),
        "settled_pnl": str(settled) if settled is not None else None,
        "state": "AVAILABLE" if positions or settled is not None or no_activity else "INDEX_PENDING"}


def fetch_account(wallet, sessions, resolutions=None, *, transport=None):
    sessions = {s["condition_id"].lower(): s for s in sessions}
    if not 1 <= len(sessions) <= 20 or not re.fullmatch(r"0x[0-9a-f]{40}", wallet):
        raise ValueError("PM_ACCOUNT_QUERY_INVALID")
    params = {"user": wallet, "condition": ",".join(sessions)}
    positions = {key: {} for key in sessions}
    trades = {key: [] for key in sessions}
    with httpx.Client(timeout=5, follow_redirects=False, transport=AccountReadTransport(transport)) as client:
        for status in ("OPEN", "CLOSED"):
            query = {**params, "status": status, "filter_type": "TOKENS", "filter_amount": 0}
            if status == "OPEN":
                query["include_archived"] = "true"
            for row in pages(client, "/v2/positions", query):
                condition = identity(row, wallet, sessions)
                position = normalize_position(row)
                previous = positions[condition].get(position["token_id"])
                if previous is not None and previous != position:
                    raise ValueError("PM_ACCOUNT_POSITION_CONFLICT")
                positions[condition][position["token_id"]] = position
        for row in pages(client, "/v2/activity", {**params, "type": "TRADE",
                "start": int(min(s["market_start"] for s in sessions.values()))}):
            condition = identity(row, wallet, sessions)
            if row.get("type") != "TRADE":
                raise ValueError("PM_ACCOUNT_ACTIVITY_TYPE_MISMATCH")
            trade = normalize_trade(row)
            # Identical public rows lack order/log IDs; preserve their
            # multiplicity instead of silently dropping a genuine fill.
            trade["id"] += ":"+str(len(trades[condition]))
            trades[condition].append(trade)
        result = {}
        for condition, session in sessions.items():
            rows = list(positions[condition].values())
            sides = token_sides(session, rows)
            expected = {side: token for token, side in sides.items()}
            resolution = (resolutions or {}).get(condition)
            if not rows:
                resolution = None
            if resolution and not expected:
                expected = resolution.get("token_ids", {})
            if resolution and (resolution.get("condition_id", "").lower() != condition
                               or resolution.get("slug") != session["slug"] or resolution.get("token_ids") != expected):
                raise ValueError("PM_ACCOUNT_RESOLUTION_MISMATCH")
            if resolution and (resolution.get("closed") is not True or resolution.get("resolution_status") != "resolved"
                               or resolution.get("winner") not in {"YES", "NO"}):
                raise ValueError("PM_ACCOUNT_RESOLUTION_NOT_FINAL")
            resolution_error = None
            if rows and not resolution and session["market_end"] <= time.time() and set(expected) == {"YES", "NO"}:
                try:
                    response = client.get("https://gamma-api.polymarket.com/markets/slug/"+session["slug"])
                    response.raise_for_status()
                    resolution = validate_resolution(response.json(), {**session, "token_ids": expected})
                except (httpx.HTTPError, ValueError) as exc:
                    resolution_error = type(exc).__name__
            result[condition] = account_market(session, rows, trades[condition], resolution, time.time())
            result[condition]["resolution_error"] = resolution_error
            result[condition]["wallet"] = wallet
        return result


def apply_account(session, payload, *, error=None):
    if payload.get("wallet") != wallet_for(session):
        raise ValueError("PM_ACCOUNT_CACHE_WALLET_MISMATCH")
    session["pm_account"] = {**payload, "error": error}
    session["m1_account_tracking"] = "PM_DATA_API"
    session["inventory"] = payload["inventory"]
    session["trading_pnl"] = payload["settled_pnl"]
    session["known_pnl"] = payload["settled_pnl"]
    session["positions_verified"] = payload["state"] == "AVAILABLE"
    session["issues"] = [i for i in session["issues"] if i not in {"M1_FILL_UNVERIFIED", "PM_ACCOUNT_READ_FAILED"}]
    if error:
        session["issues"].append("PM_ACCOUNT_READ_FAILED")
    if payload["state"] == "INDEX_PENDING":
        session["issues"].append("PM_ACCOUNT_INDEX_PENDING")
    if payload.get("resolution"):
        session["settlement"] = payload["resolution"]


class AccountObserver:
    """A bounded background reader; writes only the dashboard's evidence DB."""

    def __init__(self, collector, fetch=fetch_account):
        self.collector, self.fetch = collector, fetch
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dashboard-account-read")
        self.future, self.pending = None, []
        self.retry_at, self.errors = {}, {}
        collector.db.execute("CREATE TABLE IF NOT EXISTS pm_account(condition_id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        self.cache = {r[0]: json.loads(r[1]) for r in collector.db.execute("SELECT * FROM pm_account")}

    def tick(self, snapshot):
        if self.future is not None and self.future.done():
            try:
                result = self.future.result()
                for condition, payload in result.items():
                    key = payload["wallet"]+":"+condition
                    self.cache[key] = payload
                    self.errors.pop(key, None)
                    self.collector.db.execute("INSERT OR REPLACE INTO pm_account VALUES(?,?)", (key, json.dumps(payload)))
                self.collector.db.commit()
            except Exception as exc:
                for session in self.pending:
                    key = wallet_for(session)+":"+session["condition_id"].lower()
                    self.errors[key] = type(exc).__name__+": "+str(exc)[:120]
            for session in self.pending:
                self.retry_at[wallet_for(session)+":"+session["condition_id"].lower()] = time.monotonic()+15
            self.future, self.pending = None, []
        since = snapshot["observed_at"]-getattr(self.collector, "history_hours", 24)*3600
        settings = self.collector.output / "view-settings.json"
        if settings.exists():
            since = max(since, float(json.loads(settings.read_text()).get("display_since", 0)))
        eligible = [s for s in snapshot["sessions"] if s.get("post_type") == "REAL" and s.get("strategy") == "M1"
                    and s.get("condition_id") and s.get("market_start", 0) >= since and wallet_for(s)]
        unique = {}
        for session in eligible:
            key = wallet_for(session)+":"+session["condition_id"].lower()
            if key not in unique or session.get("last_event_at", 0) > unique[key].get("last_event_at", 0):
                unique[key] = session
        eligible = list(unique.values())
        due = []
        resolutions = {s["condition_id"].lower(): s["settlement"] for s in snapshot["sessions"]
                       if s.get("condition_id") and s.get("settlement")}
        for session in eligible:
            condition = session["condition_id"].lower()
            key = wallet_for(session)+":"+condition
            cached = self.cache.get(key)
            if cached:
                apply_account(session, cached, error=self.errors.get(key))
                if cached.get("resolution"):
                    resolutions[condition] = cached["resolution"]
            elif key in self.errors:
                session["pm_account"] = {"state": "UNAVAILABLE", "error": self.errors[key]}
            interval = 3600 if cached and cached["settled_pnl"] is not None and snapshot["observed_at"]-session["market_end"] > 900 else 15
            if (self.retry_at.get(key, 0) <= time.monotonic()
                    and (cached is None or snapshot["observed_at"]-cached["observed_at"] >= interval)):
                due.append(session)
        if self.future is None and due:
            due.sort(key=lambda s: (self.cache.get(wallet_for(s)+":"+s["condition_id"].lower(), {}).get("observed_at", 0), -s["market_start"]))
            wallet = wallet_for(due[0])
            self.pending = [s for s in due if wallet_for(s) == wallet][:20]
            self.future = self.pool.submit(self.fetch, wallet, self.pending, resolutions)
        snapshot["pm_account_reader"] = {"mode": "PUBLIC_GET_ONLY", "pending": len(self.pending), "errors": self.errors}

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
