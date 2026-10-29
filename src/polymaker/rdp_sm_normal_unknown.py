"""Ten-second cached admission for NORMAL/BA; UNKNOWN risk and close proof stay intact."""

from decimal import Decimal

from polymaker.domain import TradeState
from polymaker.favorite_live import collateral_readiness
from polymaker.rdp_sm_recovery import retry_read

WAIT_SEC = 10.0
DETAIL_MAX_AGE_SEC = 10.0  # The existing detail retry backoff reaches eight seconds.
ZERO = Decimal(0)
SOURCES = ("time", "open_orders", "trades", "positions", "capital")


class NormalUnknownRecovery:
    def __init__(self, executor):
        self.e = executor
        self.normal_ids = set()
        self.repair_ids = set()
        self.identities = {}
        self.transport_failures = set()
        self.cancel_drained_after_post = {}
        self.active_ids = set()
        self.logged = None
        self.spenders = ()

    @property
    def wait_sec(self):
        return WAIT_SEC

    def track(self, batch, account, *, repair=False):
        tracked = self.repair_ids if repair else self.normal_ids
        tracked.update(a.expected_order_id for a in batch)
        for a in batch:
            q = a.quote
            self.identities[a.expected_order_id] = (
                q.market_id,
                q.token_id,
                q.outcome,
                Decimal(str(q.price)),
                Decimal(str(q.size)),
            )
        self.spenders = tuple((account or {}).get("applicable_spenders", ()))

    def unknowns(self):
        if self.e.shared_reads is None:
            return []
        return [
            a
            for a in self.e.attempts
            if a.outcome == "UNKNOWN"
            and a.post_started
            and a.order_id in self.normal_ids | self.repair_ids
            and a.order_id == a.expected_order_id
        ]

    def post_failed(self, batch, error):
        if retry_read(error):
            self.transport_failures.update(a.expected_order_id for a in batch)

    def cancel_completed(self, result):
        cache = self.e.shared_reads
        if cache is None or result.sdk_started_monotonic is None:
            return
        # Completion is an I/O boundary, never evidence of successful cancellation.
        attempted = (
            set(result.canceled) | {oid for oid, _ in result.not_canceled} | set(result.unknown)
        )
        for oid in attempted & (self.normal_ids | self.repair_ids):
            post_return = cache.post_returns.get(oid)
            if post_return is not None and result.sdk_started_monotonic >= post_return:
                self.cancel_drained_after_post.setdefault(
                    oid, max(cache.order_writes.get(oid, 0), result.ack_monotonic or 0)
                )  # Repeated DELETEs cannot restart the original ten-second wait.

    def _absent(self, attempt, snap):
        e, cache = self.e, self.e.shared_reads
        oid = attempt.order_id
        boundary = self.cancel_drained_after_post.get(oid)
        if (
            cache is None
            or oid not in self.transport_failures
            or boundary is None
            or snap.status != "RUNNING"
            or cache.companion.failure
            or not cache.context_matches(snap)
        ):
            return None
        latest = {r.name: r for r in snap.attempts}
        detail, opened, trades = (latest.get(n) for n in ("order:" + oid, "open_orders", "trades"))
        now = e.monotonic()
        if (
            detail is None
            or detail.complete
            or detail.error_code != "SYNC_ORDER_NOT_FOUND"
            or detail.started_monotonic <= boundary
            or not 0 <= now - detail.started_monotonic <= max(DETAIL_MAX_AGE_SEC, cache.timeout_s)
            or opened is None
            or not opened.complete
            or trades is None
            or not trades.complete
            or opened.started_monotonic <= boundary
            or trades.started_monotonic <= boundary
            or not 0 <= now - opened.started_monotonic <= cache.timeout_s
            or not 0 <= now - trades.started_monotonic <= cache.timeout_s
            or any(row["order_id"] == oid for row in opened.data)
            or any(leg["order_id"] == oid for row in trades.data for leg in row["maker_legs"])
            or oid in e.observed_order_events
            or oid in e.gateway._client.observed_open_rows
            or any(key[1] == oid for key in e.gateway._client.observed_trade_legs)
        ):
            return None
        return boundary

    def suppress_cancel(self, oid):
        cache = self.e.shared_reads
        if cache is None or cache.inflight:
            return False
        attempt = next((a for a in self.unknowns() if a.order_id == oid), None)
        return attempt is not None and self._absent(attempt, cache.service.latest()) is not None

    def still_absent(self, ids):
        """Watch a previously admitted envelope while the new order is in flight."""
        cache = self.e.shared_reads
        if cache is None:
            return False
        unknowns = {a.order_id: a for a in self.unknowns()}
        snap = cache.service.latest()
        return all(oid in unknowns and self._absent(unknowns[oid], snap) is not None for oid in ids)

    def facts(self, snap=None, *, pending_batch=()):
        """Fresh account facts authorize admission, never order closure or risk release."""
        e, cache = self.e, self.e.shared_reads
        unknowns = self.unknowns()
        if (
            cache is None
            or len(unknowns) != 1
            or e.closed
            or e.fatal
            or not e.journal.ready
            or e.inventory_mismatch is not None
            or e.merge_pending
            or e.gateway._client.pagination_failure
            or cache.companion.failure
        ):
            return None
        ignored = {a.attempt_id for a in pending_batch}
        pending_ids = {a.expected_order_id for a in pending_batch}
        if any(
            kind != "POST" or set(ids) != pending_ids
            for kind, ids in tuple(cache.inflight.values())
        ):
            return None
        if pending_batch and any(a.post_started for a in pending_batch):
            return None
        attempt = unknowns[0]
        q = attempt.quote
        # The SDK worker must use frozen signed economics, never SQLite reads.
        if q.market_id != e.meta.condition_id or self.identities.get(attempt.order_id) != (
            q.market_id,
            q.token_id,
            q.outcome,
            Decimal(str(q.price)),
            Decimal(str(q.size)),
        ):
            return None
        ids = {attempt.order_id}
        for a in e.attempts:
            if a.attempt_id in ignored or a.outcome in {"REJECTED", "ABORTED"}:
                continue
            if a.outcome == "INTENT" and not a.post_started and not a.order_id:
                continue
            if a.order_id not in ids and (
                a.order_id not in e.proofs or a.order_id not in e.terminal
            ):
                return None
            if a.outcome == "UNKNOWN" and a.order_id not in ids:
                return None
        if not e.known - ids <= e.proofs.keys() & e.terminal.keys():
            return None
        snap = snap or cache.service.latest()
        boundary = self._absent(attempt, snap)
        if boundary is None or e.monotonic() - boundary < WAIT_SEC:
            return None
        good = {r.name: r for r in snap.last_good}
        latest = {r.name: r for r in snap.attempts}
        now = e.monotonic()
        for name in SOURCES:
            r = good.get(name)
            if (
                r is None
                or latest.get(name) != r
                or not r.complete
                or r.started_monotonic <= max(boundary, cache.last_write)
                or not 0 <= now - r.started_monotonic <= cache.timeout_s
            ):
                return None
        if any(
            e.processor.has_unresolved_fill_key(oid)
            or e.processor.has_failed_fill(oid)
            or oid in e.processor.quarantined_orders
            for oid in e.known
        ):
            return None
        from polymaker.rdp_sm_cached_reads import trade_raw
        from polymaker.rdp_sm_execution import _same_fill_economics

        official = e._official_fills([trade_raw(t) for t in good["trades"].data], e.known)
        if official != e._local_fills(e.known) or any(
            v[0] is not TradeState.CONFIRMED for v in official.values()
        ):
            return None
        if any(
            f.status is not TradeState.CONFIRMED
            for f in e.processor.economic_fills_for_orders(e.known)
        ):
            return None
        if any(
            key[1] in e.known
            and (
                key not in official
                or not _same_fill_economics(
                    official[key][1:], (leg.asset_id, leg.side, leg.price, leg.matched_amount)
                )
            )
            for key, (_, leg) in e.gateway._client.observed_trade_legs.items()
        ):
            return None
        positions = {row["token_id"]: Decimal(str(row["size"])) for row in good["positions"].data}
        y, n = e.economic_inventory()
        if any(
            abs(positions.get(token, ZERO) - qty) >= Decimal(".01")
            for token, qty in ((e.meta.yes.token_id, y), (e.meta.no.token_id, n))
        ):
            return None
        raw = good["capital"].data
        if len({str(k).lower() for k in raw["allowances"]}) != len(raw["allowances"]):
            return None
        balance, allowance = collateral_readiness(raw, self.spenders)
        spent = sum(
            (Decimal(str(f.price)) * Decimal(str(f.size)) for f in e.economic_fills()), ZERO
        )
        if (
            balance is None
            or allowance is None
            or e.starting_collateral is None
            or abs(e.starting_collateral - spent + e.merged_pairs - Decimal(str(balance)))
            > Decimal(".000001")
        ):
            return None
        return {
            "order_ids": sorted(ids),
            "snapshot_version": snap.version,
            "wait_sec": WAIT_SEC,
            "operation_boundary": boundary,
            "risk_retained": True,
            "reservation_released": False,
            "reserved_usdc": str(attempt.maximum),
            "outcome": attempt.quote.outcome,
            "possible_shares": str(attempt.quote.size),
            "sources": {name: good[name].read_id for name in SOURCES},
        }

    def ids(self, snap=None, *, pending_batch=()):
        facts = self.facts(snap, pending_batch=pending_batch)
        return set(facts["order_ids"]) if facts else set()

    def observe(self):
        facts = self.facts()
        key = tuple(facts["order_ids"]) if facts else None
        if key != self.logged:
            repair = bool(set(key or self.logged or ()) & self.repair_ids)
            self.e.record(
                "sm_ba_unknown_cache_recovery" if repair else "sm_normal_unknown_cache_recovery",
                {
                    "event": "READY" if facts else "REVOKED",
                    "reason": None if facts else "CACHE_OR_ORDER_EVIDENCE_CHANGED",
                    "risk_retained": True,
                    "reservation_released": False,
                    **(facts or {}),
                },
            )
            self.logged = key
        if facts:
            network = self.e.reads.network
            network.release_drained_unknown_batch(network.snapshot().active_batch)
        return bool(facts)

    def candidate_allowed(self, delta, side, quantity, *, exclude=()):
        excluded = {a.attempt_id for a in exclude}
        pending = {"YES": ZERO, "NO": ZERO}
        fills = self.e.economic_fills()
        for a in self.e.attempts:
            if (
                a.attempt_id in excluded
                or not a.post_started
                or a.outcome in {"REJECTED", "ABORTED"}
                or a.order_id in self.e.proofs
                and a.order_id in self.e.terminal
            ):
                continue
            filled = sum(
                (Decimal(str(f.size)) for f in fills if f.maker_order_id == a.order_id), ZERO
            )
            pending[a.quote.outcome] += max(ZERO, Decimal(str(a.quote.size)) - filled)
        pending[side] += quantity
        return (
            max(abs(delta - pending["NO"]), abs(delta + pending["YES"]))
            <= self.e.cfg.rdp_sm.repair.delta_limit
        )
