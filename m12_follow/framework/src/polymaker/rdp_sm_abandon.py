"""High confirmed NORMAL entry cost: stop this market's remaining repair."""
from decimal import Decimal

ZERO = Decimal(0)
REASON = "HIGH_ENTRY_REPAIR_ABANDONED"


def eligible(cfg, elapsed, entry_vwap):
    return (cfg.enabled and elapsed >= cfg.min_elapsed_sec
            and entry_vwap is not None and entry_vwap >= cfg.entry_price_threshold)


def fifo_attribution(snapshot, baseline, yes_token):
    """New completed FIFO pairs and retained actual costs; no terminal-price guess."""
    quantities, costs = {True: ZERO, False: ZERO}, {True: ZERO, False: ZERO}
    for key, old in baseline.items():
        current = snapshot.get(key)
        if current is None or current[:3] != old[:3] or current[3] < old[3]:
            raise ValueError("ABANDON_FIFO_EVIDENCE_CHANGED")
    for key, (token, price, _, paired) in snapshot.items():
        added = paired-(baseline[key][3] if key in baseline else ZERO)
        side = token == yes_token
        quantities[side] += added
        costs[side] += added*price
    if quantities[True] != quantities[False]:
        raise ValueError("ABANDON_PAIR_QUANTITIES_UNPROVEN")
    residual = {side: sum(((size-paired)*price for token, price, size, paired in snapshot.values()
                          if (token == yes_token) == side), ZERO) for side in (True, False)}
    return {"round_paired_shares": str(quantities[True]),
            "round_paired_yes_cost": str(costs[True]), "round_paired_no_cost": str(costs[False]),
            "round_pair_pnl": str(quantities[True]-sum(costs.values(), ZERO)),
            "session_residual_yes_cost": str(residual[True]),
            "session_residual_no_cost": str(residual[False]),
            "residual_settlement_pnl": "PENDING_SETTLEMENT", "maker_rebate_included": False}
