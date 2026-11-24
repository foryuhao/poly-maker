"""SOP v1.1 event-time windows, availability-time confirmation, and fixed queues."""

from bisect import bisect_left, insort_right
from collections import deque
from dataclasses import dataclass, field
from statistics import median

from .model import EPS, ZERO, D


class Coverage(list):
    def append(self, value):
        if not self or value >= self[-1]:
            super().append(value)
        else:
            insort_right(self, value)

    def extend(self, values):
        for value in values:
            self.append(value)


class Flow:
    def __init__(self):
        self.trades, self.coverage = deque(), Coverage()

    def trim(self, now):
        while self.trades and self.trades[0].received < now - 3:
            self.trades.popleft()
        before = bisect_left(self.coverage, now - 3)
        if before:
            del self.coverage[:before]

    def volume(self, start, end, side, *, frontier=False, after_key=None):
        return sum(
            (
                t.quantity
                for t in self.trades
                if start <= t.received
                and (t.received <= end if frontier else t.received < end)
                and t.side == side
                and (after_key is None or (t.available, t.input_seq, t.subindex) > after_key)
            ),
            ZERO,
        )

    def covered(self, start, end):
        cursor = start
        while cursor < end - EPS:
            stop = min(end, cursor + 0.250)
            index = bisect_left(self.coverage, cursor)
            if index == len(self.coverage) or self.coverage[index] >= stop:
                return False
            cursor = stop
        return True


