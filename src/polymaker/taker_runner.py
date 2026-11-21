"""Join independently recovered Taker markets under the existing live runner."""

import asyncio
import copy
import fcntl
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
from polymaker.m4_live_config import M421_PRINCIPAL_FAK, LiveExecutionSettings, M4LiveSettings
from polymaker.m4_live_halt import MarketHalt
from polymaker.m4_live_startup import PreparationStopped, transient_read_error
from polymaker.m4_signals import M4Feed
from polymaker.m4_types import M4_LEDGER, M6_LEDGER, M21_LEDGER, M21_VERSION, M421_LEDGER, M421_VERSION, TAKER_LEDGER_IDS, TakerStrategy
from polymaker.m6_baseline.config import M6Config
from polymaker.m6_baseline.signals import M6Feed
from polymaker.maker_account import account_snapshot
from polymaker.rdp_sm_writer import AsyncRiskWriter
from polymaker.taker_pool import ADMISSION_CUTOFF_SEC, POLICY, TakerPool, configured_profiles


def strategy_settings(project, strategy, m4, *, signal_profile=None):
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
    if strategy in {M21_LEDGER, M421_LEDGER}:
        from polymaker.m21_live import detector_manifest

        raw = tomllib.loads((project / "config/m4-live/m21.toml").read_text(), parse_float=Decimal)
        if set(raw) != {"version", "attribution", "ledger", "order_budget_usdc", "session_budget_usdc",
                        "max_opportunities_per_direction", "pricing_policy", "price_risk"}:
            raise ValueError("M21_LIVE_TAKER_CONFIG_INVALID")
        if (raw["version"], raw["attribution"]) != (M21_VERSION, "M21"):
            raise ValueError("M21_LIVE_TAKER_IDENTITY_CONFLICT")
        source = json.dumps({"detector": detector_manifest(), "live_taker": raw}, sort_keys=True, default=str)
        policy = TakerStrategy(M21_LEDGER, raw["version"], raw["attribution"], raw["ledger"],
                               hashlib.sha256(source.encode()).hexdigest(),
                               raw["order_budget_usdc"], raw["session_budget_usdc"],
                               raw["max_opportunities_per_direction"])
        if strategy == M421_LEDGER:
            from polymaker.m4_frequency_signal import PARAMETERS
            detectors = {"m21": policy.config_fingerprint, "m4": dict(PARAMETERS), "policy": M421_VERSION}
            if signal_profile == "M42112":
                from polymaker.m12_live import detector_manifest as m12_manifest

                detectors["m12"] = m12_manifest()
            combined = json.dumps(detectors, sort_keys=True, default=str)
            policy = TakerStrategy(strategy, M421_VERSION, "M421", strategy,
                                   hashlib.sha256(combined.encode()).hexdigest(),
                                   policy.signal_budget_usdc, policy.market_budget_usdc, policy.direction_limit)
        # Use the same operational protections as the lead Taker. The detector
        # and strategy-specific limits come only from the M21 profile.
        return M4LiveSettings.model_validate({
            **{k: getattr(m4, k) for k in ("account_max_age_sec", "account_poll_sec", "heartbeat_sec",
                                          "retry_initial_sec", "retry_max_sec", "start_sec", "cutoff_sec")},
            "strategy": policy.version,
            "execution_revision": "m4-taker-budget-v1",
            "order_budget_usdc": policy.signal_budget_usdc,
            "session_budget_usdc": policy.market_budget_usdc,
            "max_opportunities_per_direction": policy.direction_limit,
            "execution": {"mode": "taker", "taker": {"pricing_policy": raw["pricing_policy"]}},
            "live_taker_price_risk": raw["price_risk"],
            "taker_strategy": asdict(policy),
            **({"taker_buy_amount_policy": M421_PRINCIPAL_FAK} if strategy == M421_LEDGER else {}),
            **({"signal_profile": signal_profile} if signal_profile is not None else {}),
        })
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


async def prepare_opening(project, root, prepared, startup, *, isolated=False):
    if not isolated:
        return await _prepare_opening(project, root, prepared, startup)
    # One process performs the official read/retry sequence. The other reuses
    # the durable decision, or takes over if the first dies before committing.
    with (root / "pool-opening.lock").open("a+") as lock:
        while True:
            startup.check_stop()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                await startup.pause(.1)
            else:
                return await _prepare_opening(project, root, prepared, startup)


async def _prepare_opening(project, root, prepared, startup):
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
    profiles = configured_profiles(project)
    m4 = M4LiveSettings.load(project)
    settings = {strategy: strategy_settings(project, strategy, m4,
                signal_profile="M42112" if profile == "M42112" else None)
                for strategy, profile in profiles.items()}
    max_age = min((cfg.account_max_age_sec for cfg in settings.values()), default=m4.account_max_age_sec)
    retries = 0
    while True:
        startup.check_stop()
        account = began = valid_until = None
        error = retry = None
        phase = "account"
        remaining = deadline - clock()[1]
        if profiles and remaining > 0:
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


