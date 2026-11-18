"""Live-only execution choices; the running baseline never loads this file."""
import hashlib
import json
import tomllib
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from polymaker.execution.taker_fees import buy_amount_fee_reserve, fee_parameters
from polymaker.m4_config import LIVE_TAKER_IDENTITY_KEY, LIVE_TAKER_PRICE_RISK_KEY, M4Config, read_strategy_config
from polymaker.m4_frequency_signal import PARAMETERS
from polymaker.m4_live_price_band import (
    ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND, ANCHOR_PRICE_FLOOR,
    POST_SIGNED_LIMIT_BAND, WAIT_FOR_PRICE_BAND, configured_band,
)
from polymaker.m4_types import BEST_ASK_SIGNAL, M6_LEDGER, WAIT_SIGNAL_BAND, TakerPriceRisk, TakerStrategy


class TakerSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    order_type: Literal['FAK'] = 'FAK'
    max_anchor_slippage_ticks: int = Field(default=1, ge=1, le=1, strict=True)
    budget_policy: Literal['LIMIT_PRICE_FEE_PLAN_WITH_GROSS_RESERVE_V1'] = 'LIMIT_PRICE_FEE_PLAN_WITH_GROSS_RESERVE_V1'
    pricing_policy: Literal['ANCHOR_FIXED_V1', 'ANCHOR_10S_THEN_BEST_ASK_V1',
                            'FRESH_BEST_ASK_UNTIL_SIGNAL_DEADLINE_V1'] = 'ANCHOR_FIXED_V1'
    anchor_wait_sec: Literal[10] = 10
    price_band_policy: Literal['ANCHOR_PRICE_FLOOR_V1', 'POST_SIGNED_LIMIT_BAND_V1'] = ANCHOR_PRICE_FLOOR
    post_minimum_price: Decimal = Field(default=Decimal('.50'), ge=0, lt=1)
    post_maximum_price: Decimal = Field(default=Decimal('.95'), gt=0, le=1)
    post_price_band_retry_policy: Literal[
        'WAIT_UNTIL_T240_V1', 'ABANDON_ON_BEST_ASK_BAND_REJECTION_V1',
        'ABANDON_ON_FIRST_SIGNED_LIMIT_BAND_REJECTION_V1'
    ] = WAIT_FOR_PRICE_BAND

    @model_validator(mode='after')
    def price_band(self):
        if self.post_minimum_price >= self.post_maximum_price:
            raise ValueError('M4_POST_PRICE_BAND_INVALID')
        if (self.price_band_policy == ANCHOR_PRICE_FLOOR
                and (self.post_minimum_price != Decimal('.50')
                     or self.post_maximum_price != Decimal('.95'))):
            raise ValueError('M4_POST_PRICE_BAND_POLICY_REQUIRED')
        if (self.post_price_band_retry_policy in {ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND}
                and (self.price_band_policy != POST_SIGNED_LIMIT_BAND
                     or self.pricing_policy != 'ANCHOR_10S_THEN_BEST_ASK_V1')):
            raise ValueError('M4_BAND_ABANDON_REQUIRES_DYNAMIC_POST_BAND')
        return self

    @model_serializer(mode='wrap')
    def historical_serialization(self, handler):
        value = handler(self)
        if self.post_price_band_retry_policy == WAIT_FOR_PRICE_BAND:
            # Preserve both pre-band and already-running band-market identities.
            value.pop('post_price_band_retry_policy')
        # Preserve the exact saved settings and fingerprint of every old market.
        if self.pricing_policy == 'ANCHOR_FIXED_V1':
            value.pop('pricing_policy')
            value.pop('anchor_wait_sec')
        if self.price_band_policy == ANCHOR_PRICE_FLOOR:
            # Old saved settings/fingerprints must not acquire default policy fields.
            for key in ('price_band_policy', 'post_minimum_price', 'post_maximum_price'):
                value.pop(key)
        return value


class LiveExecutionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    mode: Literal['maker', 'taker'] = 'maker'
    taker: TakerSettings = Field(default_factory=TakerSettings)