class Pressure:
    """AP in both directions; each AP owns a distinct symmetric EX observation."""

    def __init__(self, context, token):
        self.c, self.token = context, token
        self.attacks, self.active_ap, self.ex_watches, self.active_ex = {}, {}, {}, {}
        self.serial = 0

    @property
    def flow(self):
        return self.c.flows[self.token]

    def trade(self, trade):
        if trade.side not in self.attacks and not any(
            self.c.registry.valid(f, self.c.now) for f in self.active_ap.get(trade.side, ())
        ):
            depth = self.c.books[self.token].opposing_depth(trade.side)
            if depth <= 0:
                return
            self.serial += 1
            self.attacks[trade.side] = {
                "watch_id": f"ap-watch:{self.c.generation}:{self.token}:{self.c.seq}:{self.serial}",
                "side": trade.side,
                "start": trade.received,
                "depth": depth,
                "seq": self.c.seq,
                "start_key": self.c.cursor,
                "prints": [],
            }
            self.c.event(
                "AP_STARTED",
                {
                    "token": self.token,
                    **{k: v for k, v in self.attacks[trade.side].items() if k != "prints"},
                },
            )
        for a in self.attacks.values():
            if a["start"] <= trade.received < a["start"] + 0.750:
                a["prints"].append(trade)

    def close(self, watermark):
        c, now = self.c, self.c.now
        for side, a in list(self.attacks.items()):
            end = a["start"] + 0.750
            if end > watermark + EPS:
                if now > end + 0.050 + EPS:
                    c.unavailable(self.token, "AP_INPUT_BACKLOG")
                    return
                continue
            del self.attacks[side]
            own = [t for t in a["prints"] if t.side == side]
            volume = sum((t.quantity for t in own), ZERO)
            total = sum((t.quantity for t in a["prints"]), ZERO)
            minimum = max(D(1), a["depth"] * D(".0001"))
            occupied = {min(2, int((t.received - a["start"]) / 0.250 + EPS)) for t in own}
            valid = (
                volume >= minimum
                and total
                and volume / total >= D(".65")
                and len(own) >= 2
                and len(occupied) >= 2
                and 2 in occupied
                and max((t.quantity for t in own), default=ZERO) <= volume * D(".95")
            )
            quality = now <= end + 0.050 + EPS and self.flow.covered(a["start"], end)
            if not quality:
                c.unavailable(self.token, "AP_WINDOW_UNAVAILABLE")
                return
            if not valid:
                c.event(
                    "AP_NOT_CONFIRMED",
                    {
                        "token": self.token,
                        "watch_id": a["watch_id"],
                        "side": side,
                        "start": a["start"],
                        "end": end,
                        "volume": volume,
                    },
                )
                c.on_ap_verdict(self.token, a, None)
                continue
            opposite = "BUY" if side == "SELL" else "SELL"
            for f in self.active_ap.get(opposite, ()):
                c.registry.end(f, now, "REVERSE_AP")
            for f in self.active_ex.get(opposite, ()):
                c.registry.end(f, now, "REVERSE_AP")
            for key, w in list(self.ex_watches.items()):
                if w["side"] == opposite:
                    del self.ex_watches[key]
                    c.event("EX_FORMATION_ENDED", {"ap_id": key, "reason": "REVERSE_AP"})
            fact = c.fact(
                "AP_" + side,
                self.token,
                now + 0.750,
                a["start"],
                end,
                values={
                    "side": side,
                    "volume": volume,
                    "minimum": minimum,
                    "depth": a["depth"],
                    "start_key": a["start_key"],
                    "event_ids": [t.identity for t in a["prints"]],
                },
            )
            self.active_ap.setdefault(side, []).append(fact)
            self.ex_watches[fact.id] = {"ap": fact, "side": side, "start": now, "gate": c.cursor}
            c.on_ap_verdict(self.token, a, fact)
            c.on_fact(fact)
        for key, w in list(self.ex_watches.items()):
            end, ap = w["start"] + 0.750, w["ap"]
            if end > watermark + EPS:
                if now > end + 0.050 + EPS:
                    c.unavailable(self.token, "EX_INPUT_BACKLOG")
                    return
                continue
            del self.ex_watches[key]
            if now > end + 0.050 + EPS or not self.flow.covered(w["start"], end):
                c.unavailable(self.token, "EX_WINDOW_UNAVAILABLE")
                return
            values = [
                self.flow.volume(
                    w["start"] + i * 0.250,
                    w["start"] + (i + 1) * 0.250,
                    w["side"],
                    after_key=w["gate"],
                )
                for i in range(3)
            ]
            reference = ap.values["volume"]
            if all(v <= reference / 6 for v in values) and sum(values, ZERO) <= reference * D(
                ".25"
            ):
                fact = c.fact(
                    "EX_" + w["side"],
                    self.token,
                    now + 0.750,
                    w["start"],
                    end,
                    refs=[ap.id],
                    values={"volume": reference, "side": w["side"], "ap_confirmed": ap.confirmed},
                )
                self.active_ex.setdefault(w["side"], []).append(fact)
                c.on_fact(fact)
            else:
                c.event(
                    "EX_NOT_CONFIRMED",
                    {"token": self.token, "ap_id": ap.id, "subwindow_volumes": values},
                )

    def guard(self):
        c, now = self.c, self.c.now
        for side, facts in self.active_ap.items():
            for f in facts:
                if not c.registry.valid(f, now):
                    continue
                last = c.check_times.get(f.id, f.confirmed)
                if now - last < 0.250 - EPS:
                    continue
                c.check_times[f.id] = now
                if not self.flow.covered(now - 0.750, now):
                    c.unavailable(self.token, "AP_RECHECK_COVERAGE")
                    return
                own = self.flow.volume(now - 0.750, now, side, frontier=True)
                other = self.flow.volume(
                    now - 0.750, now, "BUY" if side == "SELL" else "SELL", frontier=True
                )
                if (
                    own + other == 0
                    or own / (own + other) < D(".60")
                    or own < f.values["minimum"] / 2
                    or self.flow.volume(now - 0.250, now, side, frontier=True) == 0
                ):
                    c.registry.end(f, now, "AP_PRESSURE_WEAKENED")
        for side, facts in self.active_ex.items():
            for f in facts:
                if c.registry.valid(f, now) and self.flow.volume(
                    now - 0.750, now, side, frontier=True
                ) >= f.values["volume"] * D(".50"):
                    c.registry.end(f, now, "EX_PRESSURE_RESUMED")
        for mapping in (self.active_ap, self.active_ex):
            for side, facts in mapping.items():
                mapping[side] = [f for f in facts if c.registry.valid(f, now)]


