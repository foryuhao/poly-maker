"""Isolated GET-only collectors for phase-one sync observation.

No production Gateway, processor, journal, NetworkHealth or SDK-global transport.
Observed account order IDs confer no ownership or trading authority.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

import httpx
from eth_account import Account
from eth_utils import is_address, to_checksum_address
from py_clob_client_v2.constants import END_CURSOR, INITIAL_CURSOR

from polymaker.rdp_sm import OFFICIAL_HOSTS, ReadOnlyClobClient, _market, _token_id
from polymaker.userstream.parse import normalize_order, parse_trade, strict_number


@dataclass(frozen=True)
class ReadEvidence:
    name: str
    scope: str
    started_wall: float
    completed_wall: float
    started_monotonic: float
    completed_monotonic: float
    complete: bool
    data_json: str | None
    partial_json: str
    requests: int
    error_type: str | None = None
    http_status: int | None = None
    retry_after_s: float = 0.0
    read_id: str = ""
    error_code: str | None = None

    @property
    def data(self):
        """Return a copy; callers cannot mutate published evidence."""
        return json.loads(self.data_json) if self.data_json is not None else None

    def to_dict(self):
        row = asdict(self)
        row["data"] = row.pop("data_json")
        row["data"] = json.loads(row["data"]) if row["data"] is not None else None
        row["partial"] = json.loads(row.pop("partial_json"))
        return row


@dataclass(frozen=True)
class ObservedMarket:
    slug: str
    condition_id: str
    start: int
    tokens: tuple[str, str]


class IncompleteRead(ValueError):
    def __init__(self, rows, cause):
        super().__init__("SYNC_COLLECTION_INCOMPLETE")
        self.rows, self.cause = rows, cause


class ObserverClobClient(ReadOnlyClobClient):
    """Use the pinned SDK's authentication with a private, GET-only transport."""

    def __init__(self, reader, **kwargs):
        self.reader = reader
        super().__init__(**kwargs)

    def _get(self, endpoint, headers=None, params=None):
        path = urlsplit(endpoint).path
        allowed = {"/auth/derive-api-key", "/time", "/data/orders", "/data/trades",
                   "/balance-allowance"}
        if path not in allowed and not (
            path.startswith("/data/order/") and path.removeprefix("/data/order/")
            in self.reader.observed_ids
        ):
            raise ValueError("SYNC_GET_PATH_PROHIBITED")
        return self.reader.http_get(endpoint, headers=headers, params=params)


