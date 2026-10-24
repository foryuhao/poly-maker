"""Opposite BA exits with shared repair quantities and residual tolerance."""
from __future__ import annotations

import math
from decimal import Decimal

from polymaker.domain import Side
from polymaker.rdp_sm import repair_required
from polymaker.rdp_sm_execution import WindowQuote, repair_buy_quantity
from polymaker.rdp_sm_normal import BANormalController
from polymaker.rdp_sm_signal import Signal


class OppositeBAController(BANormalController):
    phase = "REPAIR"
    cutoff = 240
    ba_signal = True

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
            signal = self.episodes.evaluate(snapshot)
            ep = self.episodes.selected
            y, n = e.economic_inventory()
            opposite = "NO" if y > n else "YES" if n > y else None
            held = "YES" if y > n else "NO" if n > y else None
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

    def _candidates(self, snapshot, yes, no, window_id):
        self.quote_block_reason = None
        direction, _ = self.select(snapshot)
        if direction is None:
            self.quote_block_reason = self.signal_block_reason
            return {}
        ep = self.episodes.selected
        book = snapshot.no if direction == "NO" else snapshot.yes
        price = ep.anchor_bid_price
        delta = yes-no
        current_y, current_n = self.executor.economic_inventory()
        if delta != current_y-current_n or (delta > 0) != (direction == "NO"):
            self.quote_block_reason = "OPPOSITE_BA_INVENTORY_CHANGED"
            return {}
        if price != book.best_bid or not 0 < price < book.best_ask:
            self.quote_block_reason = "BA_ANCHOR_INVALID"
            return {}
        e = self.executor
        cash = max(Decimal(0), e.capital_limit-e.committed())
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
            "episode_id": ep.episode_id, "order_policy": "OPPOSITE_BA_ANCHOR_BID",
            "actual_delta_before": str(delta), "target_remaining_delta": "0",
            "quantity": str(qty), "anchor_bid_price": str(price)})}

    def deadline(self, snapshot):
        deadline = super().deadline(snapshot)
        e = self.executor
        cutoff = e.time_anchor+int(e.slug.rsplit("-", 1)[1])+self.cutoff-e.official_time
        return min(deadline, cutoff)

    def boundary_close_reason(self, intent, elapsed):
        return "MARKET_SUBMISSION_CUTOFF" if elapsed >= self.cutoff else None

    async def review_active(self, intent, deadline):
        # Shared residual and direction handling, without price/volatility signals.
        y, n = self.executor.economic_inventory()
        if not repair_required(y, n):
            return "REPAIR_SMALL_EXPOSURE"
        if (y > n) != (intent.direction == "NO"):
            return "OPPOSITE_BA_DIRECTION_CHANGED"
        return None

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
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        if slot:
            return await self.lifecycle.activate(self, slot)
        await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
        return None
