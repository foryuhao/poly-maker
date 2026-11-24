"""One cooldown per completed round, from actual FIFO Maker BUY pair costs."""
from __future__ import annotations

from decimal import Decimal

from polymaker.domain import Side
from polymaker.rdp_sm_sl_ledger import fifo_paired_quantities
from polymaker.state.tracker import fill_unit_cost

ZERO = Decimal(0)


def pair_snapshot(fills, stamps, yes_token, no_token):
    """Keep paired allocations and actual prices; never consult limit prices."""
    facts = {}
    for fill in fills:
        key = (fill.trade_id, fill.maker_order_id)
        price, size = fill_unit_cost(fill), Decimal(str(fill.size))
        if (not all(key) or key in facts or fill.our_side is not Side.BUY
                or fill.token_id not in {yes_token, no_token}
                or not price.is_finite() or price <= ZERO
                or not size.is_finite() or size <= 0):
            raise ValueError("COOLDOWN_FILL_NOT_VERIFIED")
        facts[key] = (fill.token_id, price, size)
    paired = fifo_paired_quantities(fills, stamps, yes_token)
    return {key: (*fact, paired.get(key, ZERO)) for key, fact in facts.items()}


class Cooldown:
    def __init__(self, cfg):
        self.cfg = cfg
        self.round_id = None
        self.baseline = None
        self.deadline = None
        self.last_result = None
        self.resume_block_reason = None

    @property
    def active(self):
        # Expiry alone is not permission to trade. The coordinator must also
        # establish the existing account, book, network and time conditions.
        return self.deadline is not None

    def begin(self, round_id, snapshot):
        if self.active or self.round_id is not None:
            raise ValueError("COOLDOWN_ROUND_ALREADY_ACTIVE")
        self.round_id, self.baseline = round_id, snapshot

    def complete(self, snapshot, yes_token, now):
        if self.round_id is None:
            raise ValueError("COOLDOWN_ROUND_NOT_ESTABLISHED")
        quantities, costs = {True: ZERO, False: ZERO}, {True: ZERO, False: ZERO}
        for key, old in self.baseline.items():
            current = snapshot.get(key)
            if current is None or current[:3] != old[:3] or current[3] < old[3]:
                raise ValueError("COOLDOWN_FIFO_EVIDENCE_CHANGED")
        for key, (token, price, _, paired) in snapshot.items():
            old_paired = self.baseline[key][3] if key in self.baseline else ZERO
            added = paired-old_paired
            yes = token == yes_token
            quantities[yes] += added
            costs[yes] += added*price
        if quantities[True] != quantities[False]:
            raise ValueError("COOLDOWN_PAIR_QUANTITIES_UNPROVEN")
        shares = quantities[True]
        pnl = shares-costs[True]-costs[False]
        per_share = pnl/shares if shares else ZERO
        seconds = min(Decimal(str(self.cfg.max_sec)), max(ZERO,
            -per_share*Decimal(str(self.cfg.loss_sec_per_dollar))))
        result = {"round_id": self.round_id, "paired_shares": str(shares),
                  "paired_yes_cost": str(costs[True]), "paired_no_cost": str(costs[False]),
                  "pair_pnl": str(pnl), "pair_pnl_per_share": str(per_share),
                  "cooldown_sec": float(seconds), "started_monotonic": now,
                  "deadline_monotonic": now+float(seconds) if seconds else None,
                  "state": "COOLDOWN" if seconds else "NORMAL"}
        self.deadline = result["deadline_monotonic"]
        self.last_result = result
        self.resume_block_reason = None
        self.round_id = self.baseline = None
        return result
