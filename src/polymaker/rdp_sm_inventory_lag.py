"""A short, single-order REPAIR lease backed by CONFIRMED cash-flow evidence.

The real positions discrepancy remains visible. This module cannot change fills,
release reservations, complete a cycle, merge, or authorize NORMAL. An exclusive
writer is an explicit deployment prerequisite: CLOB pages cannot prove absence
of out-of-band token transfers. Default configuration keeps the lease disabled.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from threading import RLock

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm import MIN_REPAIR_EXPOSURE

ZERO = Decimal(0)
EPS = Decimal(".000001")
NAMES = ("time", "open_orders", "trades", "positions", "capital")


def fill_signature(fills):
    return tuple(sorted((f.trade_id, f.maker_order_id, f.token_id, f.our_side.value,
                         str(Decimal(str(f.size))), str(Decimal(str(f.price)))) for f in fills))


class InventoryLagAdmission:
    def __init__(self, executor):
        self.e = executor
        self.lock = RLock()
        self.anchor = None
        self.since = None
        self.grant = None
        self.claim = None
        self.revoked_reason = None
        self.last_event = None

    def _event(self, event, reason=None, **details):
        key = (event, reason)
        if key != self.last_event:
            self.last_event = key
            self.e.record("sm_inventory_lag", {"event": event, "reason": reason,
                "normal_allowed": False, "risk_released": False, **details})

    def matched(self, account):
        """Only the original full snapshot can retire a mismatch and renew an anchor."""
        with self.lock:
            if self.grant is not None:
                self._event("RECONCILED")
            self.grant = self.claim = self.since = None
            self.revoked_reason = None
            e = self.e
            if not e.cfg.rdp_sm.repair.inventory_lag.enabled:
                self.anchor = None
                return
            fills = e.economic_fills()
            y, n = e.economic_inventory()
            actual = tuple(Decimal(str(account[k+"_inventory"])) for k in ("yes", "no"))
            if (e.shared_reads is not None and (not e.attempts or e.safe_to_continue)
                    and all(f.status is TradeState.CONFIRMED for f in fills)
                    and all(abs(a-b) <= EPS for a, b in zip(actual, (y, n), strict=True))):
                self.anchor = {"fills": fill_signature(fills), "inventory": (y, n),
                               "merged": e.merged_pairs}
            else:
                self.anchor = None

    def _reads(self, boundary):
        cache = self.e.shared_reads
        if cache is None or cache.companion.failure:
            return None
        snap = cache.service.latest()
        if snap.status != "RUNNING" or not cache.context_matches(snap):
            return None
        good = {r.name: r for r in snap.last_good}
        latest = {r.name: r for r in snap.attempts}
        now = time.monotonic()
        if any(name not in good or latest.get(name) != good[name] or not good[name].complete
               or good[name].started_monotonic < boundary
               or not 0 <= now-good[name].started_monotonic <= cache.timeout_s for name in NAMES):
            return None
        return snap, good

    def _common_reason(self):
        e, cfg = self.e, self.e.cfg.rdp_sm.repair.inventory_lag
        elapsed = e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit("-", 1)[1])
        if not cfg.enabled or not cfg.exclusive_market_writer:
            return "INVENTORY_LAG_DISABLED"
        if (e.cfg.rdp_sm.normal_entry_mode not in {"obi_v1", "ba_v1"}
                or e.cfg.rdp_sm.repair.policy != "hd_tp_sl_v1"):
            return "INVENTORY_LAG_POLICY_UNSUPPORTED"
        if (e.closed or e.fatal or not e.journal.ready or e.gateway._client.pagination_failure
                or e.merge_pending or e.cooldown.active or e.repair_abandonment is not None
                or e.repair_episode is None or not 15 <= elapsed < 240):
            return "INVENTORY_LAG_SAFETY_BLOCKED"
        if (self.since is None or e.monotonic() >= self.since+cfg.grace_sec):
            return "INVENTORY_LAG_EXPIRED"
        return None

    def consider(self, account):
        """Called only after all original full-account checks except positions."""
        with self.lock:
            e, cfg = self.e, self.e.cfg.rdp_sm.repair.inventory_lag
            if not cfg.enabled:
                return False
            if self.since is None:
                self.since = e.monotonic()
            if self.revoked_reason is not None:
                self._event("REVOKED", self.revoked_reason)
                return False
            reason = self._common_reason() or self._proof_reason(account)
            if reason:
                if self.grant is not None:
                    self.revoked_reason = reason
                self._event("WAIT", reason)
                return False
            # A consumed lease never permits a second order while positions lag.
            if self.claim is not None:
                return False
            if self.grant is not None and self.grant["revision"] != e.revision:
                self._event("REVOKED", "INVENTORY_LAG_REVISION_CHANGED")
                return False
            cache = e.shared_reads
            boundary = max(cache.last_write, e.last_economic_change_monotonic)
            evidence = self._reads(boundary)
            if evidence is None:
                return False
            snap, good = evidence
            self.grant = {"revision": e.revision, "fills": fill_signature(e.confirmed()),
                "episode": e.repair_episode, "boundary": boundary,
                "data": {n: good[n].data_json for n in NAMES if n != "time"},
                "old_attempts": frozenset(a.attempt_id for a in e.attempts),
                "budget": Decimal(str(account["basic_spendable_budget_usd"]))}
            cache.refresh_repair(account=True)
            self._event("READY", snapshot_version=snap.version,
                deadline_monotonic=self.since+cfg.grace_sec,
                economic_revision=e.economic_revision)
            return True

    def _proof_reason(self, account):
        e, cfg = self.e, self.e.cfg.rdp_sm.repair.inventory_lag
        anchor = self.anchor
        if anchor is None or anchor["merged"] != e.merged_pairs or not e.safe_to_continue:
            return "INVENTORY_LAG_ANCHOR_OR_CLOSE_MISSING"
        fills = e.economic_fills()
        if (not fills or any(f.status is not TradeState.CONFIRMED or f.our_side is not Side.BUY for f in fills)
                or any(e.proofs[oid].get("outcome") != "COMPLETE" for oid in e.known)
                or any(e.processor.has_unresolved_fill_key(oid) or e.processor.has_failed_fill(oid)
                       or oid in e.processor.quarantined_orders for oid in e.known)):
            return "INVENTORY_LAG_UNCONFIRMED_OR_UNRESOLVED"
        current = fill_signature(fills)
        if not set(anchor["fills"]) < set(current):
            return "INVENTORY_LAG_NO_CONFIRMED_INCREMENT"
        actual = tuple(Decimal(str(account[k+"_inventory"])) for k in ("yes", "no"))
        expected = e.economic_inventory()
        missing = tuple(b-a for a, b in zip(actual, expected, strict=True))
        # v1 deliberately rejects partially indexed increments and extra inventory.
        if (actual != anchor["inventory"] or any(d < 0 for d in missing)
                or not ZERO < sum(missing) <= cfg.max_unreported_shares
                or abs(expected[0]-expected[1]) <= MIN_REPAIR_EXPOSURE):
            return "INVENTORY_LAG_DIFFERENCE_UNEXPLAINED"
        cache = e.shared_reads
        evidence = self._reads(max(cache.last_write, e.last_economic_change_monotonic))
        if evidence is None or cache.active is None or cache.inflight:
            return "INVENTORY_LAG_FRESH_COLLECTIONS_REQUIRED"
        _, good = evidence
        pinned = {r.name: r for r in cache.active.last_good}
        if any(name not in pinned or pinned[name].data_json != good[name].data_json
               for name in NAMES if name != "time"):
            return "INVENTORY_LAG_FRESH_COLLECTIONS_REQUIRED"
        # Reject all account-owned activity outside this session, including SELL.
        for trade in good["trades"].data:
            ours = [leg for leg in trade["maker_legs"]
                    if str(leg.get("maker_address", "")).lower() == e.gateway.funder.lower()]
            if (trade["trader_side"] != "MAKER" or not ours
                    or any(leg["order_id"] not in e.known or leg["side"] != "BUY" for leg in ours)):
                return "INVENTORY_LAG_EXTERNAL_TRADE"
        spent = sum((Decimal(str(f.size))*Decimal(str(f.price)) for f in fills), ZERO)
        official = Decimal(str(account["official_balance_usd"]))
        if (e.starting_collateral is None
                or abs(e.starting_collateral-spent+e.merged_pairs-official) > EPS
                or Decimal(str(account["basic_spendable_budget_usd"])) <= 0):
            return "INVENTORY_LAG_CASHFLOW_UNPROVEN"
        return None

    def reserve(self, attempt):
        with self.lock:
            if self.e.inventory_mismatch is not None and self.grant is not None:
                if self.claim is not None:
                    raise ValueError("INVENTORY_LAG_LEASE_CONSUMED")
                self.claim = attempt

    def _current_reason(self):
        e, grant = self.e, self.grant
        reason = self._common_reason()
        if reason:
            return reason
        if (grant is None or grant["revision"] != e.revision
                or grant["episode"] is not e.repair_episode
                or grant["fills"] != fill_signature(e.confirmed())):
            return "INVENTORY_LAG_EVIDENCE_CHANGED"
        evidence = self._reads(grant["boundary"])
        if evidence is None:
            return "INVENTORY_LAG_FRESH_COLLECTIONS_REQUIRED"
        _, good = evidence
        for name in ("positions", "capital", "trades"):
            if good[name].data_json != grant["data"][name]:
                return "INVENTORY_LAG_EVIDENCE_CHANGED"
        # Our single claimed order may now appear LIVE. Every other order must
        # still be exactly the complete set admitted at the full snapshot.
        claimed = (self.claim.order_id or self.claim.expected_order_id) if self.claim else None
        cache = e.shared_reads
        if any(kind != "POST" or not claimed or set(ids) != {claimed}
               for kind, ids in cache.inflight.values()):
            return "INVENTORY_LAG_MUTATION_INFLIGHT"
        if (cache.last_write > grant["boundary"]
                and (not claimed or cache.order_writes.get(claimed) != cache.last_write)):
            return "INVENTORY_LAG_WRITE_BOUNDARY_CHANGED"
        for row in good["open_orders"].data:
            if row["order_id"] == claimed:
                q = self.claim.quote
                if (not self.claim.post_started or row["market_id"] != e.meta.condition_id
                        or row["token_id"] != q.token_id or row["side"] != "BUY"
                        or Decimal(str(row["price"])) != Decimal(str(q.price))
                        or Decimal(str(row["original_size"])) != Decimal(str(q.size))):
                    return "INVENTORY_LAG_ORDER_EVIDENCE_CHANGED"
        opened = [r for r in good["open_orders"].data if r["order_id"] != claimed]
        if opened != json.loads(grant["data"]["open_orders"]):
            return "INVENTORY_LAG_ORDER_EVIDENCE_CHANGED"
        if any(a.outcome == "UNKNOWN" for a in e.attempts
               if self.claim is not a or a.post_started):
            return "INVENTORY_LAG_UNKNOWN_ORDER"
        if any(a.attempt_id not in grant["old_attempts"] and a is not self.claim for a in e.attempts):
            return "INVENTORY_LAG_OTHER_ATTEMPT"
        return None

    def maintenance_reason(self):
        """No logging or I/O: also safe in the final SDK worker guard."""
        with self.lock:
            if self.revoked_reason is None:
                reason = self._current_reason()
                if reason and self.grant is not None:
                    self.revoked_reason = reason
                return reason
            return self.revoked_reason

    def admission_reason(self, owner, goal, reserved=None):
        with self.lock:
            reason = self.maintenance_reason()
            if reason:
                return reason
            if reserved is not self.claim or reserved is None and self.claim is not None:
                return "INVENTORY_LAG_LEASE_CONSUMED"
            if (reserved is not None and reserved.post_started
                    or owner.mode not in {"TP", "SL"}):
                return "INVENTORY_LAG_LEASE_CONSUMED"
            side, price, qty, _ = goal
            y, n = self.e.economic_inventory()
            if (side != ("NO" if y > n else "YES")
                    or qty > self.e.cfg.rdp_sm.repair.inventory_lag.max_unreported_shares
                    or price*qty > self.grant["budget"]):
                return "INVENTORY_LAG_TARGET_OR_BUDGET_CHANGED"
            if reserved is not None and not self.e.shared_reads.admission_current():
                self.revoked_reason = "INVENTORY_LAG_FINAL_EVIDENCE_CHANGED"
                return self.revoked_reason
            # The native actor additionally checks the complete worst-case
            # exposure envelope, including minimum-notional reverse inventory.
            return None
