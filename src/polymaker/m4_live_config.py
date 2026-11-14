"""Live-only execution choices; the running baseline never loads this file."""
import hashlib
import json
import tomllib
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from polymaker.execution.taker_fees import buy_amount_fee_reserve, fee_parameters
from polymaker.m4_config import M4Config
from polymaker.m4_frequency_signal import PARAMETERS


class TakerSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    order_type: Literal['FAK'] = 'FAK'
    max_anchor_slippage_ticks: int = Field(default=1, ge=1, le=1, strict=True)
    budget_policy: Literal['LIMIT_PRICE_FEE_PLAN_WITH_GROSS_RESERVE_V1'] = 'LIMIT_PRICE_FEE_PLAN_WITH_GROSS_RESERVE_V1'
    pricing_policy: Literal['ANCHOR_FIXED_V1', 'ANCHOR_10S_THEN_BEST_ASK_V1'] = 'ANCHOR_FIXED_V1'
    anchor_wait_sec: Literal[10] = 10

    @model_serializer(mode='wrap')
    def historical_serialization(self, handler):
        value = handler(self)
        # Preserve the exact saved settings and fingerprint of every old market.
        if self.pricing_policy == 'ANCHOR_FIXED_V1':
            value.pop('pricing_policy')
            value.pop('anchor_wait_sec')
        return value


class LiveExecutionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    mode: Literal['maker', 'taker'] = 'maker'
    taker: TakerSettings = Field(default_factory=TakerSettings)


class M4LiveSettings(M4Config):
    execution_revision: Literal['m4-maker-v1', 'm4-taker-single-fak-v1', 'm4-taker-task-v1', 'm4-taker-budget-v1'] = 'm4-maker-v1'
    execution: LiveExecutionSettings = Field(default_factory=LiveExecutionSettings)

    @model_validator(mode='after')
    def execution_identity(self):
        allowed = {'m4-taker-single-fak-v1', 'm4-taker-task-v1', 'm4-taker-budget-v1'} if self.execution.mode == 'taker' else {'m4-maker-v1'}
        if self.execution_revision not in allowed:
            raise ValueError('M4_LIVE_EXECUTION_REVISION_CONFLICT')
        if (self.execution.taker.pricing_policy != 'ANCHOR_FIXED_V1'
                and self.execution_revision != 'm4-taker-budget-v1'):
            raise ValueError('M4_DYNAMIC_PRICING_REQUIRES_BUDGET_TASK')
        return self

    @property
    def fingerprint(self):
        # Historical Maker settings/identity remain byte-for-byte compatible.
        value = self.model_dump(mode='json')
        if self.execution.mode == 'maker':
            value.pop('execution')
        return hashlib.sha256(json.dumps({'settings': value, 'signal': PARAMETERS},
                                        sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, project):
        base = M4Config.load(project).model_dump(mode='json')
        path = Path(project)/'config/m4-live/execution.toml'
        execution = LiveExecutionSettings.model_validate(tomllib.loads(path.read_text())
                    if path.exists() else {})
        base['execution_revision'] = ('m4-taker-budget-v1'
                                     if execution.mode == 'taker' else 'm4-maker-v1')
        return cls.model_validate({**base, 'execution': execution})


def validated_fees(raw, tokens):
    """Reuse the CA market-info parser, including its token identity checks."""
    return fee_parameters(raw, tokens)


def planned_fee(price, quantity, fees):
    """Published formula at the limit price; round upward to fee precision.

    This is a sizing estimate, not an actual-debit cap after price improvement
    or independently rounded matches. No rebate reduces the planned fee.
    """
    price, quantity = Decimal(str(price)), Decimal(str(quantity))
    return (quantity*Decimal(fees['rate'])*(price*(1-price))**fees['exponent']
            ).quantize(Decimal('.00001'), rounding=ROUND_CEILING)


def all_in_cost(price, quantity, fees):
    """Planned cost at the signed limit; actual debit can exceed this estimate."""
    return Decimal(str(price))*Decimal(str(quantity))+planned_fee(price, quantity, fees)


def gross_risk_cost(price, quantity, fees):
    """Keep the unchanged market/cash cap safe despite the per-order waiver."""
    principal = Decimal(str(price))*Decimal(str(quantity))
    return principal+buy_amount_fee_reserve(principal, fees)


def taker_quantity(price, minimum, fees):
    price, minimum = Decimal(str(price)), Decimal(str(minimum))
    if not price.is_finite() or not 0 < price < 1 or not minimum.is_finite() or minimum <= 0:
        raise ValueError('M4_TAKER_QUOTE_INVALID')
    rate = Decimal(fees['rate'])
    if not rate.is_finite() or not 0 <= rate < 1 or not 1 <= fees['exponent'] <= 5:
        raise ValueError('M4_TAKER_FEE_PARAMETERS_INVALID')
    unit_cost = price+rate*(price*(1-price))**fees['exponent']
    quantity = (Decimal(5)/unit_cost/Decimal('.1')
                ).to_integral_value(rounding=ROUND_FLOOR)*Decimal('.1')
    # Fee rounding may consume the final fraction of the remaining budget.
    while quantity > 0 and all_in_cost(price, quantity, fees) > 5:
        quantity -= Decimal('.1')
    if quantity < max(Decimal(5), minimum) or all_in_cost(price, quantity, fees) > 5:
        raise ValueError('M4_TAKER_MINIMUM_EXCEEDS_ALL_IN_BUDGET')
    return quantity
