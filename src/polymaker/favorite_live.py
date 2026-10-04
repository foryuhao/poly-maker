"""One selected BTC5m Favorite-First live session; no rollover."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from rich.console import Console

from polymaker.btc5m import (
    PERIOD_S,
    PROFILE,
    RESOLUTION_RETRY_S,
    next_epoch,
    resolve_btc5m_market,
    slug_for_epoch,
)
from polymaker.config import Config, MarketEntry
from polymaker.doctor import _user_ws_auth
from polymaker.domain import MarketMeta
from polymaker.favorite_report import (
    attach_raw_log,
    detach_raw_log,
    gross_paired_pnl,
    run_id,
    write_artifacts,
)
from polymaker.research.identity import (
    ResearchConfigIdentity,
    normalize_favorite_config,
    research_config_identity,
    validate_research_config_identity,
)
from polymaker.research.observer import ResearchConfig, ResearchObserver
from polymaker.research.schema import DatasetManifest
from polymaker.research.writer import ResearchWriter
from polymaker.strategy.favorite_first import FavoriteFirstConfig, StrategyState


class LiveResult(str, Enum):
    PREFLIGHT_PASS = "PREFLIGHT_PASS"
    COMPLETED = "COMPLETED"
    ABORTED_BEFORE_TRADING = "ABORTED_BEFORE_TRADING"
    HALTED_DURING_MARKET = "HALTED_DURING_MARKET"
    UNPAIRABLE_RESIDUAL = "UNPAIRABLE_RESIDUAL"
    INCOMPLETE_RECONCILIATION = "INCOMPLETE_RECONCILIATION"


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreflightSnapshot:
    credential_ready: bool
    wallet_ready: bool
    balance_usd: float | None
    allowance_usd: float | None
    yes_position: float
    no_position: float
    owned_open_orders: int
    market_ws_ready: bool
    user_ws_ready: bool
    fill_truth_ready: bool


CONTROLLED_CONFIG = FavoriteFirstConfig(
    max_pair_loss_usd=0.50,
    runner_passive_offset_ticks=2,
    normal_reprice_min_s=0.75,
    urgent_reprice_min_s=0.30,
)
COLLATERAL_REQUIRED_USD = 12.0  # two 5-share BUY orders at < $1, plus 20% buffer
PREFLIGHT_LEAD_S = 90.0
PREFLIGHT_TIMEOUT_S = 45.0


def effective_config(config: FavoriteFirstConfig) -> dict[str, Any]:
    return normalize_favorite_config(config)


def research_manifest(identity: ResearchConfigIdentity, meta: MarketMeta, *,
                      started_at_us: int, code_commit: str = "UNKNOWN") -> DatasetManifest:
    """Build the live session Manifest from its already-owned identity."""
    validate_research_config_identity(identity, identity.strategy_config,
                                      dataset_id=identity.dataset_id,
                                      session_id=identity.session_id)
    return DatasetManifest(
        schema_version=1, dataset_id=identity.dataset_id,
        session_id=identity.session_id, code_commit=code_commit,
        strategy_config_id=identity.config_id,
        effective_strategy_config=identity.effective_config,
        config_sha256=identity.config_id,
        feature_version=None, label_version=None, policy_version=None,
        execution_version=None,
        payload_versions={"ResearchEvent": 1, "Episode": 1, "StateSnapshot": 1},
        condition_id=meta.condition_id, market_slug=meta.slug,
        yes_token_id=meta.yes.token_id, no_token_id=meta.no.token_id,
        split_boundaries_us={}, started_at_us=started_at_us,
        ended_at_us=None, file_sha256={}, event_watermark=0,
        gap_count=0, close_status="OPEN")


def validate_meta(meta: MarketMeta, slug: str, epoch: int, qty: float) -> None:
    if (
        meta.slug != slug
        or not meta.condition_id
        or not meta.yes.token_id
        or not meta.no.token_id
        or meta.yes.token_id == meta.no.token_id
    ):
        raise PreflightError("selected market identity or YES/NO tokens are invalid")
    if (
        not math.isfinite(meta.tick_size)
        or meta.tick_size <= 0
        or meta.tick_size >= 1
        or not math.isfinite(meta.min_order_size)
        or meta.min_order_size <= 0
    ):
        raise PreflightError("selected market tick or minimum size is invalid")
    if meta.min_order_size > qty + 1e-9:
        raise PreflightError("selected market minimum exceeds five-share limit")
    if not meta.end_date_iso:
        raise PreflightError("selected market has no end timestamp")
    try:
        end = (
            datetime.fromisoformat(meta.end_date_iso.replace("Z", "+00:00"))
            .astimezone(UTC)
            .timestamp()
        )
    except (TypeError, ValueError) as exc:
        raise PreflightError("selected market end timestamp is invalid") from exc
    if abs(end - (epoch + PERIOD_S)) > 2:
        raise PreflightError("selected market timing differs from committed BTC5m interval")


def validate_preflight(s: PreflightSnapshot) -> None:
    failures = []
    if not s.credential_ready:
        failures.append("private key or API authentication unavailable")
    if not s.wallet_ready:
        failures.append("wallet/funder identity invalid")
    if s.balance_usd is None or s.balance_usd + 1e-9 < COLLATERAL_REQUIRED_USD:
        failures.append("usable collateral below $12.00")
    if s.allowance_usd is None or s.allowance_usd + 1e-9 < COLLATERAL_REQUIRED_USD:
        failures.append("collateral allowance below $12.00 or unverified")
    if (
        not math.isfinite(s.yes_position)
        or not math.isfinite(s.no_position)
        or abs(s.yes_position) > 1e-8
        or abs(s.no_position) > 1e-8
    ):
        failures.append("selected-market account baseline is nonzero")
    if s.owned_open_orders:
        failures.append("selected-market open-order cleanup is unverified")
    if not s.market_ws_ready:
        failures.append("both Market WS books are not generation-ready")
    if not s.user_ws_ready:
        failures.append("authenticated User WS is not ready")
    if not s.fill_truth_ready:
        failures.append("Fill Truth or startup reconciliation is unresolved")
    if failures:
        raise PreflightError("; ".join(failures))


def _microusd(value: Any) -> float | None:
    try:
        amount = float(value) / 1_000_000
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def collateral_readiness(
    raw: Any, required_spenders: tuple[str, ...] = ()
) -> tuple[float | None, float | None]:
    """CLOB collateral balance/allowance values use six decimal places."""
    if not isinstance(raw, dict):
        return None, None
    balance = _microusd(raw.get("balance"))
    allowances = raw.get("allowances")
    if not isinstance(allowances, dict) or not required_spenders:
        return balance, None
    normalized = {str(key).lower(): value for key, value in allowances.items()}
    values = [_microusd(normalized.get(spender.lower())) for spender in required_spenders]
    return balance, min(values) if all(v is not None for v in values) else None


def _wallet_identity(cfg: Config, engine: Any) -> bool:
    if not cfg.secrets.pk or not cfg.secrets.browser_address:
        return False
    if cfg.wallet.signature_type not in (0, 1, 2, 3):
        return False
    try:
        from eth_account import Account
        from web3 import Web3

        signer = Account.from_key(cfg.secrets.pk).address
        funder = Web3.to_checksum_address(cfg.secrets.browser_address)
        if cfg.wallet.signature_type == 0 and signer != funder:
            return False
        return (
            engine.gateway.address.lower() == signer.lower()
            and engine.gateway.funder.lower() == funder.lower()
            and bool(engine.gateway.creds)
        )
    except (ValueError, TypeError, AttributeError):
        return False


def _selected_orders(orders: list[Any], meta: MarketMeta) -> list[Any]:
    tokens = {meta.yes.token_id, meta.no.token_id}
    return [o for o in orders if o.token_id in tokens]


async def _account_baseline(engine: Any, meta: MarketMeta) -> tuple[float, float]:
    """Read the selected-market account baseline from the Polymarket Data API."""
    positions = await engine.gateway.positions()
    yes = positions.get(meta.yes.token_id, (0.0, 0.0))[0]
    no = positions.get(meta.no.token_id, (0.0, 0.0))[0]
    return yes, no


async def _preflight(
    engine: Any,
    cfg: Config,
    meta: MarketMeta,
    *,
    now: Callable[[], float],
    sleep: Callable[[float], Awaitable[Any]],
    deadline: float,
) -> PreflightSnapshot:
    cid = meta.condition_id
    while now() < deadline:
        if (
            engine._market_books_fresh(cid)
            and not engine._market_recovery_pending
            and engine.user is not None
            and engine.user.connected
        ):
            break
        await sleep(min(0.25, max(0.0, deadline - now())))
    market_ready = (
        engine._market_books_fresh(cid)
        and not engine._market_recovery_pending
        and engine.md.metrics["queue_full"] == 0
    )
    ws_ready = bool(engine.user is not None and engine.user.connected)
    if ws_ready:
        ws_ready, _ = await _user_ws_auth(engine.gateway.creds, [cid], cfg.proxy)
    yes, no = await _account_baseline(engine, meta)
    spenders = await engine.gateway.required_collateral_spenders(meta)
    balance, allowance = collateral_readiness(
        await engine.gateway.balance_allowance(), spenders
    )
    orders = _selected_orders(await engine.gateway.open_orders(), meta)
    economics = engine._favorite_economics(cid)
    s = PreflightSnapshot(
        credential_ready=bool(engine.gateway.creds),
        wallet_ready=_wallet_identity(cfg, engine),
        balance_usd=balance,
        allowance_usd=allowance,
        yes_position=yes,
        no_position=no,
        owned_open_orders=len(orders),
        market_ws_ready=market_ready,
        user_ws_ready=ws_ready,
        fill_truth_ready=(
            not economics["first_qty"]
            and not economics["pair_qty"]
            and not economics["mismatch"]
            and cid not in engine._halted
            and not engine._fill_recovery_pending
            and not engine._order_state_uncertain
            and not engine.user_proc.has_quarantine_for_condition(cid)
        ),
    )
    validate_preflight(s)
    return s


def _decision_events(path: Path, cid: str, since: float) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    with path.open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
                if (
                    row.get("kind") == "favorite_first_decision"
                    and row.get("ts", 0) >= since
                    and row.get("data", {}).get("cid") == cid
                ):
                    events.append(row)
            except (ValueError, AttributeError, TypeError):
                continue
    return events


def attribution_report(
    meta: MarketMeta,
    epoch: int,
    config: FavoriteFirstConfig,
    engine: Any,
    final: dict[str, Any],
    since: float,
) -> dict[str, Any]:
    """Summarize existing scoped fill truth and decision journal, without inventing settlement."""
    cid = meta.condition_id
    first = engine.user_proc.economic_fills_for_orders(engine._favorite_first_orders[cid])
    pair = engine.user_proc.economic_fills_for_orders(engine._favorite_pair_orders[cid])
    first_orders = sorted(
        (
            row
            for oid in engine._favorite_first_orders[cid]
            if (row := engine.state.owned_order(oid)) is not None
        ),
        key=lambda row: row["created_ts"],
    )
    pair_orders = sorted(
        (
            row
            for oid in engine._favorite_pair_orders[cid]
            if (row := engine.state.owned_order(oid)) is not None
        ),
        key=lambda row: row["created_ts"],
    )
    decisions = _decision_events(Path(engine.cfg.paths.journal_dir) / "live.jsonl", cid, since)
    data = [row["data"] for row in decisions]
    first_quote_row = next(
        (row for row in decisions if row["data"].get("strategy_state") == "ENTRY_QUOTING"
         and row["data"].get("desired_price") is not None),
        None,
    )
    first_quote = first_quote_row["data"] if first_quote_row else None
    risk_decision = next(
        (
            row
            for row in decisions
            if row["data"].get("strategy_state") in ("RUNNER", "PAIR_STOP", "UNPAIRABLE_RESIDUAL")
        ),
        None,
    )
    first_prices = [(row["created_ts"], row["submitted_price"]) for row in first_orders]
    pair_prices = [(row["created_ts"], row["submitted_price"]) for row in pair_orders]
    economics = final["economics"]
    first_qty, pair_qty = economics["first_qty"], economics["pair_qty"]
    paired = min(first_qty, pair_qty)
    paired_pnl = gross_paired_pnl(first_qty, economics["first_vwap"],
                                  pair_qty, economics["pair_vwap"])
    memory = engine._favorite[cid][2]
    timeline = [
        {
            "elapsed": round(row["ts"] - epoch, 2),
            "state": row["data"].get("strategy_state"),
            "quote": row["data"].get("desired_price"),
            "quote_mode": row["data"].get("pair_quote_mode"),
            "total_lockable_pnl": row["data"].get("total_lockable_pnl"),
            "trigger": row["data"].get("pair_trigger"),
        }
        for row in decisions
    ]
    timeline.extend(
        {"elapsed": round(f.ts - epoch, 2) if f.ts > 0 else None,
         "state": "FIRST_FILL", "qty": f.size, "price": f.price}
        for f in first
    )
    timeline.extend(
        {"elapsed": round(f.ts - epoch, 2) if f.ts > 0 else None,
         "state": "PAIR_FILL", "qty": f.size, "price": f.price}
        for f in pair
    )
    timeline.extend(
        {
            "elapsed": round(row["created_ts"] - epoch, 2),
            "state": "FIRST_ORDER",
            "qty": row["submitted_qty"],
            "price": row["submitted_price"],
        }
        for row in first_orders
    )
    timeline.extend(
        {
            "elapsed": round(row["created_ts"] - epoch, 2),
            "state": "PAIR_ORDER",
            "qty": row["submitted_qty"],
            "price": row["submitted_price"],
        }
        for row in pair_orders
    )
    timeline.sort(key=lambda row: row["elapsed"] if row["elapsed"] is not None else float("inf"))
    def fill_data(fills: list[Any], orders: list[Any], outcome: str | None) -> list[dict[str, Any]]:
        return [{"timestamp": f.ts if f.ts > 0 else None,
                 "order_id": getattr(f, "maker_order_id", None),
                 "trade_id": getattr(f, "trade_id", None),
                 "token": getattr(f, "token_id", None),
                 "outcome": outcome,
                 "side": getattr(getattr(f, "our_side", None), "value", "BUY"),
                 "fill_qty": f.size, "fill_price": f.price}
                for f in sorted(fills, key=lambda item: item.ts if item.ts > 0 else float("inf"))]

    def order_data(orders: list[Any]) -> list[dict[str, Any]]:
        return [{"timestamp": row["created_ts"], "order_id": row.get("order_id"),
                 "qty": row["submitted_qty"], "price": row["submitted_price"],
                 "token": row.get("token_id"), "side": row.get("side", "BUY")}
                for row in orders]
    return {
        "market": meta.slug,
        "condition_id": cid,
        "start": epoch,
        "end": epoch + PERIOD_S,
        "effective_config": effective_config(config),
        "favorite_at_entry": (first_quote or {}).get("favorite_side"),
        "favorite_probability_at_entry": (first_quote or {}).get("favorite_probability"),
        "first_quote_time": first_quote_row["ts"] if first_quote_row else None,
        "first_quote_price": first_quote.get("desired_price") if first_quote else None,
        "first_quote_reprices": max(0, len(first_prices) - 1),
        "first_fill_time": min((f.ts for f in first if f.ts > 0), default=None),
        "first_fill_path": [(f.ts, f.size, f.price) for f in sorted(first, key=lambda f: f.ts)],
        "first_leg_final_qty": memory.first_leg_final_qty,
        "first_leg_vwap": economics["first_vwap"] if first_qty else None,
        "risk_decision_time": risk_decision["ts"] if risk_decision else None,
        "risk_decision_total_lockable_pnl": (risk_decision or {})
        .get("data", {})
        .get("total_lockable_pnl"),
        "decision": (risk_decision or {}).get("data", {}).get("strategy_state"),
        "pair_quote_history": pair_prices,
        "pair_fill_path": [(f.ts, f.size, f.price) for f in sorted(pair, key=lambda f: f.ts)],
        "peak_total_lockable_pnl": memory.peak_locked_pnl,
        "trailing_floor": next(
            (d["trailing_floor"] for d in reversed(data) if d.get("trailing_floor") is not None),
            None,
        ),
        "pair_trigger": memory.pair_trigger.value if memory.pair_trigger else None,
        "t240_status": next(
            (
                d.get("strategy_state")
                for row, d in zip(decisions, data, strict=True)
                if row["ts"] >= epoch + 240
            ),
            None,
        ),
        "yes_qty": final["yes_qty"],
        "no_qty": final["no_qty"],
        "paired_qty": paired,
        "residual_qty": max(0.0, first_qty - pair_qty),
        "residual_side": economics.get("first_side") if first_qty > pair_qty + 1e-8 else None,
        "paired_locked_pnl": paired_pnl,
        "residual_or_settlement_pnl": None,
        "maker_rebate": None,
        "total_settled_pnl": None,
        "owned_open_orders": final["owned_open_orders"],
        "fill_truth_complete": final["fill_truth_complete"],
        "position_reconciled": final["position_reconciled"],
        "strategy_state": memory.state.value,
        "residual_reason": memory.residual_reason,
        "timeline": timeline,
        "decisions": decisions,
        "first_orders": order_data(first_orders),
        "pair_orders": order_data(pair_orders),
        "first_fills": fill_data(first, first_orders, economics.get("first_side")),
        "pair_fills": fill_data(pair, pair_orders,
                                "NO" if economics.get("first_side") == "YES" else "YES"),
    }


def _classify_final(engine: Any, cid: str, final: dict[str, Any], report: dict[str, Any]) -> LiveResult:
    if (final["owned_open_orders"] or not final["fill_truth_complete"]
            or not final["position_reconciled"]):
        return LiveResult.INCOMPLETE_RECONCILIATION
    memory = engine._favorite[cid][2]
    runtime_halt = engine.favorite_halt_reason(cid)
    if runtime_halt or (memory.state is StrategyState.HALTED
                        and memory.residual_reason != "T270_RESIDUAL_INVENTORY"):
        return LiveResult.HALTED_DURING_MARKET
    if (report.get("decision") == StrategyState.UNPAIRABLE_RESIDUAL.value
            or memory.state is StrategyState.UNPAIRABLE_RESIDUAL):
        return LiveResult.UNPAIRABLE_RESIDUAL
    return LiveResult.COMPLETED


async def run_favorite_first_live(
    config_dir: str,
    console: Console,
    *,
    preflight_only: bool,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    config_loader: Callable[[str], Config] = Config.load,
    resolver: Callable[[Config, str], Awaitable[MarketMeta]] = resolve_btc5m_market,
    engine_factory: Callable[..., Any] | None = None,
    research_config: ResearchConfig | None = None,
) -> LiveResult:
    """Commit to one upcoming exact slug, preflight, arm once, clean up, exit."""
    from polymaker.engine import Engine

    engine = None
    armed = False
    phase = "configuration"
    committed_meta: MarketMeta | None = None
    epoch = 0
    selected_at = 0.0
    preflight_pass_at: float | None = None
    finalization_started_at: float | None = None
    artifact_dir: Path | None = None
    raw_handler: logging.FileHandler | None = None
    final_task: asyncio.Task[tuple[dict[str, Any], dict[str, Any]]] | None = None
    research_writer: ResearchWriter | None = None
    favorite_config = CONTROLLED_CONFIG

    def persist(report: dict[str, Any], result: LiveResult) -> None:
        if artifact_dir is None or committed_meta is None or "start" not in report:
            return
        report.update(run_id=artifact_dir.name, selected_at=selected_at,
                      preflight_pass_at=preflight_pass_at,
                      finalization_started_at=finalization_started_at,
                      finalized_at=now(), selected_tick=committed_meta.tick_size,
                      min_order_size=committed_meta.min_order_size)
        try:
            write_artifacts(report, result.value, artifact_dir)
            console.print(f"Run artifacts: {artifact_dir / 'chat_report.md'} | "
                          f"{artifact_dir / 'run.json'} | {artifact_dir / 'raw.log'}")
        except Exception as exc:  # noqa: BLE001 - observability must not alter trading result
            console.print(f"Observability artifact failure: {type(exc).__name__}")

    def unarmed_report() -> dict[str, Any]:
        assert committed_meta is not None
        return {"market": committed_meta.slug, "condition_id": committed_meta.condition_id,
                "start": epoch, "effective_config": effective_config(favorite_config),
                "first_orders": [], "pair_orders": [], "first_fills": [], "pair_fills": [],
                "first_leg_final_qty": 0, "strategy_state": "NOT_ARMED",
                "yes_qty": None, "no_qty": None, "owned_open_orders": None,
                "fill_truth_complete": None, "position_reconciled": None,
                "decisions": []}

    async def verified_finalize_once() -> tuple[dict[str, Any], dict[str, Any]]:
        nonlocal final_task, finalization_started_at
        assert engine is not None and committed_meta is not None
        if final_task is None:
            finalization_started_at = now()
            # Synchronous disarm happens before the cleanup task can yield.
            engine.disarm_favorite_first(committed_meta.condition_id)

            async def cleanup() -> tuple[dict[str, Any], dict[str, Any]]:
                final = await engine.finalize_favorite_market(committed_meta.condition_id)
                report = attribution_report(
                    committed_meta, epoch, favorite_config, engine, final, selected_at
                )
                return final, report

            final_task = asyncio.create_task(cleanup())
        interrupted = False
        while True:
            try:
                result = await asyncio.shield(final_task)
                if interrupted:
                    raise asyncio.CancelledError
                return result
            except asyncio.CancelledError:
                if final_task.done() and interrupted:
                    raise
                interrupted = True
                # The caller remains interrupted; keep the one scoped cleanup
                # running through any repeated task cancellation.
    try:
        cfg = config_loader(config_dir)
        favorite_profile = cfg.profiles.get(PROFILE)
        if favorite_profile is None:
            raise PreflightError(f"FavoriteFirst profile is missing: {PROFILE}")
        favorite_config = replace(
            CONTROLLED_CONFIG,
            favorite_min=favorite_profile.favorite_min,
            favorite_max=favorite_profile.favorite_max,
            entry_start_s=favorite_profile.entry_start_s,
            last_new_entry_s=favorite_profile.last_new_entry_s,
        )
        if not cfg.secrets.pk:
            raise PreflightError("private key is missing")
        if not cfg.secrets.browser_address:
            raise PreflightError("wallet/funder address is missing")
        if cfg.wallet.signature_type not in (0, 1, 2, 3):
            raise PreflightError("signature type is invalid")
        if not cfg.engine.heartbeat:
            raise PreflightError("live heartbeat must be enabled")
        if not cfg.engine.journal:
            raise PreflightError("decision journal must be enabled")
        phase = "market discovery"
        selected_at = now()
        epoch = next_epoch(selected_at)
        if epoch - selected_at < PREFLIGHT_LEAD_S:
            epoch += PERIOD_S
        slug = slug_for_epoch(epoch)
        artifact_dir = Path(getattr(cfg.paths, "log_dir", cfg.paths.journal_dir)) / "runs" / run_id(epoch, slug, selected_at)
        raw_handler = attach_raw_log(artifact_dir / "raw.log")
        # This exact slug is fixed before discovery retries. Never select B
        # after committing to A, even if A becomes unavailable.
        deadline = epoch - PREFLIGHT_TIMEOUT_S
        while True:
            try:
                meta = await resolver(cfg, slug)
                break
            except LookupError as exc:
                if now() >= deadline:
                    raise PreflightError("committed BTC5m market unavailable") from exc
                await sleep(min(RESOLUTION_RETRY_S, max(0.0, deadline - now())))
        validate_meta(meta, slug, epoch, favorite_config.first_leg_qty)
        committed_meta = meta
        console.print(
            f"Committed market: {slug} | {meta.condition_id} | YES {meta.yes.token_id} "
            f"| NO {meta.no.token_id} | {epoch} → {epoch + PERIOD_S} "
            f"| tick {meta.tick_size} | min {meta.min_order_size}"
        )
        cfg.markets = [MarketEntry(slug=slug, profile=PROFILE, enabled=True)]
        phase = "Engine startup and account reconciliation"
        research_kwargs: dict[str, Any] = {}
        if research_config is not None and research_config.enabled:
            try:
                observer = ResearchObserver(research_config)
                assert observer.queue is not None
                identity = research_config_identity(favorite_config, dataset_id="m4a",
                                                     session_id=artifact_dir.name)
                manifest = research_manifest(identity, meta,
                                             started_at_us=time.time_ns() // 1000)
                research_writer = ResearchWriter(Path(research_config.output_root),
                    identity.dataset_id, identity.session_id, observer.queue, manifest=manifest,
                    flush_interval_s=research_config.flush_interval_s,
                    fsync_interval_s=research_config.fsync_interval_s)
                research_writer.start()
                research_kwargs = {"research_observer": observer,
                                   "research_session_id": identity.session_id,
                                   "research_config_identity": identity}
            except Exception as exc:  # Research must not prevent trading startup.
                research_writer = None
                console.print(f"Research capture unavailable: {type(exc).__name__}")
        engine = (engine_factory or Engine)(
            cfg, paper=False, favorite_first=favorite_config, market_start_ts=epoch,
            **research_kwargs,
        )
        if research_writer is not None:
            def research_failure_count() -> int:
                capture = getattr(engine, "research", None)
                return observer.failures + (capture.failures if capture is not None else 0)
            research_writer.failure_count_provider = research_failure_count
        await engine.start(arm_favorite=False)
        if len(engine.metas) != 1 or meta.condition_id not in engine.metas:
            raise PreflightError("Engine resolved a different market")
        live_meta = engine.metas[meta.condition_id]
        validate_meta(live_meta, slug, epoch, favorite_config.first_leg_qty)
        if live_meta.yes.token_id != meta.yes.token_id or live_meta.no.token_id != meta.no.token_id:
            raise PreflightError("committed market tokens changed")
        phase = "feed, wallet, and collateral preflight"
        snapshot = await _preflight(
            engine,
            cfg,
            live_meta,
            now=now,
            sleep=sleep,
            deadline=min(epoch - 1, now() + PREFLIGHT_TIMEOUT_S),
        )
        preflight_pass_at = now()
        console.print("LIVE PREFLIGHT PASS")
        console.print(f"Market {slug} | YES {live_meta.yes.token_id} | NO {live_meta.no.token_id}")
        console.print(
            f"Window {epoch} → {epoch + PERIOD_S} | tick {live_meta.tick_size} "
            f"| min order {live_meta.min_order_size}"
        )
        console.print(
            "Baseline YES 0 / NO 0 | owned open orders 0 | Market WS READY "
            "| User WS READY | Execution READY"
        )
        console.print(
            f"Signer {engine.gateway.address[:10]}… | funder "
            f"{engine.gateway.funder[:10]}… | signature type "
            f"{cfg.wallet.signature_type}"
        )
        console.print(f"FavoriteFirst v0.3 effective config: {effective_config(favorite_config)}")
        console.print(
            f"Collateral ${snapshot.balance_usd:.2f} | verified allowance "
            f"${snapshot.allowance_usd:.2f}"
        )
        if preflight_only:
            persist(unarmed_report(), LiveResult.PREFLIGHT_PASS)
            return LiveResult.PREFLIGHT_PASS
        phase = "final committed-market preflight"
        if now() >= epoch:
            raise PreflightError("preflight missed selected market start")
        # Revalidate the committed slug immediately before arming. Never switch.
        latest = await resolver(cfg, slug)
        validate_meta(latest, slug, epoch, favorite_config.first_leg_qty)
        if latest.condition_id != meta.condition_id or latest.tokens != meta.tokens:
            raise PreflightError("committed market identity changed")
        await _preflight(
            engine,
            cfg,
            live_meta,
            now=now,
            sleep=sleep,
            deadline=min(epoch - 0.1, now() + PREFLIGHT_TIMEOUT_S),
        )
        if now() >= epoch:
            raise PreflightError("final preflight missed selected market start")
        engine.arm_favorite_first(meta.condition_id)
        armed = True
        phase = "armed market lifecycle"
        console.print("ARMED: exactly one BTC5m market")
        await sleep(max(0.0, epoch + PERIOD_S - now()))
        phase = "end-of-market reconciliation"
        final, report = await verified_finalize_once()
        result = _classify_final(engine, meta.condition_id, final, report)
        persist(report, result)
        return result
    except asyncio.CancelledError:
        if armed and engine is not None and committed_meta is not None:
            try:
                final, report = await verified_finalize_once()
                status = _classify_final(engine, committed_meta.condition_id, final, report)
                persist(report, status)
                console.print(f"INTERRUPTED: verified cleanup status {status.value}")
            except Exception as cleanup_exc:  # noqa: BLE001
                console.print(f"INTERRUPTED: INCOMPLETE_RECONCILIATION ({type(cleanup_exc).__name__})")
        raise
    except PreflightError as exc:
        console.print(
            f"ABORTED BEFORE TRADING: {exc}" if not armed else f"HALTED DURING MARKET: {exc}"
        )
        status = LiveResult.ABORTED_BEFORE_TRADING if not armed else LiveResult.HALTED_DURING_MARKET
        if not armed and committed_meta is not None:
            persist(unarmed_report(), status)
        return status
    except Exception as exc:  # noqa: BLE001 - fail closed; do not expose credential-bearing errors
        console.print(
            ("ABORTED BEFORE TRADING" if not armed else "HALTED DURING MARKET")
            + f": {phase} failed ({type(exc).__name__})"
        )
        if armed and engine is not None and committed_meta is not None:
            try:
                _final, report = await verified_finalize_once()
                persist(report, LiveResult.INCOMPLETE_RECONCILIATION)
            except Exception as cleanup_exc:  # noqa: BLE001 - report safe failure class only
                console.print(f"INCOMPLETE RECONCILIATION: {type(cleanup_exc).__name__}")
        elif committed_meta is not None:
            persist(unarmed_report(), LiveResult.ABORTED_BEFORE_TRADING)
        return (
            LiveResult.ABORTED_BEFORE_TRADING if not armed else LiveResult.INCOMPLETE_RECONCILIATION
        )
    finally:
        if engine is not None:
            lifecycle = getattr(engine, "research_lifecycle", None)
            if lifecycle is not None and research_writer is not None:
                try:
                    research_capture = getattr(engine, "research", None)
                    if (research_writer.error is not None
                            or research_writer.queue.counters.rejected_full
                            or (research_capture is not None
                                and research_capture.lost_events)):
                        await lifecycle.close("WRITER_FAILURE")
                except Exception:
                    capture = getattr(engine, "research", None)
                    if capture is not None:
                        capture.failures += 1
            await engine.shutdown()
        if research_writer is not None:
            try:
                if not research_writer.close():
                    console.print("Research capture did not close cleanly")
            except Exception as exc:
                console.print(f"Research capture close failed: {type(exc).__name__}")
        if raw_handler is not None:
            detach_raw_log(raw_handler)