class TakerSocket:
    """Backpressure and receipt watermark for a live-owned socket."""

    reader_backpressure_levels = (64, 32)

    def pending_receipts(self):
        # The socket puts monotonic receipts in FIFO order. M6 only needs
        # their minimum, including a frame being applied outside the queue.
        oldest = self.m1_pending_received[0] if self.m1_pending_received else None
        processing = self.m1_processing_received
        if oldest is None or (processing is not None and processing < oldest):
            oldest = processing
        return () if oldest is None else (oldest,)

def dedicated_feed_type(strategy):
    if strategy == M21_LEDGER:
        from polymaker.m21_live import M21Feed

        class DedicatedM21Feed(TakerSocket, M21Feed):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.observe_until_market_close = True
                self.research.record_input_rejections = True

            def _apply(self, raw, *, received_mono=None, received_wall=None):
                if raw in ("PING", "PONG"):
                    return super()._apply(raw, received_mono=received_mono, received_wall=received_wall)
                try:
                    frame = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    if (not isinstance(frame, (dict, list)) or not frame
                            or isinstance(frame, list) and any(not isinstance(row, dict) for row in frame)):
                        raise ValueError("WS_FRAME_INVALID")
                    now = self.monotonic()
                    received = now if received_mono is None else received_mono
                    wall = self.wall() if received_wall is None else received_wall
                    self.validate_receipt_clock(now, received, wall)
                except ValueError as exc:
                    self.invalidate(str(exc))
                    raise
                return super()._apply(frame, received_mono=received, received_wall=wall)

        return DedicatedM21Feed

    class DedicatedM4Feed(TakerSocket, M4Feed):
        pass

    return DedicatedM4Feed


class SharedTakerFeed(TakerSocket, M4Feed):
    """One raw socket and watermark, independently validated signal engines."""

    frame_processing_policy = "SHARED_DECODE_ONCE_REUSE_BOOK_AND_CLOCKS_V1"

    def __init__(self, meta, *args, m6_settings=None, m21_settings=None, m421_settings=None, m21_only=False, **kwargs):
        super().__init__(meta, *args, **kwargs)
        if m21_only and (m21_settings is None or m6_settings is not None):
            raise ValueError("M21_ONLY_STRATEGY_SETTINGS_CONFLICT")
        self.transport_only = self.observe_until_market_close = m21_only
        self.secondary = {}
        self.execution_disabled = m421_settings is not None
        self.m6 = None
        if m6_settings is not None:
            self.m6 = self.secondary[M6_LEDGER] = M6Feed(meta, RDPObiConfig(), taker_settings=m6_settings,
                                                       monotonic=self.monotonic, wall=self.wall)
        if m21_settings is not None:
            from polymaker.m21_live import M21Feed

            self.secondary[M21_LEDGER] = M21Feed(meta, RDPObiConfig(), taker_settings=m21_settings,
                                               monotonic=self.monotonic, wall=self.wall)
            self.secondary[M21_LEDGER].research.record_input_rejections = m21_only
        if m421_settings is not None:
            from polymaker.m21_live import M21Feed
            from polymaker.m421_live import CombinedSignals

            feed_type = M21Feed
            if m421_settings.signal_profile == "M42112":
                from polymaker.m12_live import M42112Feed

                feed_type = M42112Feed
            child = self.secondary[M421_LEDGER] = feed_type(
                meta, RDPObiConfig(), taker_settings=m421_settings, monotonic=self.monotonic, wall=self.wall)
            from polymaker.m4_live_numeric import install_live_clock_boundaries
            install_live_clock_boundaries(self)
            child.m1 = CombinedSignals(self.m1, child.m1, m421_settings, m12=getattr(child, "m12", None))
        for feed in self.secondary.values():
            feed.lock = self.lock
            feed.pending_receipts = self.pending_receipts
            feed.observe_until_market_close = m21_only or m421_settings is not None
            feed.shared_frame_processing_policy = ("M21_ONLY_SHARED_TRANSPORT_OBSERVE_T300_V1"
                                                   if m21_only else self.frame_processing_policy)

    def mirror(self, method, *args, **kwargs):
        for feed in self.secondary.values():
            if feed.stop_event.is_set() or feed.m1.failure:
                continue
            try:
                getattr(feed, method)(*args, **kwargs)
            except Exception as exc:
                name = feed.m1.diagnostics()["strategy"]
                feed.m1.failure = f"{name}_SHARED_FEED_{type(exc).__name__}"
                feed.connected = False
                feed.m1.emit("LIVE_OBSERVATION_GAP", reason=feed.m1.failure)

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

    def maintain_signals(self):
        if self.transport_only:
            self.secondary[M21_LEDGER].m1.tick()
        elif m421 := self.secondary.get(M421_LEDGER):
            if m421.m1.m12 is None:
                super().maintain_signals()
                self.m1.evaluate(None)
            m421.m1.tick()
        else:
            super().maintain_signals()

    def _apply(self, raw, **kwargs):
        frame = raw
        if isinstance(raw, (str, bytes)) and raw not in ("PING", "PONG"):
            try:
                decoded = json.loads(raw)
            except ValueError:
                pass  # Preserve the primary consumer's normal rebuild path.
            else:
                if isinstance(decoded, (dict, list)):
                    frame = decoded
        if self.transport_only and frame not in ("PING", "PONG"):
            if (not isinstance(frame, (dict, list)) or not frame
                    or isinstance(frame, list) and any(not isinstance(row, dict) for row in frame)):
                self.invalidate("WS_FRAME_INVALID")
                raise ValueError("WS_FRAME_INVALID")
            now, wall = self.monotonic(), self.wall()
            received = kwargs.get("received_mono")
            receive_wall = kwargs.get("received_wall")
            received = now if received is None else received
            receive_wall = wall if receive_wall is None else receive_wall
            try:
                self.validate_receipt_clock(now, received, receive_wall)
            except ValueError as exc:
                self.invalidate(str(exc))
                raise
            self.last_receipt, self.last_wall = received, receive_wall
        elif not self.transport_only:
            super()._apply(raw if self.m1_writer is not None else frame, **kwargs)
        if M421_LEDGER in self.secondary:
            self.m1.evaluate(None)
        self.mirror("_apply", frame, **kwargs)


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


