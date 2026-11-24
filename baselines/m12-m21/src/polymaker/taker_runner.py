"""Join independently recovered Taker markets under the existing live runner."""

import asyncio
import copy
import hashlib
import json
import time
import tomllib
from dataclasses import asdict
from decimal import Decimal
from threading import local
from uuid import uuid4

from polymaker.config import RDPObiConfig
from polymaker.m4_config import LIVE_TAKER_IDENTITY_KEY, read_strategy_config
from polymaker.m4_control import atomic_json
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_live_budget import BudgetLedger
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_live_config import LiveExecutionSettings, M4LiveSettings
from polymaker.m4_live_halt import MarketHalt
from polymaker.m4_live_startup import PreparationStopped, transient_read_error
from polymaker.m4_signals import M4Feed
from polymaker.m4_types import M4_LEDGER, M6_LEDGER, TakerStrategy
from polymaker.m6_baseline.config import M6Config
from polymaker.m6_baseline.signals import M6Feed
from polymaker.maker_account import account_snapshot
from polymaker.rdp_sm_writer import AsyncRiskWriter
from polymaker.taker_pool import ADMISSION_CUTOFF_SEC, POLICY, TakerPool, membership


def strategy_settings(project, strategy, m4):
    if strategy == M4_LEDGER:
        identity = read_strategy_config(project)[LIVE_TAKER_IDENTITY_KEY]
        if set(identity) != {"attribution", "ledger"}:
            raise ValueError("M4_LIVE_TAKER_IDENTITY_INVALID")
        source = json.dumps({"settings": m4.fingerprint, "identity": identity}, sort_keys=True)
        policy = TakerStrategy(strategy, m4.strategy, identity["attribution"], identity["ledger"],
                               hashlib.sha256(source.encode()).hexdigest(),
                               m4.order_budget_usdc, m4.session_budget_usdc,
                               m4.max_opportunities_per_direction)
        return M4LiveSettings.model_validate({**m4.model_dump(mode="json"), "taker_strategy": asdict(policy)})
    if strategy != M6_LEDGER:
        raise ValueError("TAKER_UNKNOWN_STRATEGY")
    raw = tomllib.loads((project / "config/m6.toml").read_text(), parse_float=Decimal)["live_taker"]
    if set(raw) != {"attribution", "ledger", "order_budget_usdc", "session_budget_usdc",
                    "max_opportunities_per_direction", "pricing_policy", "price_risk"}:
        raise ValueError("M6_LIVE_TAKER_CONFIG_INVALID")
    m6 = M6Config.load(project)
    source = json.dumps({"detector": m6.fingerprint, "live_taker": raw}, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(source.encode()).hexdigest()
    policy = TakerStrategy(strategy, m6.strategy, raw["attribution"], raw["ledger"], fingerprint,
                           raw["order_budget_usdc"], raw["session_budget_usdc"],
                           raw["max_opportunities_per_direction"])
    return M4LiveSettings.model_validate({
        **{k: getattr(m6, k) for k in ("account_max_age_sec", "account_poll_sec",
                                      "heartbeat_sec", "retry_initial_sec", "retry_max_sec")},
        "strategy": m6.strategy,
        "execution_revision": "m4-taker-budget-v1",
        "order_budget_usdc": policy.signal_budget_usdc,
        "session_budget_usdc": policy.market_budget_usdc,
        "execution": LiveExecutionSettings.model_validate({
            "mode": "taker", "taker": {"pricing_policy": raw["pricing_policy"]}}),
        "live_taker_price_risk": raw["price_risk"],
        "taker_strategy": asdict(policy),
    })


async def prepare_opening(project, root, prepared, startup):
    """Verify capital before creating either strategy's feed or ledger."""
    funder, market_id = prepared.identity["funder"], prepared.meta.condition_id
    epoch = int(prepared.slug.rsplit("-", 1)[1])
    pool = TakerPool(root / "taker-pool.sqlite3", project)
    control = startup.control
    deadline = epoch + ADMISSION_CUTOFF_SEC

    def clock():
        server, sent, received = prepared.clock
        lower = server + max(0, time.monotonic() - received)
        return lower, lower + 1 + received - sent

    opened = pool.existing(funder, epoch)
    if opened:
        if opened.payload["market_id"] != market_id:
            raise ValueError("TAKER_POOL_MARKET_IDENTITY_CONFLICT")
        return opened, None, None
    if (prepared.startup or {}).get("recovery"):
        # Mid-market cutover is legacy recovery. A strategy snapshot may only
        # be restored beside its original, committed pool opening.
        if prepared.settings.taker_strategy is not None:
            raise ValueError("TAKER_POOL_OPENING_MISSING")
        return None, None, None
    if clock()[0] < epoch:
        raise ValueError("TAKER_POOL_BEFORE_T0")
    enabled = membership(project)
    m4 = M4LiveSettings.load(project)
    settings = {strategy: strategy_settings(project, strategy, m4) for strategy in enabled}
    max_age = min((cfg.account_max_age_sec for cfg in settings.values()), default=m4.account_max_age_sec)
    retries = 0
    while True:
        startup.check_stop()
        account = began = valid_until = None
        error = retry = None
        phase = "account"
        remaining = deadline - clock()[1]
        if enabled and remaining > 0:
            try:
                if retries:
                    phase = "clock"
                    refreshed = await asyncio.wait_for(startup.read("pool_clock", startup.api.clock),
                                                       timeout=remaining)
                    server, sent, received = refreshed
                    if server < epoch or received < sent or server + 1 + received - sent < clock()[0]:
                        raise ValueError("TAKER_POOL_CLOCK_INVALID")
                    prepared.clock = refreshed
                    remaining = deadline - clock()[1]
                if remaining > 0:
                    phase = "account"
                    began = time.monotonic()
                    valid_until = began + max_age
                    account = await asyncio.wait_for(
                        startup.read("pool_account", account_snapshot, prepared.reads, prepared.meta,
                                     raise_on_error=True),
                        timeout=min(remaining, max_age))
                    if time.monotonic() >= valid_until:
                        raise TimeoutError("TAKER_POOL_ACCOUNT_READ_STALE")
                    control.observe_balance(account["official_balance_usd"])
                    if control.inhibited():
                        error = "TAKER_POOL_ACCOUNT_STOP"
                    account = {**account, "read_started_monotonic": began, "read_at": time.time()}
            except PreparationStopped:
                raise
            except Exception as exc:
                account = began = valid_until = None
                reason = f"TAKER_POOL_{phase.upper()}_READ_{type(exc).__name__}"
                if isinstance(exc, TimeoutError) or transient_read_error(exc):
                    retry = reason
                else:
                    error = reason
        if error != "TAKER_POOL_ACCOUNT_STOP":
            startup.check_stop()
        if retry is None or clock()[1] >= deadline:
            opened = pool.decide(funder, epoch, market_id, settings, account, clock=clock,
                                 read_error=error, account_valid_until=valid_until, read_retries=retries)
            if opened is not None:
                if retries or opened.payload["official_upper"] >= epoch + m4.start_sec:
                    startup.phase = "pool_decision"
                    startup.report("READY", **{k: v for k, v in (prepared.startup or {}).items() if k != "slug"},
                                   pool_policy=opened.payload["policy"], pool_status=opened.payload["status"],
                                   pool_open_elapsed_upper_sec=opened.payload["official_upper"] - epoch,
                                   pool_read_retries=retries)
                if opened.payload["status"] != "OPEN" or opened.payload["account"] != account:
                    return opened, None, None
                return opened, account, began
            retry = "TAKER_POOL_ACCOUNT_READ_STALE"
        retries += 1
        delay = min(5.0, .5 * 2 ** min(retries - 1, 4), max(0, deadline - clock()[1]))
        startup.report("RETRYING_TAKER_POOL_READ", pool_policy=POLICY, pool_read_retries=retries,
                       pool_read_reason=retry, retry_in_sec=delay)
        await startup.pause(delay)


class SharedTakerFeed(M4Feed):
    """One raw socket and watermark, two independently validated signal engines."""

    reader_backpressure_levels = (64, 32)

    def __init__(self, meta, *args, m6_settings, **kwargs):
        super().__init__(meta, *args, **kwargs)
        self.m6 = M6Feed(meta, RDPObiConfig(), taker_settings=m6_settings,
                         monotonic=self.monotonic, wall=self.wall)
        self.m6.lock = self.lock
        self.m6.pending_receipts = self.pending_receipts

    def pending_receipts(self):
        # The socket puts monotonic receipts in FIFO order. M6 only needs
        # their minimum, including a frame being applied outside the queue.
        oldest = self.m1_pending_received[0] if self.m1_pending_received else None
        processing = self.m1_processing_received
        if oldest is None or (processing is not None and processing < oldest):
            oldest = processing
        return () if oldest is None else (oldest,)

    def mirror(self, method, *args, **kwargs):
        if not self.m6.stop_event.is_set() and not self.m6.m1.failure:
            try:
                getattr(self.m6, method)(*args, **kwargs)
            except Exception as exc:
                self.m6.m1.failure = f"M6_SHARED_FEED_{type(exc).__name__}"
                self.m6.connected = False
                self.m6.m1.emit("LIVE_OBSERVATION_GAP", reason=self.m6.m1.failure)

    def connect(self):
        with self.lock:
            super().connect()
            self.mirror("connect")

    def invalidate(self, reason):
        with self.lock:
            super().invalidate(reason)
            self.mirror("invalidate", reason)

    def verify_ticks(self, ticks):
        with self.lock:
            super().verify_ticks(ticks)
            self.mirror("verify_ticks", ticks)

    def _apply(self, raw, **kwargs):
        super()._apply(raw, **kwargs)
        self.mirror("_apply", raw, **kwargs)


class StrategyReads:
    """Strategy-local order ownership with the lead reader's session and IO pools."""

    def __init__(self, primary, feed, ledger, settings):
        self.feed, self.network = feed, primary.network
        self.gateway = copy.copy(primary.gateway)
        client = copy.copy(primary.gateway._client)
        self.gateway._client = client
        # The SDK signer and rate/IO pools are shared. Mutable order identities,
        # per-call context, native guards and archive flags belong to this ledger.
        client.known_ids, client.m4_taker_ids = set(), {}
        client.observed_open_rows, client.observed_trade_legs, client._pagination_failures = {}, {}, {}
        client.operation_local = local()
        client.m4_read_only = False
        client.m4_execution = settings.execution
        self.gateway.m4_ledger = client.m4_ledger = ledger

    def close(self):
        # Owned by the lead reader and closed after both markets drain.
        pass


async def run_secondary(primary, opening, feed, root, make_executor, control, local_stop):
    settings = opening.settings(M6_LEDGER)
    e = primary.e
    path = root / "m6-ledgers" / f"{e.slug}.sqlite3"
    ledger = executor = journal = None
    try:
        identity = {"strategy": M6_LEDGER, "mode": "LIVE", "funder": opening.funder,
                    "market_id": e.meta.condition_id, "settings_hash": settings.fingerprint}
        ledger = BudgetLedger(path, identity, settings=settings, fees=primary.ledger.fees)
        if ledger.get("complete"):
            return
        sid = ledger.get("session_id")
        recovering = sid is not None
        if not sid:
            sid = "m4_live_" + uuid4().hex  # Journal format is shared; M6 is isolated by directory and ledger.
            ledger.initialize_session(sid, settings.model_dump(mode="json"), asdict(e.meta))
        backing = M4RiskJournal(root / "m6-journal", opening.funder, session_id=sid, resume=recovering)
        journal = AsyncRiskWriter(backing, batch_size=128)
        journal.accepted = journal.written = backing.last_writer_sequence
        reads = StrategyReads(e.reads, feed, ledger, settings)
        executor = make_executor(e.cfg, e.slug, sid, reads, journal, ledger=ledger, control=control, live=True)
        executor.stop_event, executor.meta = e.stop_event, e.meta
        executor.official_time, executor.time_anchor = e.official_time, e.time_anchor
        executor.official_time_uncertainty_s = e.official_time_uncertainty_s
        executor.record("sm_session", {
            "strategy": M6_LEDGER, "mode": "LIVE", "slug": e.slug, "market_id": e.meta.condition_id,
            "settings": settings.model_dump(mode="json"), "settings_hash": settings.fingerprint,
            "state_path": str(path), "recovery": recovering, "pool_policy": opening.payload["policy"],
        })

        def record(row):
            executor.record("sm_m6_signal", row)
            return executor.journal.ready

        feed.m1.record = record
        market = BudgetMarket(executor, settings, local_stop, opening=opening, account_owner=primary,
                              display=lambda value: atomic_json(root / "m6-latest.json", value))
        await market.run()
    except Exception as exc:
        MarketHalt(path, e.slug).latch("M6_STRATEGY_RUNTIME_FAILED")
        e.record("sm_m6_strategy_fault", {"ledger": str(path), "error_type": type(exc).__name__})
    finally:
        feed.m1.record = None
        await feed.close()
        try:
            if executor:
                executor.close()
            elif journal:
                journal.close()
        finally:
            if ledger:
                ledger.close()


class StrategyStop:
    def __init__(self, parent):
        self.parent, self.local = parent, asyncio.Event()

    def is_set(self):
        return self.parent.is_set() or self.local.is_set()

    def set(self):
        self.local.set()


async def run_markets(primary, opening, feed, root, make_executor, control):
    local_stop = StrategyStop(primary.stop)
    secondary = asyncio.create_task(
        run_secondary(primary, opening, feed.m6, root, make_executor, control, local_stop),
        name="m6-taker-market",
    )
    try:
        clean = await primary.run()
    except Exception:
        # A locally halted M4 ledger must not consume M6's market window.
        # Keep the lead reader and feed alive to serve the independent lane.
        while not secondary.done() and not primary.stop.is_set() and not control.inhibited():
            await primary.refresh_account()
            await asyncio.wait({secondary}, timeout=primary.settings.account_poll_sec)
        raise
    else:
        if clean:
            await secondary
        return clean
    finally:
        local_stop.set()
        # Signing/native POST is drained by both markets before the shared IO closes.
        await secondary
