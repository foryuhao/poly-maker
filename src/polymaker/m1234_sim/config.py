"""Simulation-only M1 price and sizing settings; live defaults stay bounded."""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from polymaker.m1_live_config import M1LiveConfig


class M1SimulationConfig(M1LiveConfig):
    # Preserve legacy replay defaults. The current contract explicitly selects
    # the full venue price domain and the same five-USDC sizing as M234.
    order_budget_usdc: Literal[1, 5] = 1
    minimum_price: Decimal = Field(default=Decimal(".01"), ge=0, le=1)
    maximum_price: Decimal = Field(default=Decimal(".19"), ge=0, le=1)
