"""Route ordered M4 and M21 formal signals into one M21-style market."""

from dataclasses import replace


class CombinedSignals:
    m21_only = True

    def __init__(self, m4, m21, settings):
        self.m4, self.m21, self.settings = m4, m21, settings
        self.price_risk = settings.live_taker_price_risk
        self.record = self.selected = None
        self._ordered = {}
        self._count = 0
        m4.record = self.record_m4
        m21.record = self.record_m21

    def __getattr__(self, name):
        return getattr(self.m21, name)

    @property
    def counts(self):
        # Heartbeat totals must describe both detectors. Counter addition keeps
        # their native counts, including diagnostics sampled out of the journal.
        return self.m4.counts + self.m21.counts

    def diagnostics(self):
        return {"strategy": self.settings.strategy, "failure": self.failure,
                "counts": dict(self.counts)}

    def record_m4(self, row):
        return self._receive("M4", self.m4, row)

    def record_m21(self, row):
        return self._receive("M21", self.m21, row)

    def _receive(self, source, detector, row):
        if row["event"] == "OPPORTUNITY_IDENTIFIED":
            key = row["opportunity"]["episode_id"]
            if key in self._ordered:
                return True
            original = next(o for o in detector.opportunities.values() if o.episode_id == key)
            deadline = min(original.order_deadline, original.signal_start_mono + 3)
            unified = replace(original, order_deadline=deadline, price_risk=self.price_risk, taker_strategy=self.settings.taker_strategy,
                              market_id=self.m21.feed.meta.condition_id)
            self._count += 1
            self._ordered[key] = (detector, original, unified, self._count, source)
            row = {**row, "m421_sequence": self._count}
        return self.record({**row, "detector_source": source}) if self.record is not None else True

    def provenance(self, key):
        return self._ordered[key][3:]

    def active_reason(self, claim, snapshot=None):
        item = self._ordered.get(claim[0])
        if item is None:
            return "M421_EVIDENCE_ENDED"
        detector, original, *_ = item
        return detector.active_reason((original.episode_id,), snapshot)

    def evaluate(self, snapshot=None):
        self.tick()
        self.selected = None
        for key in tuple(self._ordered):
            if self.active_reason((key,), snapshot):
                self._ordered.pop(key)
                continue
            detector, original, unified, *_ = self._ordered[key]
            if self.selected is None and original.root_id not in detector.used_roots:
                self.selected = unified

    def take(self, opportunity):
        detector, original, *_ = self._ordered[opportunity.episode_id]
        return detector.take(original)
