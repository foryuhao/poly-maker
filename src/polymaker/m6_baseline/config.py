"""M6 contract and source fingerprint, independent of M4 configuration."""
import hashlib
import json
import tomllib
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

STRATEGY = "m6_withdrawal_supported_v1_1"
CURRENT_MINIMUM_PRICE = Decimal(".02")


def signal_sources():
    root = Path(__file__).parent / "_signal_v7"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.iterdir()) if p.suffix in {".py", ".json"}}


def validated_minimum_price(value):
    result = Decimal(str(value))
    if result != CURRENT_MINIMUM_PRICE:
        raise ValueError("M6_PRICE_FLOOR_INVALID")
    return result


def quantity_for_budget(price, minimum):
    price, minimum = Decimal(str(price)), Decimal(str(minimum))
    if not price.is_finite() or not minimum.is_finite() or not 0 < price < 1 or minimum <= 0:
        raise ValueError("M6_QUOTE_INVALID")
    quantity = (Decimal(5)/price).quantize(Decimal(".1"), rounding=ROUND_FLOOR)
    if quantity < max(Decimal(5), minimum) or price*quantity > 5:
        raise ValueError("M6_MINIMUM_EXCEEDS_BUDGET")
    return quantity


class M6Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    strategy: Literal["m6-withdrawal-supported-v1.1"] = "m6-withdrawal-supported-v1.1"
    execution_revision: Literal["m6-baseline-maker-v1"] = "m6-baseline-maker-v1"
    minimum_price: Literal[Decimal(".02")] = Decimal(".02")
    maximum_price: Literal[Decimal(".20")] = Decimal(".20")
    order_budget_usdc: Literal[5] = 5
    session_budget_usdc: Literal[10] = 10
    max_opportunities_per_direction: Literal[1] = 1
    start_sec: Literal[15] = 15
    cutoff_sec: Literal[240] = 240
    close_sec: Literal[300] = 300
    quantity_step: Literal["0.1"] = "0.1"
    account_max_age_sec: float = Field(default=2, gt=0, le=5)
    account_poll_sec: float = Field(default=.5, ge=.1, le=2)
    heartbeat_sec: float = Field(default=10, ge=1, le=60)
    post_budget_floor_ms: Literal[400] = 400
    retry_initial_sec: float = Field(default=.1, gt=0, le=1)
    retry_max_sec: float = Field(default=2, ge=1, le=10)

    @field_validator("minimum_price", "maximum_price", mode="before")
    @classmethod
    def decimal_price(cls, value):
        return Decimal(str(value))

    @property
    def fingerprint(self):
        payload = {"settings": self.model_dump(mode="json"), "signal_source_sha256": signal_sources()}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, project):
        raw = tomllib.loads((Path(project)/"config/m6.toml").read_text(), parse_float=Decimal)
        raw.pop("live_taker", None)
        return cls.model_validate(raw)
