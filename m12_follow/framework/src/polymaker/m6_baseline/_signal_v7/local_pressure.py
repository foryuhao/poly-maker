"""Strategy-owned AP calibration; never changes the shared public feed's quality."""

from .model import EPS, ZERO, D


class LocalPressure:
    def __init__(self, manager, strategy, token, window):
        self.m, self.c, self.strategy, self.token = manager, manager.c, strategy, token
        self.window = window
        self.attacks, self.active_ap = {}, {}
        self.serial = 0

    def trade(self, trade):
        c, side = self.c, trade.side
        if (trade.token != self.token or side not in ("BUY", "SELL")
                or not c.good[self.token] or not c.legal(self.token)):
            return
        if side not in self.attacks and not any(
                c.registry.valid(f, c.now) for f in self.active_ap.get(side, ())):
            depth = c.books[self.token].opposing_depth(side)
            if depth <= 0:
                return
            self.serial += 1
            self.attacks[side] = {
                "watch_id": f"local-ap:{self.strategy}:{c.generation}:{self.token}:{c.seq}:{self.serial}",
                "side": side, "start": trade.received, "depth": depth,
                "start_key": (trade.available, trade.input_seq, trade.subindex), "prints": [],
            }
        for watch in self.attacks.values():
            if watch["start"] <= trade.received < watch["start"] + self.window:
                watch["prints"].append(trade)

    def reset(self, reason, reason_class="UNKNOWN"):
        for facts in self.active_ap.values():
            for fact in facts:
                self.c.registry.end(fact, self.c.now, reason, reason_class)
        self.attacks.clear()
        self.active_ap.clear()

    def end_active(self, side, reason):
        for fact in self.active_ap.get(side, ()):
            self.c.registry.end(fact, self.c.now, reason)
        self.active_ap[side] = []

    def unavailable(self, reason):
        self.reset(reason)
        campaign = self.m.active(self.strategy, self.token)
        if campaign:
            campaign.end(reason, "UNKNOWN")
        self.c.event("LOCAL_AP_UNAVAILABLE", {
            "strategy": self.strategy, "token": self.token, "reason": reason,
            "window_seconds": self.window})

    def close(self):
        c, now = self.c, self.c.now
        for side in tuple(self.active_ap):
            self.active_ap[side] = [f for f in self.active_ap[side] if c.registry.valid(f, now)]
        for side, watch in tuple(self.attacks.items()):
            end = watch["start"] + self.window
            if end > c.watermark + EPS:
                if now > end + .050 + EPS:
                    self.unavailable("LOCAL_AP_INPUT_BACKLOG")
                    return
                continue
            del self.attacks[side]
            if (now > end + .050 + EPS or not c.good[self.token] or not c.legal(self.token)
                    or not c.flows[self.token].covered(watch["start"], end)):
                self.unavailable("LOCAL_AP_WINDOW_UNAVAILABLE")
                return
            own = [t for t in watch["prints"] if t.side == side]
            volume = sum((t.quantity for t in own), ZERO)
            total = sum((t.quantity for t in watch["prints"]), ZERO)
            minimum = max(D(1), watch["depth"] * D(".0001"))
            occupied = {min(2, int((t.received-watch["start"]) / (self.window/3) + EPS)) for t in own}
            valid = bool(volume >= minimum and total and volume/total >= D(".65")
                         and len(own) >= 2 and len(occupied) >= 2 and 2 in occupied
                         and max((t.quantity for t in own), default=ZERO) <= volume * D(".95"))
            if not valid:
                self.c.event("LOCAL_AP_NOT_CONFIRMED", {
                    "strategy": self.strategy, "token": self.token, "watch_id": watch["watch_id"],
                    "side": side, "start": watch["start"], "end": end,
                    "volume": volume, "window_seconds": self.window})
                continue
            opposite = "SELL" if side == "BUY" else "BUY"
            self.end_active(opposite, "REVERSE_AP")
            fact = c.fact(
                f"AP_{side}_{self.strategy}_PRIVATE", self.token, now + .750, watch["start"], end,
                values={"side": side, "volume": volume, "minimum": minimum,
                        "depth": watch["depth"], "start_key": watch["start_key"],
                        "event_ids": [t.identity for t in watch["prints"]],
                        "strategy_scope": self.strategy, "window_seconds": self.window})
            self.active_ap[side] = [fact]
            self.m.on_private_ap(self.strategy, self.token, watch, fact)

    def release_runtime(self):
        self.attacks.clear()
        self.active_ap.clear()
        self.m = self.c = None