class ObserverReader:
    def __init__(self, cfg, *, emit=lambda _event: None, stop=None, transport=None,
                 timeout_s=5.0, max_pages=100, read_budget_s=10.0):
        self.cfg, self.emit = cfg, emit
        self.stop = stop if stop is not None else threading.Event()
        self.transport, self.timeout_s = transport, timeout_s
        self.max_pages, self.read_budget_s = max_pages, read_budget_s
        self.local = threading.local()
        self.observed_ids: frozenset[str] = frozenset()
        self.client = self.http = None
        self.funder = ""

    def open(self):
        cfg = self.cfg
        if (any(getattr(cfg.wallet, name).rstrip("/") != host
                for name, host in OFFICIAL_HOSTS.items()) or cfg.wallet.chain_id != 137):
            raise ValueError("SYNC_OFFICIAL_HOSTS_REQUIRED")
        if not cfg.secrets.has_wallet or not is_address(cfg.secrets.browser_address):
            raise ValueError("SYNC_ACCOUNT_CONFIGURATION_INVALID")
        self.funder = to_checksum_address(cfg.secrets.browser_address)
        signer = Account.from_key(cfg.secrets.pk).address
        if int(self.funder, 16) == 0 or cfg.wallet.signature_type not in (0, 1, 2, 3):
            raise ValueError("SYNC_ACCOUNT_CONFIGURATION_INVALID")
        if cfg.wallet.signature_type == 0 and signer != self.funder:
            raise ValueError("SYNC_EOA_FUNDER_MISMATCH")
        self.http = httpx.Client(transport=self.transport, timeout=self.timeout_s,
                                 follow_redirects=False,
                                 limits=httpx.Limits(max_connections=6, max_keepalive_connections=6))
        self.client = ObserverClobClient(
            self, host=cfg.wallet.clob_host, key=cfg.secrets.pk, chain_id=137,
            signature_type=cfg.wallet.signature_type, funder=self.funder, use_server_time=False,
        )
        # Existing credential derivation is GET; never create_or_derive/create a key.
        credentials = self.client.derive_api_key()
        if not all(isinstance(v, str) and v.strip() for v in (
                credentials.api_key, credentials.api_secret, credentials.api_passphrase)):
            raise ValueError("SYNC_AUTH_INVALID")
        self.client.set_api_creds(credentials)

    def close(self):
        if self.http is not None:
            self.http.close()

    def http_get(self, endpoint, *, headers=None, params=None):
        parsed = urlsplit(endpoint)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        clob_paths = {"/auth/derive-api-key", "/time", "/data/orders", "/data/trades",
                      "/balance-allowance"} | {"/data/order/" + oid for oid in self.observed_ids}
        allowed = {
            OFFICIAL_HOSTS["clob_host"]: clob_paths,
            OFFICIAL_HOSTS["gamma_host"]: {"/markets"},
            OFFICIAL_HOSTS["data_api_host"]: {"/v2/positions"},
        }
        if (origin not in allowed or parsed.query or parsed.fragment
                or parsed.path not in allowed[origin]):
            raise ValueError("SYNC_GET_ORIGIN_PROHIBITED")
        if origin != OFFICIAL_HOSTS["clob_host"] and headers:
            raise ValueError("SYNC_AUTH_HEADERS_WRONG_HOST")
        if self.stop.is_set():
            raise InterruptedError("SYNC_STOP_REQUESTED")
        began, wall = time.monotonic(), time.time()
        response, error = None, None
        context = getattr(self.local, "context", None)
        if context is not None:
            context["requests"] += 1
        try:
            response = self.http.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("SYNC_REDIRECT_PROHIBITED")
            return response.json()
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            self.emit({"kind": "sync_http", "method": "GET", "endpoint": parsed.path,
                       "host": parsed.hostname, "scope": context["scope"] if context else "bootstrap",
                       "read_id": context["read_id"] if context else "bootstrap",
                       "started_wall": wall, "completed_wall": time.time(),
                       "started_monotonic": began, "completed_monotonic": time.monotonic(),
                       "http_status": response.status_code if response is not None else None,
                       "error_type": error})

    def read(self, name, scope, fn):
        began, wall = time.monotonic(), time.time()
        context = {"scope": scope, "read_id": uuid.uuid4().hex, "requests": 0,
                   "deadline": began + self.read_budget_s}
        self.local.context = context
        result, partial, error, status, retry, code = None, [], None, None, 0.0, None
        try:
            result = json.dumps(fn(), allow_nan=False, sort_keys=True)
        except Exception as exc:
            if isinstance(exc, IncompleteRead):
                partial, exc = exc.rows, exc.cause
            error = type(exc).__name__  # Never serialize exception messages/HTTP bodies.
            message = exc.args[0] if exc.args else None
            if isinstance(message, str) and re.fullmatch(r"SYNC_[A-Z0-9_]+", message):
                code = message
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                try:
                    retry = min(60.0, max(0.0, float(exc.response.headers.get("retry-after", "0"))))
                    if not math.isfinite(retry):
                        retry = 0.0
                except ValueError:
                    retry = 0.0
        finally:
            self.local.context = None
        return ReadEvidence(name, scope, wall, time.time(), began, time.monotonic(),
                            error is None, result, json.dumps(partial, allow_nan=False),
                            context["requests"], error, status, retry, context["read_id"], code)

    def _collection(self, path, params, convert, *, data_api=False):
        cursor, seen, result = (None if data_api else INITIAL_CURSOR), set(), []
        try:
            for _ in range(self.max_pages):
                if cursor in seen or time.monotonic() >= self.local.context["deadline"]:
                    raise ValueError("SYNC_PAGINATION_CYCLE_OR_BUDGET")
                seen.add(cursor)
                query = dict(params)
                if data_api:
                    if cursor is not None:
                        query["cursor"] = cursor
                    page = self.http_get(self.cfg.wallet.data_api_host.rstrip("/") + path, params=query)
                else:
                    query["next_cursor"] = cursor
                    page = self.client._get(self.client.host + path, params=query,
                                            headers=self.client._l2_headers("GET", path))
                if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                    raise ValueError("SYNC_PAGE_INVALID")
                for row in page["data"]:
                    result.append(convert(row))
                if data_api:
                    pagination = page.get("pagination")
                    if not isinstance(pagination, dict) or "next_cursor" not in pagination:
                        raise ValueError("SYNC_CURSOR_MISSING")
                    cursor = pagination["next_cursor"]
                    if cursor is None:
                        return result
                else:
                    cursor = page.get("next_cursor")
                    if cursor == END_CURSOR:
                        return result
                if not isinstance(cursor, str) or not cursor:
                    raise ValueError("SYNC_CURSOR_INVALID")
            raise ValueError("SYNC_TOO_MANY_PAGES")
        except Exception as exc:
            raise IncompleteRead(result, exc) from exc

    def server_time(self):
        value = strict_number(self.client.get_server_time())
        if not math.isfinite(value) or value <= 0:
            raise ValueError("SYNC_TIME_INVALID")
        return value

    def market(self, epoch):
        slug = f"btc-updown-5m-{epoch}"
        rows = self.http_get(self.cfg.wallet.gamma_host.rstrip("/") + "/markets",
                             params={"slug": slug, "limit": 1})
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("SYNC_MARKET_MISSING")
        meta, start = _market(rows[0], slug)
        return ObservedMarket(slug, meta.condition_id, start,
                              (meta.yes.token_id, meta.no.token_id))

    @staticmethod
    def order_row(row):
        if not isinstance(row, dict):
            raise ValueError("SYNC_ORDER_INVALID")
        event = normalize_order(row)
        if (event is None or not event.market_id
                or re.fullmatch(r"0x[0-9a-fA-F]{64}", event.market_id) is None
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event.order_id) is None):
            raise ValueError("SYNC_ORDER_INVALID")
        _token_id(event.token_id)
        result = asdict(event)
        result["associate_trades_present"] = "associate_trades" in row
        if "maker_address" in row:
            if not isinstance(row["maker_address"], str):
                raise ValueError("SYNC_ORDER_MAKER_INVALID")
            result["maker_address"] = row["maker_address"]
        return result

    def open_orders(self):
        rows = self._collection("/data/orders", {}, self.order_row)
        if len({r["order_id"] for r in rows}) != len(rows):
            raise IncompleteRead(rows, ValueError("SYNC_DUPLICATE_ORDER"))
        return rows

    def trades(self, market):
        def convert(row):
            parsed = parse_trade(row)
            if parsed.market != market.condition_id or not math.isfinite(parsed.timestamp):
                raise ValueError("SYNC_TRADE_MARKET_MISMATCH")
            legs = [asdict(leg) for leg in parsed.maker_legs]
            for leg in legs:
                _token_id(leg["asset_id"])
                if leg["asset_id"] not in market.tokens:
                    raise ValueError("SYNC_TRADE_TOKEN_MISMATCH")
                leg.pop("owner", None)
            return {"trade_id": parsed.trade_id, "status": parsed.status,
                    "market": parsed.market, "timestamp": parsed.timestamp, "maker_legs": legs,
                    "trader_side": row.get("trader_side"),
                    "taker_order_id": parsed.taker_order_id,
                    "taker_asset_id": parsed.taker_asset_id, "taker_side": parsed.taker_side}
        rows = self._collection("/data/trades", {"market": market.condition_id}, convert)
        if len({r["trade_id"] for r in rows}) != len(rows):
            raise IncompleteRead(rows, ValueError("SYNC_DUPLICATE_TRADE"))
        return rows

    def order(self, order_id, market=None):
        row = self.order_row(self.client.get_order(order_id))
        if row["order_id"] != order_id:
            raise ValueError("SYNC_ORDER_ID_MISMATCH")
        if market is not None and (row["market_id"] != market.condition_id
                                   or row["token_id"] not in market.tokens):
            raise ValueError("SYNC_ORDER_MARKET_MISMATCH")
        return row

    def capital(self):
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

        raw = self.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        if not isinstance(raw, dict) or not isinstance(raw.get("allowances"), dict):
            raise ValueError("SYNC_CAPITAL_INVALID")
        result = {"balance": raw.get("balance"), "allowances": dict(raw["allowances"])}
        for value in [result["balance"], *result["allowances"].values()]:
            number = strict_number(value)
            if not math.isfinite(number) or number < 0 or not number.is_integer():
                raise ValueError("SYNC_CAPITAL_INVALID")
        return result

    def positions(self):
        def convert(row):
            token = _token_id(row.get("token_id"))
            size, price = strict_number(row.get("current_size")), strict_number(row.get("avg_price"))
            if not math.isfinite(size) or size < 0 or not math.isfinite(price) or not 0 <= price <= 1:
                raise ValueError("SYNC_POSITION_INVALID")
            return {"token_id": token, "size": size, "avg_price": price}
        rows = self._collection("/v2/positions", {"user": self.funder}, convert, data_api=True)
        if len({r["token_id"] for r in rows}) != len(rows):
            raise IncompleteRead(rows, ValueError("SYNC_DUPLICATE_POSITION"))
        return rows
