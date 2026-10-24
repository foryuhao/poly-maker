"""EV REPAIR controller, using the existing owned post-only lifecycle."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
from decimal import Decimal

from polymaker.domain import Side, TradeState
from polymaker.execution.filltruth import reconcile_owned_order
from polymaker.rdp_sm import repair_required
from polymaker.rdp_sm_execution import WindowQuote
from polymaker.rdp_sm_quotes import QuoteUnavailable
from polymaker.repair_model import RepairOrder, RepairPlanner, RepairState


class RepairController:
    phase = "REPAIR"
    cutoff = 240

    def __init__(self, executor, lifecycle):
        self.executor, self.lifecycle = executor, lifecycle
        self.cfg = executor.cfg.rdp_sm.repair
        self.planner = RepairPlanner(self.cfg)
        self.plan = None
        self.next_review = 0.
        self.last_review_revision = None
        self.worker = None
        self.plan_books = None

    @staticmethod
    def fingerprint(snapshot):
        return (snapshot.generation, *((b.best_bid, b.best_ask, b.tick)
                                        for b in (snapshot.yes, snapshot.no)))

    def sigma(self):
        from polymaker.rdp_sm_volatility import estimate_sigma

        feed = self.lifecycle.feed
        return estimate_sigma(feed, monotonic=self.executor.monotonic(),
                              wall=feed.wall(), generation=feed.generation).sigma

    def state(self, snapshot, *, active=None):
        e = self.executor
        sigma = self.sigma()
        if sigma is None:
            return None
        y, n = e.economic_inventory()
        p = float((snapshot.yes.best_bid+snapshot.yes.best_ask)/2)
        other = snapshot.no if y > n else snapshot.yes
        no_p = 1-float((snapshot.no.best_bid+snapshot.no.best_ask)/2)
        if abs(p-no_p) > .02000000001:
            raise QuoteUnavailable("REPAIR_PROBABILITY_INCONSISTENT")
        spent = sum((Decimal(str(f.price))*Decimal(str(f.size)) for f in e.economic_fills()), Decimal(0))
        cash = max(Decimal(0), Decimal(15)-e.committed())
        if active is None and self.lifecycle.account:
            cash = min(cash, Decimal(str(self.lifecycle.account["basic_spendable_budget_usd"])))
        elapsed = e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit("-", 1)[1])
        return RepairState(p, sigma, elapsed, float(y), float(n), float(spent), float(cash),
            float(other.best_bid), float(other.best_ask), float(other.tick),
            float(e.meta.min_order_size), float(e.cfg.rdp_sm.max_order_size),
            (e.repair_episode or {}).get("side"))

    async def solve(self, state, order=None):
        if self.worker is not None and not self.worker.done():
            return None
        revision = self.executor.revision
        generation = self.lifecycle.feed.generation
        self.worker = asyncio.create_task(asyncio.to_thread(self.planner.decide, state, order))
        try:
            result = await asyncio.shield(self.worker)
        except asyncio.CancelledError:
            async def drain():
                with suppress(Exception):
                    await asyncio.shield(self.worker)

            await self.executor._await_owned(drain(), propagate_cancel=False)
            raise
        except (TimeoutError, ValueError, ArithmeticError) as exc:
            self.executor.record("sm_repair_wait", {"reason": str(exc), "model_version": self.cfg.model_version})
            return None
        if revision != self.executor.revision or generation != self.lifecycle.feed.generation:
            return None
        self.executor.record("sm_repair_decision", {"state": asdict(state), "order": asdict(order) if order else None,
                                                   "input_revision": revision, **result})
        return result

    def select(self, snapshot):
        return "REPAIR", None

    def candidates(self, snapshot, yes, no, window_id):
        if not self.plan or self.plan["action"] != "BUY":
            return {}
        p = self.plan
        token = self.executor.meta.yes.token_id if p["side"] == "YES" else self.executor.meta.no.token_id
        quote = WindowQuote(self.executor.meta.condition_id, token, p["side"], Side.BUY,
                            p["price"], p["quantity"], window_id)
        return {"REPAIR": ((quote,), {"repair_plan": {k: v for k, v in p.items() if k != "candidates"},
                                      "order_policy": "EV_OPPOSITE_BEST_BID",
                                      "signed_target_delta": "0", "target_sizing_applied": False,
                                      "actual_delta_before": str(yes-no),
                                      "favorite_data_validity": "NOT_USED"})}

    def deadline(self, snapshot):
        e = self.executor
        return e.time_anchor + int(e.slug.rsplit("-", 1)[1])+self.plan["cancel_at"]-e.official_time

    def matches(self, intent, snapshot):
        if not self.plan or not snapshot.valid:
            return False
        q = intent.quotes[0]
        book = snapshot.yes if q.outcome == "YES" else snapshot.no
        return (Decimal(str(q.price)) == book.best_bid and
                self.fingerprint(snapshot) == self.plan_books and
                Decimal(str(q.size)) == Decimal(str(self.plan["quantity"])) and
                self.executor.delta_candidate_allowed(intent.yes-intent.no, q.outcome, Decimal(str(q.size))))

    async def repair(self, yes, no, window_id):
        self.executor.update_repair_episode()
        if self.executor.check_repair_abandonment():
            return None
        if self.executor.cooldown.active or not repair_required(*self.executor.economic_inventory()):
            await self.executor.sleep(.1)
            return None
        if (self.executor.monotonic() < self.next_review
                and self.last_review_revision == self.executor.revision):
            await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence,
                         min(.5, self.next_review-self.executor.monotonic()))
            return None
        self.next_review = self.executor.monotonic()+self.cfg.review_sec
        self.last_review_revision = self.executor.revision
        snapshot = self.lifecycle.snapshot(self, yes, no)
        if not snapshot.valid:
            self.lifecycle.wait(snapshot.reason, snapshot)
            return None
        state = self.state(snapshot)
        if state is None:
            self.lifecycle.wait("REPAIR_VOLATILITY_WARMUP", snapshot)
            return None
        if state.elapsed >= self.cutoff:
            self.plan = None
            return None
        self.plan_books = self.fingerprint(snapshot)
        self.plan = await self.solve(state)
        if not self.plan or self.plan["action"] != "BUY":
            return None
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        return await self.lifecycle.activate(self, slot) if slot else None

    async def review_active(self, intent, deadline):
        """Observe real execution; no simulated crossing is an actual fill."""
        e = self.executor
        ids = [a.order_id for a in e.attempts if a.quote.window_id == intent.window_id and a.order_id]
        if not ids:
            return None
        for oid in ids:
            await reconcile_owned_order(oid, e.gateway, e.processor, retry_delays=())
        if any(e.processor.has_failed_fill(oid) or e.processor.has_unresolved_fill_key(oid)
               or oid in e.processor.quarantined_orders for oid in ids):
            return "REPAIR_EXECUTION_UNRESOLVED"
        raw = [f for oid in ids for f in e.processor.economic_fills_for_order(oid)]
        quote = intent.quotes[0]
        filled = sum((Decimal(str(f.size)) for f in raw), Decimal(0))
        remaining = Decimal(str(quote.size))-filled
        if remaining <= 0:
            return "REPAIR_FILLED"
        pending = [f for f in raw if f.status is not TradeState.CONFIRMED and
                   (f.trade_id, f.maker_order_id) not in e.position_reconciled]
        y, n = e.economic_inventory()
        pending_qty = sum((Decimal(str(f.size)) for f in pending), Decimal(0))
        possible = y-n+(pending_qty if quote.outcome == "YES" else -pending_qty)
        if not e.delta_candidate_allowed(y-n, quote.outcome, remaining+pending_qty):
            return "REPAIR_REMAINING_DELTA_LIMIT"
        if pending:
            if abs(possible) < Decimal(".1"):
                return "REPAIR_POSSIBLY_PAIRED"
            return None  # preserve the existing lease; no new/extended plan
        if not repair_required(y, n):
            return "REPAIR_SMALL_EXPOSURE"
        snapshot = self.lifecycle.snapshot(self, y, n)
        if not snapshot.valid:
            return snapshot.reason
        state = self.state(snapshot, active=True)
        if state is None:
            return None
        accepted = e.repair_order_started.get(ids[0], e.monotonic())
        order = RepairOrder(quote.outcome, quote.price, float(remaining),
                            max(0., e.monotonic()-accepted),
                            e.official_time+deadline[0]-e.time_anchor-int(e.slug.rsplit("-", 1)[1]))
        result = await self.solve(state, order)
        if not result:
            return None
        current = self.lifecycle.snapshot(self, *e.economic_inventory())
        if not current.valid:
            return current.reason
        if self.fingerprint(current) != self.fingerprint(snapshot):
            return None
        if result["action"] in {"CANCEL", "REPLACE_AFTER_CANCEL"}:
            return "REPAIR_"+result["action"]
        if result["action"] == "KEEP":
            proposed = e.time_anchor+int(e.slug.rsplit("-", 1)[1])+result["cancel_at"]-e.official_time
            if abs(proposed-deadline[0]) > .01:
                e.record("sm_repair_deadline", {"window_id": intent.window_id,
                    "previous_deadline": deadline[0], "new_deadline": proposed,
                    "reason": "MODEL_REPLAN_NOT_TTL_RESET"})
                deadline[0] = proposed
        return None
