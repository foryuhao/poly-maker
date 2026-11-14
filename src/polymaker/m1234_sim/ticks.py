"""Generation/revision-bound public constraint verification, driven only by input events."""

import math
from copy import deepcopy
from decimal import Decimal

from polymaker.m1234_sim.events import digest

TICK_POLICY = {"timeout_seconds": 5, "attempts_per_target": 4,
               "requests_per_market": 64, "retry_max_seconds": 4}


class TickVerification:
    def __init__(self, signals, clock, emit):
        self.emit = emit
        self.market = signals.m1.meta.slug
        self.tokens = sorted(signals.m1.books)
        self.book_view = next(name for name, feed in signals.views.items() if feed is signals.m1)
        self.target = self.bindings(signals)
        self.target_id = digest(self.target)
        self.verified_target = self.target_id
        self.state, self.reason = "VERIFIED", None
        self.active = None
        self.attempts = self.requests = 0
        self.next_due = clock.mono
        self.first_connect_bound = False
        self.invalidated = self.closed = False
        self.unverified_since = None
        self.unverified_seconds = 0.0
        self.stats = {k: 0 for k in ("notifications", "duplicate_notifications", "changes",
                                     "requests", "successes", "failures", "timeouts",
                                     "conflicts", "stale_results", "unmatched_results", "cancelled",
                                     "invalid_notification_times")}
        self.last_notified = {t: str(b.tick) for t, b in signals.m1.books.items()}
        self.source_floor = {}
        self.constraints = {
            t: {"tick": str(b.tick), "known": True, "authority": "INIT_PUBLIC_REST",
                "generation": 0, "revision": 0, "received_mono": clock.mono,
                "available_mono": clock.mono, "source_mono": clock.mono}
            for t, b in signals.m1.books.items()
        }

    @staticmethod
    def bindings(signals):
        return {name: {"generation": f.generation, "revision": f.tick_revision,
                       "ticks": {t: str(b.tick) for t, b in f.books.items()}}
                for name, f in signals.views.items()}

    def block(self, signals, now, reason):
        for feed in signals.views.values():
            feed.tick_verified.clear()
        if self.unverified_since is None and not self.closed:
            self.unverified_since = now
        self.state, self.reason = "UNVERIFIED", reason
        self.verified_target = None

    def observe(self, signals, clock, origin):
        current = self.bindings(signals)
        if current == self.target:
            return
        initial = (
            origin == "CONNECT" and not self.first_connect_bound and not self.invalidated
            and all(self.target[n]["generation"] == 0 and row["generation"] == 1
                    and row["ticks"] == self.target[n]["ticks"]
                    and row["revision"] == self.target[n]["revision"]
                    for n, row in current.items())
        )
        previous = self.target
        self.target, self.target_id = current, digest(current)
        self.attempts, self.next_due = 0, clock.mono
        if initial:
            self.first_connect_bound = True
            self.verified_target = self.target_id
            for t in self.tokens:
                self.constraints[t]["generation"] = signals.m1.generation
            self.emit("TICK_INITIAL_GENERATION_BOUND", {"target": current, "at": clock.mono})
            return
        self.invalidated = False
        self.block(signals, clock.mono, "WS_TICK_UNVERIFIED")
        if self.requests >= TICK_POLICY["requests_per_market"]:
            self.reason = "REQUEST_BUDGET_EXHAUSTED"
        self.stats["changes"] += 1
        for token, book in signals.m1.books.items():
            generation_changed = (
                current[self.book_view]["generation"] != previous[self.book_view]["generation"]
            )
            tick_changed = (
                current[self.book_view]["ticks"][token] != previous[self.book_view]["ticks"][token]
            )
            if not generation_changed and not tick_changed:
                self.constraints[token]["revision"] = signals.m1.tick_revision
                continue
            self.constraints[token] = {
                "tick": str(book.tick), "known": False,
                "authority": "WS" if tick_changed else "GENERATION_PENDING",
                "generation": signals.m1.generation, "revision": signals.m1.tick_revision,
                "received_mono": clock.mono, "available_mono": clock.mono, "source_mono": None,
            }
        self.emit("TICK_CONSTRAINT_CHANGED", {"target": current, "target_id": self.target_id,
                                              "origin": origin, "at": clock.mono})

    def notice(self, row, signals, clock, received, received_wall):
        token, tick = row.get("asset_id"), str(Decimal(str(row["new_tick_size"])))
        self.stats["notifications"] += 1
        if token in self.last_notified and Decimal(self.last_notified[token]) == Decimal(tick):
            self.stats["duplicate_notifications"] += 1
        self.last_notified[token] = tick
        if token in self.constraints and Decimal(tick) == signals.m1.books[token].tick:
            try:
                stamp = float(row["timestamp"]) / 1000
                valid = math.isfinite(stamp) and -.002 - 1e-9 <= received_wall - stamp <= .5 + 1e-9
            except (KeyError, ValueError, TypeError):
                valid = False
            if not valid:
                self.stats["invalid_notification_times"] += 1
                return  # A stale duplicate cannot destroy a newer known constraint.
            source = received + (stamp - received_wall)
            previous_source = self.source_floor.get(token)
            if previous_source is not None and source + 1e-6 < previous_source:
                self.stats["invalid_notification_times"] += 1
                return
            self.source_floor[token] = source if previous_source is None else max(source, previous_source)
            self.constraints[token] = {
                "tick": tick, "known": True, "authority": "WS",
                "generation": signals.m1.generation, "revision": signals.m1.tick_revision,
                "received_mono": received, "available_mono": clock.mono, "source_mono": source,
            }

    def invalidate(self, signals, clock, reason):
        self.invalidated = True
        self.block(signals, clock.mono, "DISCONNECTED:" + reason)
        for constraint in self.constraints.values():
            constraint["known"] = False

    def propose(self, signals, now):
        if (self.closed or self.state == "VERIFIED" or self.active is not None
                or self.invalidated or not all(f.connected for f in signals.views.values())
                or now < self.next_due or self.attempts >= TICK_POLICY["attempts_per_target"]
                or self.requests >= TICK_POLICY["requests_per_market"]):
            return None
        return {"request_id": f"tick:{self.market}:{self.requests + 1}", "market": self.market,
                "tokens": self.tokens, "target": deepcopy(self.target),
                "target_id": self.target_id, "attempt": self.attempts + 1}

    def start(self, request, signals, clock):
        expected = self.propose(signals, clock.mono)
        if expected is None or request != expected:
            raise ValueError("SIM_TICK_REQUEST_IDENTITY_OR_ORDER")
        self.attempts += 1
        self.requests += 1
        self.stats["requests"] += 1
        self.active = {**deepcopy(request), "requested_mono": clock.mono,
                       "requested_wall": clock.wall}
        self.emit("TICK_REVERIFY_REQUEST", self.active)

    def finish(self, result, signals, clock):
        active = self.active
        reason = None
        if active is None or result.get("request_id") != active["request_id"]:
            self.stats["unmatched_results"] += 1
            reason = "UNMATCHED_REQUEST"
        elif (result.get("target_id") != active["target_id"]
              or result.get("target") != active["target"]
              or result.get("market") != active["market"]
              or self.target_id != active["target_id"] or self.invalidated
              or not all(f.connected for f in signals.views.values())):
            self.stats["stale_results"] += 1
            self.active = None
            reason = "STALE_REVISION_OR_GENERATION"
        else:
            self.active = None
            times = [result.get("sent_mono"), result.get("received_mono"),
                     result.get("sent_wall"), result.get("received_wall")]
            valid_times = all(isinstance(t, (float, int)) and math.isfinite(t) for t in times)
            if valid_times:
                valid_times = (active["requested_mono"] - 1e-6 <= times[0] <= times[1]
                               and times[1] <= clock.mono + 1e-6
                               and active["requested_wall"] - 1e-6 <= times[2] <= times[3]
                               and times[3] <= clock.wall + 1e-6)
            if not valid_times:
                reason = "INVALID_RESPONSE_AVAILABILITY_TIME"
                self.stats["failures"] += 1
            elif result.get("status") != "SUCCESS":
                reason = result.get("status", "FAILED")
                self.stats["cancelled"] += reason == "CANCELLED"
                self.stats["failures"] += reason != "CANCELLED"
                self.stats["timeouts"] += reason == "TIMEOUT"
            else:
                try:
                    signals.ticks(result["ticks"])
                except (ValueError, KeyError, ArithmeticError, TypeError) as exc:
                    reason = str(exc) if isinstance(exc, ValueError) else "INVALID_TICK_RESPONSE"
                    self.stats["conflicts"] += 1
                    self.stats["failures"] += 1
                else:
                    self.target = self.bindings(signals)
                    self.target_id = digest(self.target)
                    self.verified_target = self.target_id
                    self.state, self.reason = "VERIFIED", None
                    if self.unverified_since is not None:
                        self.unverified_seconds += clock.mono - self.unverified_since
                        self.unverified_since = None
                    self.stats["successes"] += 1
                    for token, book in signals.m1.books.items():
                        constraint = self.constraints[token]
                        if not constraint["known"] or Decimal(constraint["tick"]) != book.tick:
                            constraint.update({"authority": "PUBLIC_REST",
                                               "received_mono": result["received_mono"],
                                               "available_mono": clock.mono,
                                               "source_mono": clock.mono})
                        self.constraints[token].update({
                            "tick": str(book.tick), "known": True,
                            "generation": signals.m1.generation,
                            "revision": signals.m1.tick_revision,
                            "verified_at": clock.mono,
                        })
            if reason is not None:
                self.block(signals, clock.mono, reason)
                self.next_due = clock.mono + min(2 ** (self.attempts - 1),
                                                  TICK_POLICY["retry_max_seconds"])
                if self.attempts >= TICK_POLICY["attempts_per_target"]:
                    self.reason = "RETRY_EXHAUSTED:" + reason
                if self.requests >= TICK_POLICY["requests_per_market"]:
                    self.reason = "REQUEST_BUDGET_EXHAUSTED:" + reason
        self.emit("TICK_REVERIFY_RESULT", {"at": clock.mono, "result": result,
                                           "applied": reason is None, "reason": reason,
                                           "current_target_id": self.target_id})

    def close(self, clock):
        if self.unverified_since is not None:
            self.unverified_seconds += clock.mono - self.unverified_since
            self.unverified_since = None
        self.closed = True

    def snapshot(self, now):
        elapsed = self.unverified_seconds
        if self.unverified_since is not None and not self.closed:
            elapsed += now - self.unverified_since
        return {"state": self.state, "reason": self.reason, "target": self.target,
                "target_id": self.target_id, "verified_target": self.verified_target,
                "active_request": self.active, "attempts_current_target": self.attempts,
                "next_due": self.next_due, "stats": self.stats,
                "unverified_seconds": elapsed, "constraints": self.constraints,
                "notification_source_floor": self.source_floor,
                "closed": self.closed, "policy": TICK_POLICY}
