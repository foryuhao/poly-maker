"""Concurrent M1 claims over the actual public book, including our orders."""

import uuid

from polymaker.rdp_sm_m1_signal import M1Signals


class M1LiveSignals(M1Signals):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.namespace = uuid.uuid4().hex

    def new_id(self, kind):
        return f"{self.namespace}:{super().new_id(kind)}"

    def take(self, opportunity):
        """Consume independent RF/EX once without pausing unrelated detection."""
        with self.feed.lock:
            if self.active_reason((opportunity.episode_id,), None) is not None:
                return False
            if opportunity.rf_id in self.used_rf or opportunity.ex_id in self.used_ex:
                return False
            self.used_rf.add(opportunity.rf_id)
            self.used_ex.add(opportunity.ex_id)
            self.emit("OPPORTUNITY_CONSUMED", opportunity=vars(opportunity))
            return True
