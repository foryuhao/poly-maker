"""MakerInventory M2A: one BTC5m market, official read-only inputs, NO-SEND output."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from polymaker.btc5m import PERIOD_S, choose_epoch, resolve_btc5m_market, slug_for_epoch
from polymaker.config import Config
from polymaker.domain import MarketMeta
from polymaker.marketdata.service import MarketDataService
from polymaker.mi_readonly_account import AccountSnapshot, OfficialReadOnlyAccount
from polymaker.strategy.maker_inventory import (
    MakerInventoryBook,
    MakerInventoryDecision,
    MakerInventorySnapshot,
    MakerInventoryState,
    evaluate,
)


class MIReadinessError(RuntimeError):
    """MI observation cannot safely represent the selected market."""


class AccountReader(Protocol):
    async def connect(self) -> None: ...
    async def snapshot(self, meta: MarketMeta) -> AccountSnapshot: ...


def validate_official_hosts(cfg: Config) -> None:
    for url, expected in (
        (cfg.wallet.gamma_host, "gamma-api.polymarket.com"),
        (cfg.wallet.clob_host, "clob.polymarket.com"),
        (cfg.wallet.data_api_host, "data-api.polymarket.com"),
    ):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != expected or parsed.port is not None:
            raise MIReadinessError("OFFICIAL_POLYMARKET_HOST_REQUIRED")


def validate_market(meta: MarketMeta, epoch: int) -> None:
    if (
        meta.slug != slug_for_epoch(epoch)
        or not meta.condition_id
        or meta.yes.outcome.strip().lower() != "up"
        or meta.no.outcome.strip().lower() != "down"
        or not meta.yes.token_id
        or not meta.no.token_id
        or meta.yes.token_id == meta.no.token_id
        or not math.isfinite(meta.tick_size)
        or meta.tick_size <= 0
        or not math.isfinite(meta.min_order_size)
        or meta.min_order_size <= 0
    ):
        raise MIReadinessError("MARKET_IDENTITY_OR_METADATA_INVALID")
    if not meta.end_date_iso:
        raise MIReadinessError("MARKET_END_UNKNOWN")
    try:
        end = datetime.fromisoformat(meta.end_date_iso.replace("Z", "+00:00"))
        if abs(end.astimezone(UTC).timestamp() - (epoch + PERIOD_S)) > 2:
            raise MIReadinessError("MARKET_WINDOW_MISMATCH")
    except ValueError as exc:
        raise MIReadinessError("MARKET_END_INVALID") from exc


def assert_clean_start(account: AccountSnapshot, meta: MarketMeta) -> None:
    tokens = {meta.yes.token_id, meta.no.token_id}
    if any(account.positions.get(token, Decimal("0")) != 0 for token in tokens):
        raise MIReadinessError("TARGET_MARKET_NOT_CLEAN: position")
    if any(token in tokens for token in account.open_order_tokens):
        raise MIReadinessError("TARGET_MARKET_NOT_CLEAN: open order")


def venue_quantity_step(tick_size: Decimal) -> Decimal:
    """Use the installed official CLOB order builder's size rounding contract."""
    from py_clob_client_v2.order_builder.builder import ROUNDING_CONFIG

    key = format(tick_size.normalize(), "f")
    rounding = ROUNDING_CONFIG.get(key)
    if rounding is None:
        raise MIReadinessError("VENUE_QUANTITY_PRECISION_UNKNOWN")
    return Decimal("1").scaleb(-rounding.size)


