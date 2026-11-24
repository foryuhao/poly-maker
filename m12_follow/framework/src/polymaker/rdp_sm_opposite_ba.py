"""Opposite BA exits with shared repair quantities and residual tolerance."""
from __future__ import annotations

import math
from decimal import Decimal

from polymaker.domain import Side
from polymaker.rdp_sm import repair_required
from polymaker.rdp_sm_ba import entry_admission
from polymaker.rdp_sm_execution import WindowQuote
from polymaker.rdp_sm_execution import ba_repair_buy_quantity as repair_buy_quantity
from polymaker.rdp_sm_normal import BANormalController
from polymaker.rdp_sm_signal import Signal


class OppositeBAController(BANormalController):
    phase = "REPAIR"
    ba_signal = True

    def __init__(self, executor, lifecycle):
        super().__init__(executor, lifecycle)
        self.tp_handled_ba = {}

    @property
    def cfg(self):
        return self.executor.cfg.rdp_sm.repair

    @property
    def ba_enabled(self):
        return self.cfg.ba_enabled

    @property
    def restore_tp_enabled(self):
        cfg = self.executor.cfg.rdp_sm
        return (cfg.normal_entry_mode == "ca_v1" and cfg.ca_matched_tp.enabled
                and cfg.ca_matched_tp.restore_after_ba)

    def elapsed(self):
        e = self.executor
        return e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit("-", 1)[1])

    def tp_ba_signal(self, snapshot):
        """Only a fresh, new repair BA may displace a restored TP."""
        if not self.ba_enabled:
            return None
        e = self.executor
        with self.lifecycle.feed.lock:
            now = e.monotonic()
            self.tp_handled_ba = {key: end for key, end in self.tp_handled_ba.items() if end > now}
            direction, _ = self.select(snapshot)
            ep = self.episodes.selected
            if not direction or ep is None or ep.episode_id in self.tp_handled_ba:
                return None
            cutoff = e.time_anchor+int(e.slug.rsplit("-", 1)[1])+self.cutoff-e.official_time
            timing = entry_admission(self.ba_config, t0=ep.signal_start_mono,
                deadline=min(ep.order_deadline, cutoff), writes=e.reads.network.recent_writes())
            return ep if timing.reason is None else None

    @property
    def cutoff(self):
        return self.executor.cfg.rdp_sm.repair.cutoff_sec

    @property
    def episodes(self):
        return self.lifecycle.feed.opposite_ba

    @property
    def ba_config(self):
        return self.executor.cfg.rdp_sm.repair.opposite_ba

    def _opening_time(self):
        ep = self.executor.repair_episode
        if ep is None:
            return math.inf
        if "first_fill_monotonic" in ep:
            return ep["first_fill_monotonic"]
        if "first_fill_elapsed" in ep:
            return ep["first_fill_elapsed"]-ep["path_clock_offset"]
        return math.inf  # Never invent an opening time to admit a stale BA.

    def select(self, snapshot):
        e = self.executor
        with self.lifecycle.feed.lock:
            if not self.ba_enabled:
                self.signal_block_reason = "BA_REPAIR_DISABLED"
                return None, Signal("NONE", snapshot.snapshot_id)
            y, n = e.economic_inventory()
            opposite = "NO" if y > n else "YES" if n > y else None
            held = "YES" if y > n else "NO" if n > y else None
            signal = (self.episodes.evaluate(snapshot, outcome=opposite) if opposite else
                      Signal("NONE", snapshot.snapshot_id))
            ep = self.episodes.selected
            reason = (
                "OPPOSITE_BA_NO_POSITION" if e.repair_episode is None or not repair_required(y, n) else
                "OPPOSITE_BA_DIRECTION_HANDOFF_UNPROVEN" if e.repair_episode.get("side") != held else
                "OPPOSITE_BA_NO_SIGNAL" if signal.direction != opposite else
                "OPPOSITE_BA_PREDATES_POSITION" if ep.signal_start_mono < self._opening_time() else
                None
            )
            self.signal_block_reason = reason
            if reason:
                return None, Signal("NONE", snapshot.snapshot_id)
            return opposite, signal

    def candidates(self, snapshot, yes, no, window_id):
        with self.lifecycle.feed.lock:
            return self._candidates(snapshot, yes, no, window_id)

    def _candidates(self, snapshot, yes, no, window_id, *, own_reservation=Decimal(0)):
        self.quote_block_reason = None
        direction, _ = self.select(snapshot)
        if direction is None:
            self.quote_block_reason = self.signal_block_reason
            return {}
        ep = self.episodes.selected
        book = snapshot.no if direction == "NO" else snapshot.yes
        tolerant = getattr(self.episodes, "fixed_ask_quote", False)
        price = book.best_ask-book.tick if tolerant else ep.anchor_bid_price
        delta = yes-no
        current_y, current_n = self.executor.economic_inventory()
        if delta != current_y-current_n or (delta > 0) != (direction == "NO"):
            self.quote_block_reason = "OPPOSITE_BA_INVENTORY_CHANGED"
            return {}
        if (not tolerant and price != book.best_bid) or not 0 < price < book.best_ask:
            self.quote_block_reason = "BA_ANCHOR_INVALID"
            return {}
        e = self.executor
        # Final POST validation already reserves this exact batch in attempts.
        # Keep the global ledger intact; only credit that owner's unsent intent
        # while recalculating its original quote and quantity.
        cash = max(Decimal(0), e.capital_limit-e.committed()+own_reservation)
        if self.lifecycle.account is not None:
            cash = min(cash, Decimal(str(self.lifecycle.account["basic_spendable_budget_usd"])))
        try:
            qty = repair_buy_quantity(e, direction, price, delta, cash)
        except ValueError as exc:
            self.quote_block_reason = str(exc)
            return {}
        quote = WindowQuote(self.executor.meta.condition_id, book.token_id, direction,
                            Side.BUY, float(price), float(qty), window_id)
        return {direction: ((quote,), {"policy": "opposite_ba_v1",
            "episode_id": ep.episode_id, "order_policy": ("FIXED_ASK_MINUS_TICK"
                if tolerant else "OPPOSITE_BA_ANCHOR_BID"),
            "signed_target_delta": "0", "target_sizing_applied": False,
            "favorite_data_validity": "NOT_USED",
            "actual_delta_before": str(delta), "target_remaining_delta": "0",
            "quantity": str(qty), "anchor_bid_price": str(ep.anchor_bid_price),
            **({"quote_price":str(price), "quote_fixed":True,
                "book_consistency_after_submit":"KEEP_FIXED_QUOTE",
                "price_tolerance":None, "max_price":None}
               if tolerant else {})})}

    def matches_for_submit(self, intent, snapshot, pending_batch):
        with self.lifecycle.feed.lock:
            self.quote_block_reason = None
            if not self.ba_enabled:
                self.quote_block_reason = "BA_REPAIR_DISABLED"
                return False
            if not pending_batch:
                return self.matches(intent, snapshot)
            e = self.executor
            if (len({id(a) for a in pending_batch}) != len(pending_batch)
                    or tuple(a.quote for a in pending_batch) != intent.quotes
                    or any(not any(a is owned for owned in e.attempts)
                           or a.quote.window_id != intent.window_id
                           or a.outcome != "INTENT" or a.post_started or a.order_id is not None
                           or not a.expected_order_id
                           or a.expected_order_id in getattr(e, "known", ())
                           for a in pending_batch)):
                self.quote_block_reason = "REPAIR_RESERVATION_CONTEXT_INVALID"
                return False
            reserved = sum((a.maximum for a in pending_batch), Decimal(0))
            if reserved > e.committed():
                self.quote_block_reason = "REPAIR_RESERVATION_CONTEXT_INVALID"
                return False
            direction, _ = self.select(snapshot)
            if (direction != intent.direction or self.episodes.selected is None
                    or not intent.claim or self.episodes.selected.episode_id != intent.claim[0]):
                return False
            return self._candidates(snapshot, intent.yes, intent.no, intent.window_id,
                                    own_reservation=reserved).get(direction, (None,))[0] == intent.quotes

    def deadline(self, snapshot):
        deadline = super().deadline(snapshot)
        e = self.executor
        cutoff = e.time_anchor+int(e.slug.rsplit("-", 1)[1])+self.cutoff-e.official_time
        return min(deadline, cutoff)

    def boundary_close_reason(self, intent, elapsed):
        return "MARKET_SUBMISSION_CUTOFF" if elapsed >= self.cutoff else None

    def active_snapshot(self, intent):
        # Only the post-start owner may relax BBO alignment. Preparation and
        # the final native POST guard keep using the strict shared snapshot.
        with self.lifecycle.feed.lock:
            ep = getattr(self.episodes, "opportunities", {}).get(intent.claim[0]) if intent.claim else None
            if ep is not None and ep.consumed:
                return self.lifecycle.feed.snapshot(ba_cfg=self.ba_config, allow_pending_top=True)
            return self.lifecycle.snapshot(self, intent.yes, intent.no)

    async def review_active(self, intent, deadline):
        # Shared residual and direction handling, without price/volatility signals.
        if not self.ba_enabled:
            return "BA_REPAIR_DISABLED"
        y, n = self.executor.economic_inventory()
        if not repair_required(y, n):
            return "REPAIR_SMALL_EXPOSURE"
        if (y > n) != (intent.direction == "NO"):
            return "OPPOSITE_BA_DIRECTION_CHANGED"
        return None

    def active_close_reason(self, intent, snapshot):
        if not self.ba_enabled:
            return "BA_REPAIR_DISABLED"
        return super().active_close_reason(intent, snapshot)

    def reconcile_episode(self):
        from polymaker.rdp_sm_sl_ledger import install_reverse_episode

        if self.executor.safe_to_continue and install_reverse_episode(self.executor, mode="WAIT_OPPOSITE_BA"):
            self.lifecycle.cached_slot = None
            return True
        return False

    async def repair(self, yes, no, window_id):
        e = self.executor
        e.update_repair_episode()
        self.reconcile_episode()
        if e.repair_episode is None or e.cooldown.active:
            await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
            return None
        if not self.ba_enabled:
            self.lifecycle.drain_ba()
            if self.restore_tp_enabled:
                from polymaker.rdp_sm_restored_tp import execute_restored_tp

                return await execute_restored_tp(self, window_id)
            self.lifecycle.wait("BA_REPAIR_DISABLED", self.lifecycle.snapshot(self, yes, no))
            await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
            return None
        if self.restore_tp_enabled:
            from polymaker.rdp_sm_restored_tp import execute_restored_tp

            self.lifecycle.drain_ba()
            snapshot = self.lifecycle.snapshot(self, yes, no)
            ep = self.tp_ba_signal(snapshot)
            if ep is None:
                return await execute_restored_tp(self, window_id)
            # One attempt per confirmation even if final admission/signing fails.
            # Otherwise the same unconsumed BA would repeatedly evict the TP.
            self.tp_handled_ba[ep.episode_id] = ep.order_deadline
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        if slot:
            outcome = await self.lifecycle.activate(self, slot)
            if outcome is not None:
                if self.restore_tp_enabled:
                    eid = outcome[1].get("episode_id")
                    opportunity = getattr(self.episodes, "opportunities", {}).get(eid)
                    if opportunity is not None:
                        self.tp_handled_ba[eid] = opportunity.order_deadline
                return outcome
        if self.restore_tp_enabled:
            return await execute_restored_tp(self, window_id)
        await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
        return None
