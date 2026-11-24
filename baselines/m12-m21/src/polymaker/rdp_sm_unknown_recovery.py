"""Owner-scoped repair permission and three-round UNKNOWN budget release."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from decimal import Decimal
from threading import RLock

from polymaker.domain import TradeState
from polymaker.favorite_live import collateral_readiness
from polymaker.rdp_sm import trading_start_sec
from polymaker.rdp_sm_sl_ledger import exposure

ZERO = Decimal(0)
NAMES = ("time", "open_orders", "trades", "positions", "capital")


def canonical(rows):
    return tuple(sorted(json.dumps(r, sort_keys=True) for r in rows))


class UnknownRepairRecovery:
    def __init__(self, owner):
        self.owner, self.e = owner, owner.e
        self.cfg = self.e.cfg.rdp_sm.repair.unknown_recovery
        self.lock = RLock()
        self.count = 0
        self.sample = self.read_ids = None
        self.completed = 0.
        self.active = self.granted = False
        self.economics = None
        self.boundary = 0.
        self.claim = None
        self.e.unknown_repair_recovery = self

    def unknowns(self):
        return [a for a in self.owner.orders
                if a.outcome == "UNKNOWN" and self.owner.unresolved(a)]

    def waiting(self):
        orders = self.unknowns()
        return bool(self.cfg.enabled and self.e.shared_reads is not None and orders
                    and all(a.unknown_recovery_eligible for a in orders))

    def post_boundary(self, a):
        cache = self.e.shared_reads
        return max(cache.post_returns.get(self.owner.oid(a), 0),
                   self.owner.post_returned.get(a.attempt_id, 0))

    def suppress_cancel(self, a):
        """Drain one cancellation started after POST; avoid starving new reads.

        New order/fill facts or any positive order detail re-enable cancellation.
        A not-found reply is never interpreted as successful cancellation.
        """
        if (not self.waiting() or self.owner.closed or not trading_start_sec(self.e.cfg) <= self.owner.elapsed() < self.e.cfg.rdp_sm.repair.cutoff_sec
                or self.e.repair_abandonment is not None
                or a not in self.unknowns() or not self.owner.post_done(a)):
            return False
        aid, oid = a.attempt_id, self.owner.oid(a)
        if (self.owner.cancel_started.get(aid, -1) < self.post_boundary(a)
                or self.owner.cancel_revision.get(aid) != self.e.revision):
            return False
        task = self.owner.cancels.get(aid)
        if task is None or not task.done() or not self.owner.cancel_completed.get(aid, False):
            return False
        snap = self.e.shared_reads.service.latest()
        if not self.e.shared_reads.context_matches(snap):
            return False
        attempts = {r.name: r for r in snap.attempts}
        detail = attempts.get("order:"+oid)
        opened = attempts.get("open_orders")
        return bool(detail and detail.error_code == "SYNC_ORDER_NOT_FOUND"
                    and opened and opened.complete
                    and not any(r["order_id"] == oid for r in opened.data))

    def _facts(self, *, sampling=False):
        e, owner, cache = self.e, self.owner, self.e.shared_reads
        orders = self.unknowns()
        if (not self.waiting() or e.closed or e.fatal or not e.journal.ready
                or e.inventory_mismatch is not None or e.merge_pending
                or e.cooldown.active or e.repair_abandonment is not None
                or e.repair_episode is None or not trading_start_sec(e.cfg) <= owner.elapsed() < e.cfg.rdp_sm.repair.cutoff_sec
                or cache.companion.failure or e.gateway._client.pagination_failure):
            return None
        if any(not owner.post_done(a) or not a.order_id or a.order_id != a.expected_order_id
               or a.order_id not in e.known for a in orders):
            return None
        snap = cache.service.latest()
        if snap.status != "RUNNING" or not cache.context_matches(snap):
            return None
        good, latest = ({r.name: r for r in source} for source in (snap.last_good, snap.attempts))
        now = time.monotonic()
        if any(name not in good or latest.get(name) != good[name] or not good[name].complete
               or not 0 <= now-good[name].started_monotonic <= cache.timeout_s for name in NAMES):
            return None
        # A late/previously observed fill cannot disappear in a subsequent page.
        from polymaker.rdp_sm_cached_reads import trade_raw

        trades = e._official_fills([trade_raw(t) for t in good["trades"].data], e.known)
        if (trades != e._local_fills(e.known)
                or any(v[0] is not TradeState.CONFIRMED for v in trades.values())
                or any(e.processor.has_unresolved_fill_key(oid)
                       or e.processor.has_failed_fill(oid)
                       or oid in e.processor.quarantined_orders for oid in e.known)):
            return None
        unknown_ids = {a.order_id for a in orders}
        external = ZERO
        for row in good["open_orders"].data:
            oid = row["order_id"]
            if oid in unknown_ids:
                return None
            owned = e.processor.owned_record(oid)
            if owned is None:
                if row["token_id"] in {e.meta.yes.token_id, e.meta.no.token_id}:
                    return None
                if row["side"] == "BUY":
                    external += Decimal(str(row["price"]))*Decimal(str(row["original_size"]))
            elif (row["market_id"] != e.meta.condition_id or row["token_id"] != owned["token_id"]
                  or row["side"] != owned["side"]
                  or Decimal(str(row["price"])) != Decimal(str(owned["submitted_price"]))
                  or Decimal(str(row["original_size"])) != Decimal(str(owned["submitted_qty"]))
                  or oid in e.terminal or oid in e.sl_terminal_quantities):
                return None
        for a in orders:
            detail = latest.get("order:"+a.order_id)
            if detail is None or detail.error_code != "SYNC_ORDER_NOT_FOUND":
                return None
        positions = {r["token_id"]: Decimal(str(r["size"])) for r in good["positions"].data}
        y, n = e.economic_inventory()
        if any(abs(positions.get(token, ZERO)-qty) >= Decimal(".01")
               for token, qty in ((e.meta.yes.token_id, y), (e.meta.no.token_id, n))):
            return None
        raw = good["capital"].data
        spenders = (owner.lifecycle.account or {}).get("applicable_spenders", ())
        if len({str(k).lower() for k in raw["allowances"]}) != len(raw["allowances"]):
            return None
        balance, allowance = collateral_readiness(raw, tuple(spenders))
        state = exposure(e, retain_unknown_reservations=True)
        if (balance is None or allowance is None or e.starting_collateral is None
                or abs(e.starting_collateral-state.spent+e.merged_pairs-Decimal(str(balance)))
                > Decimal(".000001")):
            return None
        boundary = max([self.post_boundary(a) for a in orders]+[
            self.post_boundary(a) for a in owner.orders if a.post_started])
        if sampling:
            if cache.inflight or any(t and not t.done() for t in owner.posts.values()):
                return None
            for a in orders:
                if not self.suppress_cancel(a):
                    return None
            boundary = max(boundary, cache.last_write)
        else:
            boundary = self.boundary
        if any(good[n].started_monotonic <= boundary for n in NAMES):
            return None
        economics = (tuple(sorted(unknown_ids)), e.revision, e.reads.network.failure_revision,
                     cache.service.account_failure_revision,
                     canonical(good["trades"].data), canonical(good["positions"].data),
                     json.dumps(raw, sort_keys=True))
        full = (economics, canonical(good["open_orders"].data), boundary)
        return {"economics": economics, "full": full, "boundary": boundary,
                "ids": tuple(good[n].read_id for n in NAMES),
                "started": min(good[n].started_monotonic for n in NAMES),
                "completed": max(good[n].completed_monotonic for n in NAMES),
                "capital": min(e.capital_limit, state.spent+min(Decimal(str(balance)),
                    Decimal(str(allowance)))-external), "snapshot": snap.version}

    def observe(self):
        with self.lock:
            current = self._facts()
            if current is None or (self.active and current["economics"] != self.economics):
                if self.active:
                    self.e.record("sm_unknown_repair_recovery", {"event": "REVOKED",
                        "risk_retained": True, "reason": "ACCOUNT_OR_EXECUTION_EVIDENCE_CHANGED"})
                self.active = self.granted = False
                self.claim = self.sample = self.read_ids = None
                self.count, self.completed = 0, 0.
            facts = self._facts(sampling=True)
            if facts is None:
                self.count, self.sample, self.read_ids = 0, None, None
                self.completed = 0.
                self.granted = False
                return
            if facts["full"] != self.sample:
                self.count, self.completed, self.read_ids = 0, 0., None
                self.sample = facts["full"]
            if (self.read_ids is not None and (any(a == b for a, b in zip(
                    facts["ids"], self.read_ids, strict=True)) or facts["started"] <= self.completed)):
                return
            self.count = min(self.count+1, self.cfg.confirmation_rounds)
            self.read_ids, self.completed = facts["ids"], facts["completed"]
            if self.count >= self.cfg.confirmation_rounds:
                newly_ready = not self.granted
                self.active = self.granted = True
                self.economics, self.boundary = facts["economics"], facts["boundary"]
                self.owner.capital = min(self.owner.capital, facts["capital"])
                if newly_ready:
                    # UNKNOWN's local same-target latch must not permanently
                    # veto the newly authorized, separately reserved repair.
                    targets = {(a.quote.outcome, Decimal(str(a.quote.price)),
                                Decimal(str(a.quote.size))) for a in self.unknowns()}
                    if self.owner.rejected_target in targets:
                        self.owner.rejected_target = None
                        setattr(self.owner.controller, self.owner.mode.lower()+"_rejected_target", None)
                    self.e.record("sm_unknown_repair_recovery", {"event": "READY",
                        "rounds": self.count, "snapshot_version": facts["snapshot"],
                        "unknown_order_ids": list(facts["economics"][0]),
                        "post_and_cancel_boundary": self.boundary, "risk_retained": False,
                        "reservation_released": True,
                        "normal_allowed": False, **self.owner.evidence()})

    def released_order_ids(self):
        """Reuse the recovery proof; changed/late facts immediately restore risk.

        This releases reservations only, never order identity or closure proof.
        Consuming a submit grant does not consume the stable account evidence.
        """
        with self.lock:
            if not self.active or self.owner.closed:
                return set()
            facts = self._facts()
            if facts and facts["economics"] == self.economics:
                return {a.order_id for a in self.unknowns()}
            return set()

    def health(self):
        native = self.e.reads.network.continuation(self.owner.group)
        if not self.unknowns():
            return native
        with self.lock:
            facts = self._facts()
            if self.active and facts and facts["economics"] == self.economics:
                return self.e.reads.network.repair_probe(self.owner.group)
        return replace(native, allowed=False, close_active=True, reason="UNKNOWN_REPAIR_RECONCILING")

    def allows(self, reserved=None):
        if not self.unknowns():
            return True
        with self.lock:
            facts = self._facts()
            claimed = reserved is not None and self.claim == reserved.attempt_id
            return bool(self.active and facts and facts["economics"] == self.economics
                        and (claimed or self.granted and facts["full"] == self.sample))

    def reserve(self, attempt):
        with self.lock:
            if self.unknowns():
                self.claim, self.granted = attempt.attempt_id, False