def _best(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def build_snapshot(
    meta: MarketMeta,
    market_data: MarketDataService,
    account: AccountSnapshot,
    epoch: int,
    now: float,
) -> MakerInventorySnapshot:
    """Map live official state without deriving any MI strategy quantities here."""
    yes, no = market_data.view(meta.yes.token_id), market_data.view(meta.no.token_id)
    y_book, n_book = market_data.book(meta.yes.token_id), market_data.book(meta.no.token_id)
    valid = market_data.books_fresh(meta)
    if y_book is None or n_book is None or y_book.tick_size != n_book.tick_size:
        valid = False
    tick = Decimal(str(y_book.tick_size if y_book is not None else meta.tick_size))
    return MakerInventorySnapshot(
        yes=MakerInventoryBook(_best(yes.best_bid), _best(yes.best_ask)),
        no=MakerInventoryBook(_best(no.best_bid), _best(no.best_ask)),
        confirmed_yes_qty=Decimal("0"),
        confirmed_no_qty=Decimal("0"),
        cash_available_for_new_order=account.cash_available,
        elapsed_s=Decimal(str(max(0.0, now - epoch))),
        data_valid=valid,
        tick_size=tick,
        venue_min_order_size=Decimal(str(meta.min_order_size)),
        quantity_step=venue_quantity_step(tick),
    )


def _number(value: Decimal | int | float | str | None) -> str | None:
    return str(value) if value is not None else None


def decision_record(
    meta: MarketMeta,
    snapshot: MakerInventorySnapshot,
    decision: MakerInventoryDecision,
    now: float,
    state: MakerInventoryState | None = None,
) -> dict[str, Any]:
    y, n = snapshot.yes, snapshot.no
    mid_y = (Decimal(str(y.best_bid)) + Decimal(str(y.best_ask))) / 2 if (
        y.best_bid is not None and y.best_ask is not None and snapshot.data_valid
    ) else None
    mid_n = (Decimal(str(n.best_bid)) + Decimal(str(n.best_ask))) / 2 if (
        n.best_bid is not None and n.best_ask is not None and snapshot.data_valid
    ) else None
    return {
        "event": "INVENTORY_DECISION", "schema_version": 1,
        "timestamp_utc": datetime.fromtimestamp(now, UTC).isoformat(),
        "timestamp_epoch": now,
        "condition_id": meta.condition_id, "market_slug": meta.slug,
        "yes_token_id": meta.yes.token_id, "no_token_id": meta.no.token_id,
        "yes_outcome": meta.yes.outcome, "no_outcome": meta.no.outcome,
        "bidY": _number(y.best_bid), "askY": _number(y.best_ask),
        "bidN": _number(n.best_bid), "askN": _number(n.best_ask),
        "data_valid": snapshot.data_valid, "tick_size": str(snapshot.tick_size),
        "quantity_step": str(snapshot.quantity_step),
        "venue_min_order_size": str(snapshot.venue_min_order_size),
        "midY": _number(mid_y), "midN": _number(mid_n),
        "p": _number(decision.p), "N_target": _number(decision.n_target),
        "qY": str(snapshot.confirmed_yes_qty), "qN": str(snapshot.confirmed_no_qty),
        "N_actual": str(decision.n_actual), "delta": _number(decision.delta),
        "tracking_error": _number(decision.tracking_error),
        "available_cash": str(snapshot.cash_available_for_new_order),
        "working_order_state": state.order.lifecycle.value if state and state.order else "NO_WORKING_ORDER",
        "desired_action": decision.action.value,
        "decision_side": decision.side.value if decision.side else None,
        "decision_outcome": decision.outcome.value if decision.outcome else None,
        "decision_price": _number(decision.price), "decision_qty": _number(decision.qty),
        "decision_reason": decision.reason,
        "estimated_cash_requirement": _number(
            decision.price * decision.qty if decision.price is not None and decision.qty is not None else None
        ),
        "potential_inventory_min": str(decision.potential_inventory_min),
        "potential_inventory_max": str(decision.potential_inventory_max),
        "reducing_old_exposure": decision.reducing_old_exposure,
        "execution_status": "NO_SEND",
        "execution": "SUPPRESSED_NO_SEND",
    }


@dataclass(slots=True)
class NoSendDecisionSink:
    directory: Path
    _file: Any = None
    count: int = 0

    def __enter__(self) -> NoSendDecisionSink:
        self.directory.mkdir(parents=True, exist_ok=False)
        self._file = (self.directory / "inventory_decisions.jsonl").open("x", encoding="utf-8")
        return self

    def record(self, row: dict[str, Any]) -> None:
        if self._file is None:
            raise RuntimeError("NO-SEND sink is closed")
        self._file.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        self._file.flush()
        self.count += 1

    def __exit__(self, *_exc: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def evaluate_and_record(
    meta: MarketMeta,
    snapshot: MakerInventorySnapshot,
    sink: NoSendDecisionSink,
    now: float,
    state: MakerInventoryState | None = None,
) -> MakerInventoryDecision:
    decision = evaluate(snapshot, state or MakerInventoryState())
    sink.record(decision_record(meta, snapshot, decision, now, state))
    return decision


async def run_inventory_no_send(config_dir: str, *, once: bool, research: bool, no_send: bool) -> Path:
    """Observe exactly one official BTC5m market. No execution object is constructed."""
    if not (once and research and no_send):
        raise MIReadinessError("M2A_REQUIRES_ONCE_RESEARCH_NO_SEND")
    cfg = Config.load(config_dir)
    validate_official_hosts(cfg)
    account_reader: AccountReader = OfficialReadOnlyAccount(cfg)
    await account_reader.connect()

    while True:
        epoch = choose_epoch(time.time())
        if epoch > time.time():
            await asyncio.sleep(max(0.0, epoch - time.time()))
        if time.time() >= epoch + PERIOD_S - 15:
            continue
        try:
            meta = await resolve_btc5m_market(
                cfg, slug_for_epoch(epoch), cache=False, strict_metadata=True
            )
        except LookupError:
            await asyncio.sleep(1.5)
            continue
        validate_market(meta, epoch)
        account = await account_reader.snapshot(meta)
        assert_clean_start(account, meta)
        if time.time() >= epoch + PERIOD_S - 15:
            continue
        break

    session = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "_mi_" + str(epoch)
    directory = Path(cfg.paths.log_dir) / "runs" / session
    dirty = asyncio.Event()
    md = MarketDataService(on_dirty=lambda cid, _token: dirty.set() if cid == meta.condition_id else None,
                           on_connection=lambda _up: dirty.set(), proxy=cfg.proxy)
    md.set_markets([(meta.condition_id, [meta.yes.token_id, meta.no.token_id])])
    status = "INCOMPLETE"
    counts: dict[str, int] = {}
    valid_evaluations = 0
    error: str | None = None
    task: asyncio.Task[None] | None = None
    with NoSendDecisionSink(directory) as sink:
        task = asyncio.create_task(md.run(), name="mi-market-ws")
        try:
            last_top: tuple[Any, ...] | None = None
            cutoff = epoch + PERIOD_S - 15
            end = epoch + PERIOD_S
            while time.time() < end:
                now = time.time()
                deadline = cutoff if now < cutoff else end
                try:
                    await asyncio.wait_for(dirty.wait(), timeout=max(0.001, deadline - now))
                    dirty.clear()
                except TimeoutError:
                    pass
                now = time.time()
                if now >= end:
                    break
                y, n = md.view(meta.yes.token_id), md.view(meta.no.token_id)
                top = (y.best_bid, y.best_ask, n.best_bid, n.best_ask,
                       md.connected, md.connection_generation)
                if top == last_top and now < cutoff:
                    continue
                last_top = top
                latest_account = await account_reader.snapshot(meta)
                assert_clean_start(latest_account, meta)
                evaluation_now = time.time()
                snapshot = build_snapshot(meta, md, latest_account, epoch, evaluation_now)
                result = evaluate_and_record(meta, snapshot, sink, evaluation_now)
                counts[result.action.value] = counts.get(result.action.value, 0) + 1
                if result.p is not None:
                    valid_evaluations += 1
                if evaluation_now >= cutoff:
                    # One exact cutoff evaluation records the M1 time gate.
                    # Observe the remaining window without repeating WAIT for every WS update.
                    await asyncio.sleep(max(0.0, end - time.time()))
                    break
            if valid_evaluations == 0:
                raise MIReadinessError("NO_VALID_BOOK")
            status = "COMPLETED"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            md.stop()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            report = {
                "schema_version": 1, "strategy": "MakerInventory", "slice": "M2A_NO_SEND",
                "status": status, "error": error, "market_slug": meta.slug,
                "condition_id": meta.condition_id, "yes_token_id": meta.yes.token_id,
                "no_token_id": meta.no.token_id, "yes_outcome": meta.yes.outcome,
                "no_outcome": meta.no.outcome, "market_start_epoch": epoch,
                "market_end_epoch": epoch + PERIOD_S, "evaluate_count": sink.count,
                "valid_evaluate_count": valid_evaluations,
                "desired_actions": counts, "execution_calls": 0,
                "cancel_calls": 0, "fills_caused_by_mi": 0,
                "completed_at_utc": datetime.now(UTC).isoformat(),
            }
            (directory / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return directory
