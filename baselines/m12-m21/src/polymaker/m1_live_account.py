"""M1 cash admission only; no order, trade or position reconciliation."""

import asyncio

from polymaker.m1_live_state import decimal
from polymaker.rdp_sm import _require


async def account_snapshot(reads, meta):
    gateway = reads.gateway
    values = await asyncio.gather(gateway.balance_allowance(),
        gateway.required_collateral_spenders(meta), return_exceptions=True)
    for value in values:
        if isinstance(value, BaseException):
            raise value
    raw, spenders = values
    _require(bool(spenders), "APPLICABLE_SPENDER_UNKNOWN")
    _require(isinstance(raw, dict) and isinstance(raw.get("allowances"), dict), "CAPITAL_READ_UNKNOWN")
    keys = [str(k).lower() for k in raw["allowances"]]
    _require(len(keys) == len(set(keys)), "ALLOWANCE_ALIASES_CONFLICT")
    allowances = {str(k).lower(): v for k, v in raw["allowances"].items()}
    amounts = [decimal(raw.get("balance"))] + [decimal(allowances.get(s.lower())) for s in spenders]
    _require(all(n >= 0 and n == n.to_integral_value() for n in amounts), "CAPITAL_MICRO_UNITS_INVALID")
    return {"official_balance_usd": str(amounts[0]/1_000_000),
            "applicable_allowance_usd": str(min(amounts[1:])/1_000_000),
            "source": "CLOB_BALANCE_ALLOWANCE_ONLY"}
