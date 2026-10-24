"""Opposite BA exits: fresh absorption, anchor Bid, exact pairing, no hold timeout."""
from __future__ import annotations

import math
from decimal import ROUND_FLOOR, Decimal

from polymaker.domain import Side
from polymaker.rdp_sm_execution import WindowQuote, maker_buy_size
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
                "OPPOSITE_BA_NO_POSITION" if e.repair_episode is None or opposite is None else
                "OPPOSITE_BA_DIRECTION_CHANGED" if e.repair_episode.get("side") != held else
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
        # The lifecycle requires the previous ownership barrier before preparing.
        # Never round UP the residual to the minimum order or open a reverse leg.
        qty = min(abs(delta), self.executor.cfg.rdp_sm.max_order_size).quantize(
            Decimal(".01"), rounding=ROUND_FLOOR)
        minimum = maker_buy_size(price, Decimal(0), Decimal(str(self.executor.meta.min_order_size)))
        if qty < minimum:
            self.quote_block_reason = "OPPOSITE_BA_RESIDUAL_BELOW_MINIMUM"
            return {}
        if price != book.best_bid or not 0 < price < book.best_ask:
            self.quote_block_reason = "BA_ANCHOR_INVALID"
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
        # No price review, sigma, TP/SL handoff or small-residual early completion.
        y, n = self.executor.economic_inventory()
        if y == n:
            return "OPPOSITE_BA_PAIRED"
        if (y > n) != (intent.direction == "NO"):
            return "OPPOSITE_BA_DIRECTION_CHANGED"
        return None

    async def repair(self, yes, no, window_id):
        e = self.executor
        e.update_repair_episode()
        if e.repair_episode is None or e.cooldown.active:
            await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
            return None
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        if slot:
            return await self.lifecycle.activate(self, slot)
        await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
        return None
