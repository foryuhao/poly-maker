"""Two independent original feed views over one raw ordered market stream."""

from __future__ import annotations

from decimal import Decimal

from polymaker.config import RDPM1Config, RDPObiConfig
from polymaker.m1_live_signal import M1LiveSignals
from polymaker.m234_signal import M234Feed
from polymaker.rdp_sm_m1_signal import M1Signals
from polymaker.rdp_sm_marketdata import SessionFeed


class Signals:
    def __init__(self, meta, clock, m1_settings, m234_settings, emit):
        self.emit, self.clock = emit, clock
        common = {
            "monotonic": clock.monotonic,
            "wall": clock.time,
            "normal_entry_mode": "m1_v1",
            "m1_config": RDPM1Config(record_events=False),
        }
        self.m1 = SessionFeed(meta, RDPObiConfig(), **common)
        self.m1.m1 = M1LiveSignals(
            self.m1,
            signal_config=m1_settings.signal,
            out_of_order_tolerance_ms=m1_settings.out_of_order_tolerance_ms,
            record=lambda r: self.record("M1", r),
        )
        self.m1.m1.namespace = "sim-m1"
        self.m234 = M234Feed(meta, RDPObiConfig(), research_settings=m234_settings, **common)
        self.m234.m1.namespace = "sim-m2"
        self.m234.hub.legacy_record = lambda r: self.m234.hub.emit("M2", r["event"], r)
        original_pair = self.m234.hub.m4.pair
        self.m4_namespace_counter = 0

        def pair(token):
            created = token not in self.m234.hub.m4.queues
            result = original_pair(token)
            if created:
                self.m4_namespace_counter += 1
                for side, observer in result.items():
                    observer.namespace = f"sim-m4-{self.m4_namespace_counter}-{side}"
            return result

        self.m234.hub.m4.pair = pair
        self.views = {"M1": self.m1, "M234": self.m234}

    def record(self, strategy, row):
        if row["event"] != "EVENT_DECISION":
            self.emit("SIGNAL", {"strategy": strategy, "event": row["event"], "payload": row})
        return True

    def connect(self):
        for feed in self.views.values():
            feed.connect()
        self.drain()

    def ticks(self, ticks):
        # Validate both views before changing either: a conflict must not leave
        # a partially verified profile or token behind.
        if set(ticks) != set(self.m1.books):
            raise ValueError("SIM_OFFICIAL_TICK_TOKEN_IDENTITY")
        for token, value in ticks.items():
            tick = Decimal(str(value))
            if not tick.is_finite() or not 0 < tick < 1:
                raise ValueError("SIM_INVALID_OFFICIAL_TICK")
            for feed in self.views.values():
                book = feed.books[token]
                if book.snapshot_generation and tick != book.tick:
                    raise ValueError("WS_OFFICIAL_TICK_CONFLICT")
        for feed in self.views.values():
            feed.verify_ticks(ticks)

    def gap(self, reason):
        for feed in self.views.values():
            feed.invalidate(reason)
        self.drain()

    def apply(self, raw, received, wall, pending=()):
        errors = []
        for name, feed in self.views.items():
            feed.m1_pending_received.clear()
            feed.m1_pending_received.extend(pending)
            try:
                feed.apply(raw, received_mono=received, received_wall=wall)
            except Exception as exc:
                errors.append((name, type(exc).__name__, str(exc)))
        self.drain()
        if errors:
            raise ValueError("SIM_OBSERVER_FRAME_FAILURE:" + str(errors))

    def tick(self):
        self.m1.m1.tick()
        self.m1.m1.evaluate(self.m1.snapshot())
        self.m234.m1.tick()
        self.m234.hub.observe_new(
            lambda now: {
                "elapsed_lower": self.clock.wall - int(self.m1.meta.slug.rsplit("-", 1)[1]),
                "elapsed_upper": self.clock.wall - int(self.m1.meta.slug.rsplit("-", 1)[1]),
                "clock_fresh": True,
                "planned_shutdown": False,
            }
        )
        self.drain()
        for name, feed in self.views.items():
            if feed.m1.failure:
                raise RuntimeError("SIM_OBSERVER_FAILED:" + name + ":" + feed.m1.failure)
        if self.m234.hub.failures:
            raise RuntimeError("SIM_OBSERVER_FAILED:" + str(self.m234.hub.failures))

    def drain(self):
        hub = self.m234.hub
        for strategy in ("M2", "M3", "M4"):
            while hub.events[strategy]:
                row = hub.events[strategy].popleft()
                self.emit("SIGNAL", row)


class SignalView:
    """Shared detector/evidence; independent M1Market consumption per account.

    The original evaluate/active_reason implementations execute on this view.
    Mutable signal evidence, IDs and timers belong to the one base detector;
    used RF/EX, selection and own-active admission flags belong to each profile.
    """

    def __init__(self, base):
        self.base = base
        self.used_rf, self.used_ex = set(), set()
        self.selected = self.episode_id = self.episode_start = None
        self.own_active = False

    def __getattr__(self, name):
        return getattr(self.base, name)

    evaluate = M1Signals.evaluate
    active_reason = M1Signals.active_reason
    take = M1LiveSignals.take


class FeedView:
    def __init__(self, base):
        self.base = base
        self.m1 = SignalView(base.m1)

    def __getattr__(self, name):
        return getattr(self.base, name)
