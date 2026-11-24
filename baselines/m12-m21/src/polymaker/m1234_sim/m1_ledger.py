"""M1 simulation sizing, including the frozen legacy one-USDC baseline."""

from polymaker.m1_live_state import M1Ledger
from polymaker.rdp_sm_m1_policy import budget_quantity


class M1SimulationLedger(M1Ledger):
    @property
    def sizing_policy(self):
        if self.order_budget == 1:
            return super().sizing_policy
        return "BUDGET_FLOOR_001_MAX5_V1"

    def order_quantity(self, price, minimum):
        if self.order_budget == 1:
            return super().order_quantity(price, minimum)
        return budget_quantity(price, minimum, self.order_budget)