@dataclass
class Round:
    id: str
    token: str
    anchor: object
    start: float
    initial: object
    low: object
    current: object
    hit: object = ZERO
    hold_start: float | None = None
    held: bool = False
    qualified_at: float | None = None
    event_ids: list = field(default_factory=list)

    @property
    def minimum(self):
        return max(D(".5"), self.initial * D(".0001"))

    @property
    def qualified(self):
        return self.hit >= self.minimum and self.initial - self.low >= self.minimum

    @property
    def recovery(self):
        loss = self.initial - self.low
        return min(D(1), max(ZERO, self.current - self.low) / loss) if loss > 0 else ZERO


class Rounds:
    """An independent campaign observer. Another strategy cannot clear its rounds."""

    def __init__(
        self,
        context,
        token,
        scope,
        callback,
        *,
        gate=None,
        anchor=None,
        qualified_callback=None,
        progress_callback=None,
        lost_callback=None,
        unavailable_callback=None,
        window_seconds=.5,
        hold_seconds=.1,
        stable_recovery=".50",
        maximum_window_seconds=.5,
    ):
        self.c, self.token, self.scope, self.callback = context, token, scope, callback
        self.gate = gate or (float("-inf"), -1, -1)
        self.anchor, self.round, self.serial = anchor, None, 0
        self.qualified_callback = qualified_callback
        self.progress_callback, self.lost_callback = progress_callback, lost_callback
        self.unavailable_callback = unavailable_callback
        self.closed = False
        observers = getattr(context, "runtime_observers", None)
        if observers is not None:
            observers.add(self)
        if not 0 < hold_seconds < window_seconds <= maximum_window_seconds <= 1:
            raise ValueError("INVALID_ROUND_TIMING")
        self.window_seconds, self.hold_seconds = window_seconds, hold_seconds
        self.stable_recovery = D(str(stable_recovery))
        if not self.stable_recovery.is_finite() or not 0 < self.stable_recovery <= 1:
            raise ValueError("INVALID_STABLE_RECOVERY")

    def release_runtime(self):
        """Detach callbacks after the owning market's durable close only."""
        self.closed = True
        self.callback = self.qualified_callback = self.progress_callback = None
        self.lost_callback = self.unavailable_callback = None
        self.round = self.c = None

    def trade(self, trade):
        if self.closed or trade.side != "SELL":
            return
        if (trade.available, trade.input_seq, trade.subindex) <= self.gate:
            return
        book = self.c.books[self.token]
        if self.anchor is None:
            if book.bids.get(trade.price, ZERO) <= 0:
                return
            self.anchor = trade.price
        if trade.price != self.anchor or book.bids.get(self.anchor, ZERO) <= 0:
            return
        if self.round is None:
            self.serial += 1
            q = book.bids[self.anchor]
            self.round = Round(
                f"{self.scope}:{self.serial}", self.token, self.anchor, trade.received, q, q, q
            )
            self.c.event(
                "ROUND_STARTED",
                {
                    "id": self.round.id,
                    "token": self.token,
                    "scope": self.scope,
                    "start": trade.received,
                    "anchor": self.anchor,
                    "initial": q,
                },
            )
        r = self.round
        if r.start <= trade.received < r.start + self.window_seconds:
            r.hit += trade.quantity
            r.event_ids.append(trade.identity)
            self.progress(trade.received)

    def depth(self, received):
        if self.closed or self.anchor is None:
            return
        quantity = self.c.books[self.token].bids.get(self.anchor, ZERO)
        if quantity <= 0:
            self.closed = True
            if self.lost_callback:
                self.lost_callback("ANCHOR_LOST")
            return
        r = self.round
        if r is not None and received < r.start + self.window_seconds:
            r.current = quantity
            if quantity < r.low:
                r.low, r.hold_start, r.held = quantity, None, False
            self.progress(received)

    def progress(self, received):
        r, c = self.round, self.c
        if r is None:
            return
        if r.qualified:
            if r.qualified_at is None:
                r.qualified_at = c.now
                if self.qualified_callback:
                    self.qualified_callback(r)
            recovery = r.recovery
            if recovery < self.stable_recovery * D(".4"):
                r.held = False
            if recovery < self.stable_recovery * D(".8"):
                r.hold_start = None
            elif r.hold_start is None and recovery >= self.stable_recovery:
                r.hold_start = received
            until = min(c.watermark, received, r.start + self.window_seconds)
            if r.hold_start is not None and until - r.hold_start >= self.hold_seconds - EPS:
                r.held = True
        if self.progress_callback:
            self.progress_callback(r)

    def close(self, watermark):
        r, c = self.round, self.c
        if self.closed or r is None:
            return
        end = r.start + self.window_seconds
        if watermark < end - EPS:
            if c.now > end + 0.050 + EPS:
                self.unavailable("ROUND_INPUT_BACKLOG")
            return
        self.progress(end)
        self.round = None
        if c.now > end + 0.050 + EPS or not c.flows[self.token].covered(r.start, end):
            self.unavailable("ROUND_WINDOW_UNAVAILABLE")
            return
        values = {
            "scope": self.scope,
            "window_seconds": self.window_seconds,
            "hold_seconds": self.hold_seconds,
            "stable_recovery": str(self.stable_recovery),
            "initial": r.initial,
            "trough": r.low,
            "loss": r.initial - r.low,
            "hit": r.hit,
            "recovery": r.recovery,
            "u": r.hit / r.initial,
            "d": (r.initial - r.low) / r.initial,
            "qualified": r.qualified,
            "stable": r.qualified and r.held and r.recovery >= self.stable_recovery,
            "held": r.held,
            "hold_start": r.hold_start,
            "qualified_available_at": r.qualified_at,
            "event_ids": r.event_ids,
        }
        fact = c.fact("ROUND", self.token, c.now, r.start, end, anchor=r.anchor, values=values)
        self.callback(fact)

    def unavailable(self, reason):
        if self.unavailable_callback:
            self.unavailable_callback(reason)
        else:
            self.c.unavailable(self.token, reason)