async def run_secondary(primary, opening, feed, root, make_executor, control, local_stop, *, strategy=M6_LEDGER):
    settings = opening.settings(strategy)
    name = next(name for name, ledger in TAKER_LEDGER_IDS.items() if ledger == strategy)
    lane = name.lower()
    e = primary.e
    path = root / f"{lane}-ledgers" / f"{e.slug}.sqlite3"
    ledger = executor = journal = None
    try:
        identity = {"strategy": strategy, "mode": "LIVE", "funder": opening.funder,
                    "market_id": e.meta.condition_id, "settings_hash": settings.fingerprint}
        ledger = BudgetLedger(path, identity, settings=settings, fees=primary.ledger.fees)
        if ledger.get("complete"):
            return
        sid = ledger.get("session_id")
        recovering = sid is not None
        if not sid:
            sid = "m4_live_" + uuid4().hex
            ledger.initialize_session(sid, settings.model_dump(mode="json"), asdict(e.meta))
        backing = M4RiskJournal(root / f"{lane}-journal", opening.funder, session_id=sid, resume=recovering)
        journal = AsyncRiskWriter(backing, batch_size=128)
        journal.accepted = journal.written = backing.last_writer_sequence
        reads = StrategyReads(e.reads, feed, ledger, settings)
        executor = make_executor(e.cfg, e.slug, sid, reads, journal, ledger=ledger, control=control, live=True)
        executor.stop_event, executor.meta = e.stop_event, e.meta
        executor.official_time, executor.time_anchor = e.official_time, e.time_anchor
        executor.official_time_uncertainty_s = e.official_time_uncertainty_s
        executor.record("sm_session", {
            "strategy": strategy, "mode": "LIVE", "slug": e.slug, "market_id": e.meta.condition_id,
            "settings": settings.model_dump(mode="json"), "settings_hash": settings.fingerprint,
            "state_path": str(path), "recovery": recovering, "pool_policy": opening.payload["policy"],
            "signal_input_policy": feed.research.source_order_policy,
            "shared_frame_processing_policy": getattr(feed, "shared_frame_processing_policy", None),
            **({"signal_profile": settings.signal_profile, "m12_detector": feed.m12_manifest}
               if settings.signal_profile is not None else {}),
        })

        market = BudgetMarket(executor, settings, local_stop, opening=opening, account_owner=primary,
                              public_api=primary.public_api if getattr(feed, "observe_until_market_close", False) else None,
                              display=lambda value: atomic_json(root / f"{lane}-latest.json", value))

        def record(row):
            executor.record(f"sm_{lane}_signal", row)
            if (getattr(feed, "observe_until_market_close", False) and row["event"] == "OPPORTUNITY_IDENTIFIED"
                    and market.elapsed() >= market.settings.cutoff_sec):
                executor.record("sm_m4_candidate_skipped",
                                {"opportunity": row["opportunity"], "reason": f"{name}_FORMAL_AFTER_T240"})
            return executor.journal.ready

        feed.m1.record = record
        await market.run()
    except Exception as exc:
        MarketHalt(path, e.slug).latch(f"{name}_STRATEGY_RUNTIME_FAILED")
        e.record(f"sm_{lane}_strategy_fault", {"ledger": str(path), "error_type": type(exc).__name__})
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
    secondary = asyncio.gather(*(
        run_secondary(primary, opening, child, root, make_executor, control, local_stop, strategy=strategy)
        for strategy, child in feed.secondary.items()
    ))
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
