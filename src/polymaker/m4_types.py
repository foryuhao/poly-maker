"""Immutable M4 opportunity identity; a DF root can authorize one task."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Opportunity:
    episode_id: str
    token_id: str
    outcome: str
    anchor_bid_price: Decimal
    signal_start_mono: float
    order_deadline: float
    support_id: str
    root_id: str
    generation: int
