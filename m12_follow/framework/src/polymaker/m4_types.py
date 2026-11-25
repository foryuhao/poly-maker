"""Immutable M4 opportunity identity; a DF root can authorize one task."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal


POST_SIGNED_LIMIT_BAND = "POST_SIGNED_LIMIT_BAND_V1"
CLOSED_SIGNED_LIMIT_BAND = "POST_SIGNED_LIMIT_CLOSED_BAND_V1"
ABANDON_FIRST_SIGNED_BAND = "ABANDON_ON_FIRST_SIGNED_LIMIT_BAND_REJECTION_V1"
WAIT_SIGNAL_BAND = "WAIT_UNTIL_SIGNAL_DEADLINE_V1"
OPPORTUNITY_PRICE_RISK = "M4_OPPORTUNITY_PRICE_RISK_V1"
BEST_ASK_SIGNAL = "FRESH_BEST_ASK_UNTIL_SIGNAL_DEADLINE_V1"
M4_LEDGER = "m4_frequency_v2_native_v1"
M6_LEDGER = "m6_withdrawal_supported_v1_1"
M21_LEDGER = "m21_endpoint_coflow_v1"
M21_VERSION = "m21-endpoint-recovery-withdrawal-v1/raw-joint-endpoint-r1/p-1256b8aa9433"
BASELINE_FOLLOW_STRATEGIES = ("M12", "M21")
M421_LEDGER = "m421_combined_v1"
M421_VERSION = "m421-m4-m21-v1"
TAKER_LEDGER_IDS = {"M4": M4_LEDGER, "M6": M6_LEDGER, "M21": M21_LEDGER, "M421": M421_LEDGER}


def signal_lifetime_limit_us(ledger):
    return 3_000_000 if ledger in {M21_LEDGER, M421_LEDGER} else 750_000


@dataclass(frozen=True)
class TakerPriceRisk:
    policy: Literal["POST_SIGNED_LIMIT_BAND_V1", "POST_SIGNED_LIMIT_CLOSED_BAND_V1"]
    minimum: Decimal
    maximum: Decimal
    retry_policy: Literal["ABANDON_ON_FIRST_SIGNED_LIMIT_BAND_REJECTION_V1", "WAIT_UNTIL_SIGNAL_DEADLINE_V1"]

    def __post_init__(self):
        try:
            low, high = Decimal(str(self.minimum)), Decimal(str(self.maximum))
        except InvalidOperation as exc:
            raise ValueError("M4_POST_PRICE_BAND_INVALID") from exc
        if ((self.policy, self.retry_policy) not in {
                (POST_SIGNED_LIMIT_BAND, ABANDON_FIRST_SIGNED_BAND),
                (POST_SIGNED_LIMIT_BAND, WAIT_SIGNAL_BAND),
                (CLOSED_SIGNED_LIMIT_BAND, WAIT_SIGNAL_BAND)}
                or not low.is_finite() or not high.is_finite() or not 0 <= low < high <= 1):
            raise ValueError("M4_POST_PRICE_BAND_INVALID")
        object.__setattr__(self, "minimum", low)
        object.__setattr__(self, "maximum", high)

    def plan_fields(self):
        if self.retry_policy == ABANDON_FIRST_SIGNED_BAND:
            source = OPPORTUNITY_PRICE_RISK
        elif self.policy == POST_SIGNED_LIMIT_BAND:
            source = "M21_OPPORTUNITY_PRICE_RISK_V1"
        else:
            source = "M6_OPPORTUNITY_PRICE_RISK_V1"
        return {
            "post_price_band_source": source,
            "post_price_band": {"policy": self.policy, "minimum": str(self.minimum), "maximum": str(self.maximum)},
            "post_price_band_retry_policy": self.retry_policy,
        }


@dataclass(frozen=True)
class TakerStrategy:
    strategy: str
    version: str
    attribution: str
    ledger: str
    config_fingerprint: str
    signal_budget_usdc: Decimal
    market_budget_usdc: Decimal
    direction_limit: int

    def __post_init__(self):
        signal, market = (Decimal(str(self.signal_budget_usdc)), Decimal(str(self.market_budget_usdc)))
        if (self.strategy != self.ledger or self.ledger not in TAKER_LEDGER_IDS.values()
                or not self.version or not self.attribution or len(self.config_fingerprint) != 64
                or not signal.is_finite() or not market.is_finite() or not 0 < signal <= market
                or type(self.direction_limit) is not int or self.direction_limit < 1):
            raise ValueError("TAKER_STRATEGY_SNAPSHOT_INVALID")
        object.__setattr__(self, "signal_budget_usdc", signal)
        object.__setattr__(self, "market_budget_usdc", market)


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
    price_risk: TakerPriceRisk | None = None
    taker_strategy: TakerStrategy | None = None
    market_id: str | None = None

    def __post_init__(self):
        if self.price_risk is not None and not isinstance(self.price_risk, TakerPriceRisk):
            raise TypeError("M4_OPPORTUNITY_PRICE_RISK_INVALID")
        if self.taker_strategy is not None and not isinstance(self.taker_strategy, TakerStrategy):
            raise TypeError("TAKER_STRATEGY_SNAPSHOT_INVALID")
