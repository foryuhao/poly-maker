"""Frozen execution contract shared by the M4 live and baseline runners."""
import hashlib
import json
import tomllib
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from polymaker.m4_frequency_signal import PARAMETERS

CURRENT_MINIMUM_PRICE = Decimal('.20')
LEGACY_MINIMUM_PRICE = Decimal('.60')


def validated_minimum_price(value):
    price = Decimal(str(value))
    if price not in {CURRENT_MINIMUM_PRICE, Decimal('.50'), LEGACY_MINIMUM_PRICE}:
        raise ValueError('M4_PRICE_FLOOR_INVALID')
    return price


def quantity_for_budget(price, minimum):
    price, minimum = Decimal(str(price)), Decimal(str(minimum))
    if (not price.is_finite() or not minimum.is_finite()
            or not 0 < price < 1 or minimum <= 0):
        raise ValueError('M4_QUOTE_INVALID')
    quantity = (Decimal(5)/price).quantize(Decimal('.1'), rounding=ROUND_FLOOR)
    if quantity < max(Decimal(5), minimum) or quantity*price > 5:
        raise ValueError('M4_MINIMUM_EXCEEDS_BUDGET')
    return quantity


class M4Config(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True)
    strategy: Literal['m4-frequency-v2'] = 'm4-frequency-v2'
    execution_revision: Literal['m4-maker-v1'] = 'm4-maker-v1'
    order_budget_usdc: Literal[5] = 5
    session_budget_usdc: Literal[10] = 10
    max_opportunities_per_direction: Literal[1] = 1
    start_sec: Literal[15] = 15
    cutoff_sec: Literal[240] = 240
    close_sec: Literal[300] = 300
    minimum_price: Decimal = Field(default=CURRENT_MINIMUM_PRICE, ge=CURRENT_MINIMUM_PRICE,
                                   le=LEGACY_MINIMUM_PRICE)
    maximum_price: Decimal = Field(default=Decimal(1), ge=1, le=1)  # Exclusive.
    quantity_step: Literal['0.1'] = '0.1'
    account_max_age_sec: float = Field(default=2., gt=0, le=5)
    account_poll_sec: float = Field(default=.5, ge=.1, le=2)
    post_budget_floor_ms: float = Field(default=400, gt=0, lt=750)
    heartbeat_sec: float = Field(default=10, ge=1, le=60)
    retry_initial_sec: float = Field(default=.1, gt=0, le=1)
    retry_max_sec: float = Field(default=2, ge=1, le=10)

    @field_validator('minimum_price')
    @classmethod
    def price_floor(cls, value):
        # The old floor remains valid only as an explicitly stored/configured
        # contract; a restart must not lower an existing market's floor.
        return validated_minimum_price(value)

    @property
    def fingerprint(self):
        payload = {'settings': self.model_dump(mode='json'), 'signal': PARAMETERS}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, project):
        # Both modes load this exact file. Missing settings must not silently
        # fall back to a different strategy or an old M1 directory.
        path = Path(project)/'config/m4.toml'
        return cls.model_validate(tomllib.loads(path.read_text()))


STRATEGY = 'm4_frequency_v2_native_v1'
