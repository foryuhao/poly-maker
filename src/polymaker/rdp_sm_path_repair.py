"""HD/TP/SL adapter for the existing owned post-only execution lifecycle."""
from __future__ import annotations

import math
import time
from dataclasses import asdict
from decimal import ROUND_FLOOR, Decimal
from threading import RLock
from types import SimpleNamespace

from polymaker.domain import Side, TradeState
from polymaker.execution.filltruth import reconcile_owned_order
from polymaker.rdp_sm import _Issue
from polymaker.rdp_sm_execution import CAPITAL_LIMIT, WindowQuote, maker_buy_size
from polymaker.rdp_sm_repair import RepairController
from polymaker.repair_policy import PathPoint, RepairPolicy, repair_readiness

MIN_REPAIR_EXPOSURE = Decimal("2.5")


class PathRepairController(RepairController):
    immediate_activation = True

    def __init__(self, executor, lifecycle):
        super().__init__(executor, lifecycle)
        self.policy = None
        self.episode_id = None
        self.last_path_sequence = -1
        self.last_decision = None
        self.fill_signature = {}
        self._policy_lock = RLock()
        self._state_log_key = None
        self._state_log_mono = -math.inf
        self._small_exposure_key = None

    def elapsed(self, monotonic=None):
        e = self.executor
        return (e.official_time+(e.monotonic() if monotonic is None else monotonic)
                -e.time_anchor-int(e.slug.rsplit("-", 1)[1]))

    def _policy(self):
        ep = self.executor.repair_episode
        if ep is None:
            return None
        if self.episode_id != ep["opening_window"]:
            self.episode_id = ep["opening_window"]
            saved = ep.get("policy_state")
            self.policy = (RepairPolicy.restore(self.cfg.path, saved) if saved
                           else RepairPolicy(self.cfg.path))
            self.last_path_sequence = ep.get("last_path_sequence", -1)
            self.last_decision = None
            self.fill_signature = ep.get("policy_fill_signature", {})
            self._state_log_key = None
            self._state_log_mono = -math.inf
        return self.policy

    def _persist(self):
        ep = self.executor.repair_episode
        if ep is not None and self.policy is not None:
            ep["policy_state"] = self.policy.payload()
            ep["last_path_sequence"] = self.last_path_sequence
            ep["policy_fill_signature"] = dict(self.fill_signature)
            latest = self.policy.points[-1] if self.policy.points else None
            key = (self.policy.mode, self.policy.last_reason, str(self.policy.tp_price),
                   ep["original_qty"], len(self.policy.path_breaks),
                   latest.episode_delta if latest else None,
                   latest.inventory_path_valid if latest else None)
            now = self.executor.monotonic()
            if key == self._state_log_key and 0 <= now-self._state_log_mono < 1:
                return
            self._state_log_key, self._state_log_mono = key, now
            self.executor.record("sm_repair_policy_state", {
                "opening_window": ep["opening_window"], "policy": "hd_tp_sl_v1",
                "first_fill_time_source": ep.get("first_fill_time_source"),
                "first_fill_elapsed": ep.get("first_fill_elapsed"),
                "original_qty": ep["original_qty"],
                "last_path_sequence": self.last_path_sequence,
                "parameters": self.cfg.path.model_dump(mode="json"),
                **ep["policy_state"]})

    def _economics(self, elapsed=None):
        e, ep = self.executor, self.executor.repair_episode
        windows = set(ep.get("windows", [ep["opening_window"]]))
        ids = {a.order_id for a in e.attempts if a.order_id and a.quote.window_id in windows}
        fills = e.economic_fills()
        if elapsed is not None:
            fills = [f for f in fills if e.fill_observed_at.get(
                (f.trade_id, f.maker_order_id), {"elapsed": math.inf})["elapsed"] <= elapsed]
        yes = sum((Decimal(str(f.size)) for f in fills
                   if f.token_id == e.meta.yes.token_id), Decimal(0))
        no = sum((Decimal(str(f.size)) for f in fills
                  if f.token_id == e.meta.no.token_id), Decimal(0))
        own = [f for f in fills if f.maker_order_id in ids]
        own += [f for f in ep.get("source_lots", []) if elapsed is None or
                e.fill_observed_at[(f.trade_id, f.maker_order_id)]["elapsed"] <= elapsed]
        own_y = sum((Decimal(str(f.size)) for f in own
                     if f.token_id == e.meta.yes.token_id), Decimal(0))
        own_n = sum((Decimal(str(f.size)) for f in own
                     if f.token_id == e.meta.no.token_id), Decimal(0))
        cost = sum((Decimal(str(f.price))*Decimal(str(f.size)) for f in own), Decimal(0))
        return yes, no, own_y, own_n, cost

    def _point(self, p, elapsed, sigma, generation, *, historical=False,
               tick=.01, path_segment=0, inventory_path_valid=True):
        y, n, own_y, own_n, cost = self._economics(elapsed if historical else None)
        q0 = Decimal(self.executor.repair_episode["original_qty"])
        mark = Decimal(str(p))*own_y+(1-Decimal(str(p)))*own_n-cost
        return PathPoint(elapsed, p, sigma, generation, float(y-n), float(mark/q0),
                         episode_delta=float(own_y-own_n), tick=float(tick),
                         path_segment=path_segment, inventory_path_valid=inventory_path_valid)

    def _episode_fills(self):
        e, ep = self.executor, self.executor.repair_episode
        windows = set(ep.get("windows", [ep["opening_window"]]))
        ids = {a.order_id for a in e.attempts if a.order_id and a.quote.window_id in windows}
        fills = [f for f in e.economic_fills() if f.maker_order_id in ids]
        fills += ep.get("source_lots", [])
        known = {(f.trade_id, f.maker_order_id) for f in fills}
        pending = any((f.trade_id, f.maker_order_id) not in known
                      for f in e.processor.economic_fills_for_orders(ids))
        return fills, pending

    def _sigma_at(self, point):
        with self.lifecycle.feed.lock:
            points = [p for p in self.lifecycle.feed.probability_points
                      if p[3] == point.generation and p[0] < math.floor(point.wall)
                      and point.monotonic-30 <= p[1] <= point.monotonic]
        pairs = [(a, b) for a, b in zip(points, points[1:], strict=False) if 0 < b[1]-a[1] <= 5]
        duration = sum(b[1]-a[1] for a, b in pairs)
        return (math.sqrt(sum((b[2]-a[2])**2 for a, b in pairs)/duration)
                if len(pairs) >= 10 and duration >= 20 else None)

    def observe(self, snapshot, *, project=False):
        with self._policy_lock:
            return self._observe(snapshot, project=project)

    def _observe(self, snapshot, *, project=False):
        policy = self._policy()
        ep = self.executor.repair_episode
        if policy is None or Decimal(ep["original_qty"]) <= 0:
            raise ValueError("REPAIR_ORIGINAL_QUANTITY_UNKNOWN")
        if "first_fill_elapsed" not in ep:
            raise ValueError("REPAIR_FIRST_FILL_TIME_UNKNOWN")
        if project:
            policy = RepairPolicy.restore(self.cfg.path, policy.payload())
        q0 = float(ep["original_qty"])
        now = self.elapsed()
        now_mono = self.executor.monotonic()
        offset = self.executor.official_time-self.executor.time_anchor-int(self.executor.slug.rsplit("-", 1)[1])
        clock_changed = abs(offset-ep.get("path_clock_offset", offset)) > 2
        if clock_changed:
            policy.break_path("OFFICIAL_CLOCK_CORRECTION")
        cursor = policy.points[-1].elapsed if policy.points else ep["first_fill_elapsed"]
        signature = dict(self.fill_signature)
        fills, pending = self._episode_fills()
        timeline = []
        uncertain_until = None
        for fill in fills:
            key = fill.trade_id+":"+fill.maker_order_id
            value = (str(fill.size), str(fill.price))
            if tuple(signature.get(key, ())) == value:
                continue
            signature[key] = value
            stamp = self.executor.fill_observed_at.get((fill.trade_id, fill.maker_order_id), {})
            native = stamp.get("elapsed") if stamp.get("source") == "VENUE_TRADE_TIME" else None
            uncertain = native is None or native < cursor
            at = (max(cursor, stamp.get("economic_observed_elapsed",
                      stamp.get("observed_elapsed", now))) if uncertain else native)
            # First observation is an anchor, not a synthetic opening-price move.
            if at <= now:
                timeline.append((at, 0, "FILL", uncertain))
                if uncertain:
                    uncertain_until = max(uncertain_until or at, at)
        removed = set(signature)-{f.trade_id+":"+f.maker_order_id for f in fills}
        if removed:
            for key in removed:
                del signature[key]
            policy.break_inventory("ECONOMIC_FACT_REMOVED")
        with self.lifecycle.feed.lock:
            history = list(self.lifecycle.feed.repair_price_points)
            segment = self.lifecycle.feed.repair_path_segment
            points = [p for p in history
                      if p.sequence > self.last_path_sequence
                      and p.monotonic >= ep.get("opening_monotonic", -math.inf)
                      and (not clock_changed or p.monotonic >= now_mono)
                      and self.elapsed(p.monotonic) <= now
                      and p.sequence <= snapshot.sequence]
        if (not policy.points and not clock_changed and self.last_path_sequence < 0
                and not policy.path_breaks):
            # A quiet quote can predate the fill. Use its actual as-of mark,
            # never the opening cost, to retain the FIRST post-fill price move.
            before = [s for s in history if self.elapsed(s.monotonic) <= ep["first_fill_elapsed"]
                      and s.sequence <= snapshot.sequence]
            anchor = before[-1] if before else None
            after = next((s for s in history if self.elapsed(s.monotonic) > ep["first_fill_elapsed"]
                          and s.sequence <= snapshot.sequence), None)
            end_generation = after.generation if after else snapshot.generation
            end_segment = after.path_segment if after else segment
            if anchor and anchor.generation == end_generation and anchor.path_segment == end_segment:
                timeline.append((ep["first_fill_elapsed"], -1, "ANCHOR", anchor))
            else:
                policy.break_path("FIRST_FILL_MARK_UNKNOWN")
        if (policy.points and history and self.last_path_sequence >= 0
                and self.last_path_sequence < history[0].sequence
                and self.elapsed(history[0].monotonic) > cursor):
            policy.break_path("PRICE_HISTORY_OVERWRITTEN")
        timeline.extend((self.elapsed(s.monotonic), s.sequence, "PRICE", s) for s in points)
        logged = []
        # Discarded pre-fill / old-clock prices are consumed too. Otherwise a
        # quiet book spins the price-event scheduler, or replays discarded data
        # on the next observation after the clock-offset marker is updated.
        last_sequence = max([self.last_path_sequence]+[s.sequence for s in history
            if s.sequence <= snapshot.sequence and self.elapsed(s.monotonic) <= now])
        for elapsed, sequence, kind, sample in sorted(timeline, key=lambda row: (row[0], row[1])):
            if kind == "PRICE":
                last_sequence = max(last_sequence, sequence)
            if elapsed < ep["first_fill_elapsed"] or (policy.points and elapsed < policy.points[-1].elapsed):
                continue
            valid = not pending and (uncertain_until is None or elapsed >= uncertain_until)
            if kind == "FILL":
                if not policy.points:
                    continue
                previous = policy.points[-1]
                if sample:
                    policy.break_inventory("FILL_TIME_OR_AVAILABILITY_UNKNOWN")
                mono = self.executor.time_anchor+int(self.executor.slug.rsplit("-", 1)[1])+elapsed-self.executor.official_time
                stamp = SimpleNamespace(generation=previous.generation, monotonic=mono,
                                        wall=self.lifecycle.feed.wall()-(now-elapsed))
                point = self._point(previous.p, elapsed, self._sigma_at(stamp), previous.generation,
                                    historical=True, tick=previous.tick, path_segment=previous.path_segment,
                                    inventory_path_valid=valid)
            else:
                sigma_point = sample
                if kind == "ANCHOR":
                    mono = self.executor.time_anchor+int(self.executor.slug.rsplit("-", 1)[1])+elapsed-self.executor.official_time
                    sigma_point = SimpleNamespace(generation=sample.generation, monotonic=mono,
                        wall=self.lifecycle.feed.wall()-(now-elapsed))
                point = self._point(sample.p, elapsed, self._sigma_at(sigma_point), sample.generation,
                                    historical=True, tick=sample.yes_tick if ep["side"] == "YES" else sample.no_tick,
                                    path_segment=sample.path_segment, inventory_path_valid=valid)
            policy.decide(point, original_side=ep["side"], original_qty=q0,
                          first_fill_elapsed=ep["first_fill_elapsed"])
            logged.append({**asdict(point), "kind": kind, "sequence": sequence})
        if logged and not project:
            self.executor.record("sm_repair_market_path", {"opening_window": self.episode_id,
                                                           "points": logged})
        p = float((snapshot.yes.best_bid+snapshot.yes.best_ask)/2)
        if abs(p-(1-float((snapshot.no.best_bid+snapshot.no.best_ask)/2))) > .02000000001:
            raise ValueError("REPAIR_PROBABILITY_INCONSISTENT")
        book = snapshot.yes if ep["side"] == "YES" else snapshot.no
        point = self._point(p, now, self.sigma(), snapshot.generation,
                            tick=book.tick, path_segment=segment, inventory_path_valid=not pending)
        features = policy.observe(point, original_side=ep["side"], original_qty=q0)
        decision = policy.decide(point, original_side=ep["side"], original_qty=q0,
                                 first_fill_elapsed=ep["first_fill_elapsed"], features=features)
        decision["next_review_at"] = min(point.elapsed+self.cfg.review_sec,
                                         240. if policy.mode == "SL" else 180.)
        decision["wake_on"] = ["PRICE_EVENT", "EXECUTION_FACT", "TIME_BOUNDARY"]
        # Only a bounded causal window is needed for future short-shock tests;
        # the cumulative price peak is separately preserved across the episode.
        while (len(policy.points) > 2
               and policy.points[1].elapsed < point.elapsed-self.cfg.path.shock_max_age_sec):
            policy.points.popleft()
        if not project:
            ep["path_clock_offset"] = offset
            self.last_decision = decision
            self.last_path_sequence = last_sequence
            self.fill_signature = signature
            self._persist()
        return decision, point

    def _blocked(self, reason, decision=None, *, discard_plan=True):
        if discard_plan:
            self.plan = None
        self.executor.record("sm_repair_execution_blocked", {
            "policy": "hd_tp_sl_v1", "mode": self.policy.mode if self.policy else None,
            "reason": reason, "decision": decision, "readiness": repair_readiness(self.cfg)})

    def _make_plan(self, snapshot, decision, point):
        e, ep = self.executor, self.executor.repair_episode
        if point.elapsed >= 240 or abs(Decimal(str(point.delta))) <= MIN_REPAIR_EXPOSURE:
            return None
        side = "NO" if point.delta > 0 else "YES"
        if side == ep["side"] and abs(point.delta) <= float(self.cfg.delta_limit):
            raise ValueError("MINIMUM_FORCED_REVERSE_RESIDUAL")
        book = snapshot.yes if side == "YES" else snapshot.no
        price = self.policy.price(bid=book.best_bid, ask=book.best_ask,
                                  tick=book.tick, sigma=point.sigma)
        if price is None:
            return None
        y, n = e.economic_inventory()
        qty = maker_buy_size(price, abs(y-n), Decimal(str(e.meta.min_order_size)))
        cash = max(Decimal(0), getattr(e, "capital_limit", CAPITAL_LIMIT)-e.committed())
        if self.lifecycle.account is not None:
            cash = min(cash, Decimal(str(self.lifecycle.account["basic_spendable_budget_usd"])))
        if abs(y-n) > self.cfg.delta_limit:
            # A true breach must still admit a feasible partial restoration if
            # full pairing cannot be funded or exceeds the order-size bound.
            qty = min(qty, e.cfg.rdp_sm.max_order_size,
                      (cash/price).quantize(Decimal(".01"), rounding=ROUND_FLOOR))
            if qty < maker_buy_size(price, Decimal(0), Decimal(str(e.meta.min_order_size))):
                raise ValueError("LIMIT_RECOVERY_MINIMUM_NOT_FEASIBLE")
        if qty > e.cfg.rdp_sm.max_order_size:
            raise ValueError("MINIMUM_ORDER_EXCEEDS_MAXIMUM")
        after = y-n+(qty if side == "YES" else -qty)
        if abs(after) >= abs(y-n):
            raise ValueError("REPAIR_WOULD_NOT_REDUCE_DELTA")
        if not e.delta_candidate_allowed(y-n, side, qty):
            raise ValueError("SESSION_DELTA_LIMIT")
        if price*qty > cash:
            raise ValueError("OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT")
        return {**decision, "policy": "hd_tp_sl_v1", "action": "BUY", "side": side,
                "price": float(price), "quantity": float(qty), "tick": str(book.tick),
                "cancel_at": 180. if self.policy.mode == "TP" else 240.,
                "next_review_at": min(point.elapsed+self.cfg.review_sec,
                                      180. if self.policy.mode == "TP" else 240.),
                "switch_conditions": ["PATH_SL", "T180"] if self.policy.mode == "TP" else ["PAIRED", "T240"],
                "candidates": [], "parameter_status": self.cfg.path.calibration_status}

    async def repair(self, yes, no, window_id):
        if self.elapsed() >= 240:
            self.plan = None
            return None
        e, feed = self.executor, self.lifecycle.feed
        y, n = e.economic_inventory()
        if abs(y-n) <= MIN_REPAIR_EXPOSURE:
            self.plan = None
            key = (e.repair_episode["opening_window"] if e.repair_episode else None, y-n)
            if key != self._small_exposure_key:
                e.record("sm_repair_small_exposure", {
                    "reason": "EXPOSURE_AT_OR_BELOW_REPAIR_MINIMUM",
                    "actual_delta": str(y-n), "threshold_shares": str(MIN_REPAIR_EXPOSURE),
                    "action": "WAIT_WITH_RESIDUAL", "inventory_flat": y == n,
                })
                self._small_exposure_key = key
            await feed.wait_after(feed.sequence, .1)
            return None
        self._small_exposure_key = None
        if self.elapsed() >= 180:
            self.boundary_close_reason(None, self.elapsed())
        price_sequence = feed.repair_price_points[-1].sequence if feed.repair_price_points else -1
        force_due = self.elapsed() >= 180 and (self.policy is None or self.policy.mode != "SL")
        if (not force_due and e.monotonic() < self.next_review
                and self.last_review_revision == e.revision
                and price_sequence == self.last_path_sequence):
            await feed.wait_after(feed.sequence, min(.1, self.next_review-e.monotonic()))
            return None
        self.next_review = e.monotonic()+self.cfg.review_sec
        self.last_review_revision = e.revision
        snapshot = self.lifecycle.snapshot(self, yes, no)
        if not snapshot.valid:
            if self._policy() is not None:
                self.policy.break_path(snapshot.reason)
                self._persist()
            self.lifecycle.wait(snapshot.reason, snapshot)
            return None
        try:
            decision, point = self.observe(snapshot)
            self.plan = self._make_plan(snapshot, decision, point)
        except ValueError as exc:
            self._blocked(str(exc), self.last_decision)
            await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
            return None
        self.executor.record("sm_repair_decision", self.plan or {
            **decision, "action": "HOLD", "policy": "hd_tp_sl_v1"})
        if not self.plan:
            await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
            return None
        if self.policy.mode == "SL":
            return await self.execute_sl(window_id)
        await self.executor.journal.flush()
        try:
            slot = await self.lifecycle.prepare(self, yes, no, window_id)
        except _Issue as exc:
            if str(exc) != "QUOTE_WOULD_TAKE":
                raise
            # Preparation rereads the book after the decision's WAL flush.
            # No POST has started here: discard only this stale candidate and
            # recompute promptly, keeping the episode and its latched mode.
            self.executor.record("sm_repair_quote_invalidated", {
                "window_id": window_id, "policy": "hd_tp_sl_v1",
                "reason": str(exc), "mode": self.plan["mode"],
                "side": self.plan["side"], "price": self.plan["price"],
                "quantity": self.plan["quantity"], "post_started": False,
                "retry": "RECOMPUTE_ON_NEXT_REVIEW",
            })
            self.plan = None
            self.next_review = -math.inf
            self.lifecycle.cached_slot = None
            return None
        return await self.lifecycle.activate(self, slot) if slot else None

    async def execute_sl(self, window_id):
        from polymaker.rdp_sm_execution import WindowResult
        from polymaker.rdp_sm_sl import SLExecution
        from polymaker.rdp_sm_sl_ledger import install_reverse_episode

        e = self.executor
        initial_delta = e.economic_inventory()[0]-e.economic_inventory()[1]
        initial_plan = self.plan
        actor = SLExecution(self, window_id)
        created = await e._await_owned(actor.run(), propagate_cancel=False)
        if not created:
            return None
        safe = await e._await_owned(e._verify_close(cancel=True), propagate_cancel=False)
        if not safe and e.pending_confirmations:
            await e._await_owned(e._wait_for_confirmation(), propagate_cancel=False)
            safe = e.safe_to_continue
        e.update_repair_episode()
        if safe:
            install_reverse_episode(e)
        self.plan = None
        result = WindowResult(e.session_id, window_id, e.meta.condition_id, actor.started_wall,
            end_time=time.time(), requested_quotes=[a.quote for a in created],
            placement_results=[asdict(a) for a in created], known_order_ids=sorted(e.known),
            confirmed_fills=[asdict(f) for f in e.economic_fills()],
            yes_inventory=float(e.economic_inventory()[0]), no_inventory=float(e.economic_inventory()[1]),
            delta=float(e.economic_inventory()[0]-e.economic_inventory()[1]),
            capital_committed=float(e.committed()),
            close_barrier="SAFE_TO_CONTINUE" if safe else "BLOCKED", block_reason=e.reason,
            t0_monotonic=actor.started_monotonic, close_monotonic=e.monotonic())
        e.reads.network.complete_batch(actor.group, safe=safe)
        e.record("sm_window_result", result.to_dict())
        await e.journal.flush()
        return result, {"repair_plan": initial_plan, "mode": "SL",
            "order_policy": "SIGNAL_DRIVEN_SL", "signed_target_delta": "0",
            "target_sizing_applied": False, "favorite_data_validity": "NOT_USED",
            "actual_delta_before": str(initial_delta)}

    def candidates(self, snapshot, yes, no, window_id):
        if not self.plan:
            return {}
        plan = self.plan
        token = self.executor.meta.yes.token_id if plan["side"] == "YES" else self.executor.meta.no.token_id
        quote = WindowQuote(self.executor.meta.condition_id, token, plan["side"], Side.BUY,
                            plan["price"], plan["quantity"], window_id)
        return {"REPAIR": ((quote,), {"repair_plan": plan,
                "order_policy": "TP_FIXED_PRICE" if plan["mode"] == "TP" else "SL_ASK_MINUS_TICK",
                "signed_target_delta": "0", "target_sizing_applied": False,
                "actual_delta_before": str(yes-no), "favorite_data_validity": "NOT_USED"})}

    def matches(self, intent, snapshot):
        if not self.plan or not snapshot.valid or not self.policy:
            return False
        elapsed = self.elapsed()
        if elapsed >= self.plan["cancel_at"] or self.policy.mode != self.plan["mode"]:
            return False
        q = intent.quotes[0]
        book = snapshot.yes if q.outcome == "YES" else snapshot.no
        price = Decimal(str(q.price))
        if (str(book.tick) != self.plan["tick"] or price >= book.best_ask
                or price <= 0 or price % book.tick or q.size != self.plan["quantity"]):
            return False
        if self.policy.mode == "SL" and price != book.best_ask-book.tick:
            return False
        if self.policy.mode == "TP":
            # The SDK's final guard may run after a material price move. Check
            # its risk meaning without mutating policy/journal in that worker.
            ep = self.executor.repair_episode
            if ep is None:
                return False
            try:
                decision, _ = self.observe(snapshot, project=True)
            except ValueError:
                return False
            if decision["mode"] != "TP":
                return False
        delta, qty = intent.yes-intent.no, Decimal(str(q.size))
        after = delta+(qty if q.outcome == "YES" else -qty)
        return (abs(delta) > MIN_REPAIR_EXPOSURE and abs(after) < abs(delta)
                and self.executor.delta_candidate_allowed(delta, q.outcome, qty))

    def on_order_accepted(self, intent):
        if self.plan["mode"] == "TP":
            self.policy.accepted_tp(Decimal(str(intent.quotes[0].price)), Decimal(self.plan["tick"]))
            self._persist()

    def boundary_close_reason(self, intent, elapsed):
        """Independent wall-time transition, including sigma warmup/unknown POST."""
        with self._policy_lock:
            if 180 <= elapsed < 240 and self._policy() is not None and self.policy.mode != "SL":
                self.policy.force_sl(elapsed)
                self._persist()
            if (self.policy is not None and self.policy.mode == "SL"
                    and self.plan is not None and self.plan["mode"] != "SL"):
                return "REPAIR_MODE_SL_CANCEL_OLD_STATE"
        return None

    def _active_result(self, intent, action, reason=None, remaining=None):
        quote = intent.quotes[0]
        self.executor.record("sm_repair_decision", {
            **(self.last_decision or {}), "policy": "hd_tp_sl_v1",
            "mode": self.policy.mode, "action": action,
            "execution_reason": reason, "side": quote.outcome,
            "price": quote.price, "remaining_quantity": str(remaining) if remaining is not None else None,
            "order_window": intent.window_id})
        return reason

    async def review_active(self, intent, deadline):
        e = self.executor
        # TP risk intent is evaluated before REST reconciliation. A transition
        # to SL hands off directly; only the final handoff uses full close.
        y, n = e.economic_inventory()
        snapshot = self.lifecycle.snapshot(self, y, n)
        if snapshot.valid and self._policy() is not None:
            try:
                decision, _ = self.observe(snapshot)
                if self.plan["mode"] != decision["mode"]:
                    return self._active_result(intent, "CANCEL", "REPAIR_MODE_SL_CANCEL_OLD_STATE")
            except ValueError as exc:
                self._blocked(str(exc), discard_plan=False)
        ids = [a.order_id for a in e.attempts if a.quote.window_id == intent.window_id and a.order_id]
        if not ids:
            return None
        for oid in ids:
            await reconcile_owned_order(oid, e.gateway, e.processor, retry_delays=())
        boundary = self.boundary_close_reason(intent, self.elapsed())
        if boundary:
            return boundary
        if any(e.processor.has_failed_fill(oid) or e.processor.has_unresolved_fill_key(oid)
               or oid in e.processor.quarantined_orders for oid in ids):
            return "REPAIR_EXECUTION_UNRESOLVED"
        raw = [f for oid in ids for f in e.processor.economic_fills_for_order(oid)]
        quote = intent.quotes[0]
        remaining = Decimal(str(quote.size))-sum((Decimal(str(f.size)) for f in raw), Decimal(0))
        if remaining <= 0:
            return "REPAIR_FILLED"
        y, n = e.economic_inventory()
        pending = [f for f in raw if f.status is not TradeState.CONFIRMED and
                   (f.trade_id, f.maker_order_id) not in e.position_reconciled]
        pending_qty = sum((Decimal(str(f.size)) for f in pending), Decimal(0))
        if not e.delta_candidate_allowed(y-n, quote.outcome, remaining+pending_qty):
            return "REPAIR_REMAINING_DELTA_LIMIT"
        if abs(y-n) < Decimal(".1"):
            return "REPAIR_PAIRED"
        snapshot = self.lifecycle.snapshot(self, y, n)
        if not snapshot.valid:
            return snapshot.reason
        try:
            decision, _ = self.observe(snapshot)
        except ValueError as exc:
            self._blocked(str(exc), discard_plan=False)
            return None
        if self.plan["mode"] != decision["mode"]:
            return self._active_result(intent, "CANCEL", "REPAIR_MODE_SL_CANCEL_OLD_STATE", remaining)
        if pending:
            return None
        if abs(y-n) <= MIN_REPAIR_EXPOSURE:
            return self._active_result(intent, "CANCEL", "REPAIR_SMALL_EXPOSURE", remaining)
        book = snapshot.yes if quote.outcome == "YES" else snapshot.no
        if (decision["mode"] == "SL"
                and Decimal(str(quote.price)) <= book.best_ask-2*book.tick):
            return self._active_result(intent, "REPLACE_AFTER_CANCEL", "REPAIR_SL_REPRICE", remaining)
        return self._active_result(intent, "KEEP", remaining=remaining)