class Refill:
    """Shared RF facts; positive replacements never invalidate previous fact IDs."""

    def __init__(self, context, token):
        self.c, self.token, self.rounds, self.facts = context, token, [], []
        self.observer = Rounds(context, token, "shared-rf", self.on_round, lost_callback=self.lost)

    def lost(self, reason):
        for f in self.facts:
            self.c.registry.end(f, self.c.now, reason)
        self.rounds.clear()

    def on_round(self, fact):
        self.c.on_round(fact)
        if not fact.values["qualified"]:
            return
        if not fact.values["stable"]:
            self.lost("QUALIFIED_REFILL_FAILED")
            return
        self.rounds.append(fact)
        if len(self.rounds) == 2:
            first, last = self.rounds
            if self.c.now - first.start <= 15 + EPS and median(
                f.values["recovery"] for f in self.rounds
            ) >= D(".50"):
                rf = self.c.fact(
                    "RF_BID",
                    self.token,
                    self.c.now + 8,
                    first.start,
                    last.end,
                    anchor=first.anchor,
                    refs=[f.id for f in self.rounds],
                    values={"rounds": tuple(self.rounds)},
                )
                self.facts.append(rf)
                self.c.on_fact(rf)
            self.rounds.clear()

    def trim(self):
        self.facts = [f for f in self.facts if self.c.registry.valid(f, self.c.now)]
        if self.observer.closed:
            self.observer = Rounds(
                self.c, self.token, "shared-rf", self.on_round, lost_callback=self.lost
            )
        if not self.facts and not self.rounds and self.observer.round is None:
            self.observer.anchor = None
        if self.rounds and self.c.now - self.rounds[0].start > 15:
            self.rounds.clear()