class M4LiveSettings(M4Config):
    strategy: Literal['m4-frequency-v2', 'm6-withdrawal-supported-v1.1'] = 'm4-frequency-v2'
    execution_revision: Literal['m4-maker-v1', 'm4-taker-single-fak-v1', 'm4-taker-task-v1', 'm4-taker-budget-v1'] = 'm4-maker-v1'
    execution: LiveExecutionSettings = Field(default_factory=LiveExecutionSettings)
    live_taker_price_risk: TakerPriceRisk | None = None
    taker_strategy: TakerStrategy | None = None
    order_budget_usdc: int | Decimal = 5
    session_budget_usdc: int | Decimal = 10

    @model_validator(mode='after')
    def execution_identity(self):
        allowed = {'m4-taker-single-fak-v1', 'm4-taker-task-v1', 'm4-taker-budget-v1'} if self.execution.mode == 'taker' else {'m4-maker-v1'}
        if self.execution_revision not in allowed:
            raise ValueError('M4_LIVE_EXECUTION_REVISION_CONFLICT')
        if (self.execution.taker.pricing_policy != 'ANCHOR_FIXED_V1'
                and self.execution_revision != 'm4-taker-budget-v1'):
            raise ValueError('M4_DYNAMIC_PRICING_REQUIRES_BUDGET_TASK')
        if (self.post_price_band is not None
                and self.execution_revision != 'm4-taker-budget-v1'):
            raise ValueError('M4_POST_PRICE_BAND_REQUIRES_BUDGET_TAKER')
        if self.live_taker_price_risk is not None:
            if self.execution.taker.model_fields_set & {
                'price_band_policy', 'post_minimum_price', 'post_maximum_price', 'post_price_band_retry_policy'
            }:
                raise ValueError('M4_PRICE_RISK_CONFLICTING_SOURCES')
            if self.execution.taker.pricing_policy not in {'ANCHOR_10S_THEN_BEST_ASK_V1', BEST_ASK_SIGNAL}:
                raise ValueError('M4_BAND_ABANDON_REQUIRES_DYNAMIC_POST_BAND')
        m6 = self.taker_strategy is not None and self.taker_strategy.ledger == M6_LEDGER
        if (m6 != (self.strategy == 'm6-withdrawal-supported-v1.1')
                or m6 != (self.execution.taker.pricing_policy == BEST_ASK_SIGNAL)
                or m6 != (self.post_price_band_retry_policy == WAIT_SIGNAL_BAND)):
            raise ValueError('TAKER_STRATEGY_PRICING_CONFLICT')
        if self.taker_strategy is not None and (
                self.execution_revision != 'm4-taker-budget-v1' or self.live_taker_price_risk is None
                or self.taker_strategy.version != self.strategy
                or self.taker_strategy.signal_budget_usdc != self.order_budget_usdc
                or self.taker_strategy.market_budget_usdc != self.session_budget_usdc
                or self.taker_strategy.direction_limit != self.max_opportunities_per_direction):
            raise ValueError('TAKER_STRATEGY_SETTINGS_CONFLICT')
        if not m6 and (self.order_budget_usdc, self.session_budget_usdc) != (5, 10):
            raise ValueError('M4_RISK_LIMIT_INVALID')
        return self

    @model_serializer(mode='wrap')
    def historical_serialization(self, handler):
        value = handler(self)
        if self.live_taker_price_risk is None:
            value.pop(LIVE_TAKER_PRICE_RISK_KEY)
        if self.taker_strategy is None:
            value.pop('taker_strategy')
        return value

    @property
    def post_price_band(self):
        if self.live_taker_price_risk is not None:
            return self.live_taker_price_risk.plan_fields()["post_price_band"]
        return configured_band(self.execution.taker)

    @property
    def post_price_band_retry_policy(self):
        return (self.live_taker_price_risk.retry_policy if self.live_taker_price_risk is not None
                else self.execution.taker.post_price_band_retry_policy)

    @property
    def fingerprint(self):
        # Historical Maker settings/identity remain byte-for-byte compatible.
        value = self.model_dump(mode='json')
        if self.execution.mode == 'maker':
            value.pop('execution')
        signal = (self.taker_strategy.config_fingerprint if self.taker_strategy
                  and self.taker_strategy.ledger == M6_LEDGER else PARAMETERS)
        return hashlib.sha256(json.dumps({'settings': value, 'signal': signal},
                                        sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, project):
        raw = read_strategy_config(project)
        price_risk = raw.pop(LIVE_TAKER_PRICE_RISK_KEY, None)
        raw.pop(LIVE_TAKER_IDENTITY_KEY, None)
        base = M4Config.model_validate(raw).model_dump(mode='json')
        path = Path(project)/'config/m4-live/execution.toml'
        raw_execution = tomllib.loads(path.read_text()) if path.exists() else {}
        # This is a config-file guard for new markets. Saved market settings
        # already carry the resolved source and must round-trip unchanged.
        required = raw_execution.pop('require_opportunity_price_risk', False)
        if type(required) is not bool:
            raise ValueError('M4_OPPORTUNITY_PRICE_RISK_REQUIREMENT_INVALID')
        execution = LiveExecutionSettings.model_validate(raw_execution)
        if required and execution.mode == 'taker' and price_risk is None:
            raise ValueError('M4_OPPORTUNITY_PRICE_RISK_REQUIRED')
        base['execution_revision'] = ('m4-taker-budget-v1'
                                     if execution.mode == 'taker' else 'm4-maker-v1')
        return cls.model_validate({**base, 'execution': execution,
                                   LIVE_TAKER_PRICE_RISK_KEY: price_risk if execution.mode == 'taker' else None})


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
