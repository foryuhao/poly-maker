"""Attribute existing PM position economics to complete BUY-only fill histories."""

from collections import defaultdict
from decimal import Decimal

from polymaker.dashboard.model import number


def fill_pnls(session):
    account = session.get("pm_account") or {}
    trades = account.get("trades") or []
    result = {t["id"]: {"pnl": None, "pending": "待结算"} for t in trades}
    if number(account.get("settled_pnl")) is None:
        return result
    groups = defaultdict(list)
    for trade in trades:
        groups[trade["token_id"]].append(trade)
    positions = defaultdict(list)
    for position in account.get("positions", []):
        positions[position["token_id"]].append(position)
    proof = account.get("resolution") or {}
    tokens = proof.get("token_ids") or {}
    final = (proof.get("closed") is True and proof.get("resolution_status") == "resolved"
             and proof.get("winner") in {"YES", "NO"}
             and proof.get("slug") == session.get("slug")
             and proof.get("condition_id", "").lower() == str(session.get("condition_id", "")).lower()
             and set(tokens) == {"YES", "NO"} and len(set(tokens.values())) == 2)
    winner_token = tokens.get(proof.get("winner")) if final else None
    for token, fills in groups.items():
        for trade in fills:
            result[trade["id"]]["pending"] = "待核对"
        if len(positions[token]) != 1 or any(t.get("side") != "BUY" for t in fills):
            continue
        position = positions[token][0]
        qty, value = number(position.get("current_size")), number(position.get("current_value"))
        total_size, total_pnl = number(position.get("total_size")), number(position.get("total_pnl"))
        amounts = [number(t.get("amount")) for t in fills]
        sizes = [number(t.get("quantity")) for t in fills]
        if (any(v is None for v in (qty, value, total_size, total_pnl, *amounts, *sizes))
                or any(q <= 0 for q in sizes) or any(a < 0 for a in amounts)
                or qty < 0 or value < 0 or total_size < 0):
            continue
        # PM aggregate economics and quantities can be rounded more coarsely
        # than individual activity rows. Permit only a bounded rounding gap;
        # never change a fill's PnL to force agreement with the aggregate.
        tolerance = min(Decimal(".001"), Decimal(".0001")*(len(fills)+1))
        if abs(sum(sizes, Decimal(0))-total_size) > tolerance:
            continue
        if winner_token and token in (proof.get("token_ids") or {}).values():
            payout = Decimal(int(token == winner_token))
            official = total_pnl-value+qty*payout
        elif qty == 0 and value == 0 and position.get("status") == "CLOSED":
            official, payout = total_pnl, None
        else:
            continue
        fees = number(position.get("entry_fees_usdc"))
        if payout is not None and fees is not None and fees >= 0 and (len(fills) == 1 or fees == 0):
            values = [q*payout-a for q, a in zip(sizes, amounts, strict=True)]
            if len(fills) == 1:
                values[0] -= fees
        elif payout is None and len(fills) == 1 and -amounts[0]-tolerance <= official <= sizes[0]-amounts[0]+tolerance:
            # A closed position with exactly one BUY needs no arbitrary
            # allocation or inferred winning side. Use its official net PnL.
            values = [official]
        else:
            for trade in fills:
                result[trade["id"]]["pending"] = "未分摊"
            continue
        if abs(sum(values, Decimal(0))-official) > tolerance:
            continue
        for trade, pnl in zip(fills, values, strict=True):
            result[trade["id"]] = {"pnl": str(pnl), "pending": None}
    return result
