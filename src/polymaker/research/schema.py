"""Version 1, observation-only research contracts.

Decimals are stored as finite strings so JSON round trips preserve accounting precision.
Unknown observations are None, never invented zeros.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)


def _decimal_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("decimal values must be strings")
    try:
        if not Decimal(value).is_finite():
            raise ValueError("decimal value must be finite")
    except InvalidOperation as exc:
        raise ValueError("invalid decimal value") from exc
    return value


DecimalString = Annotated[str, BeforeValidator(_decimal_string)]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Side = Literal["YES", "NO"]
Role = Literal["FIRST", "PAIR"]


class ResearchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceRef(ResearchRecord):
    path: str
    sha256: str | None
    offset_bytes: NonnegativeInt
    length_bytes: NonnegativeInt


class Quote(ResearchRecord):
    token_id: str
    side: Literal["BUY"]
    price: DecimalString
    qty: DecimalString


class WorkingOrder(ResearchRecord):
    order_id: str
    role: Role
    quote: Quote
    submitted_at_us: NonnegativeInt | None
    acknowledged_at_us: NonnegativeInt | None
    remaining_qty: DecimalString
    cumulative_fill_qty: DecimalString
    lifecycle: str
    cancel_requested_at_us: NonnegativeInt | None
    cancel_acknowledged_at_us: NonnegativeInt | None
    terminal_authoritative: bool


class Level(ResearchRecord):
    price: DecimalString
    qty: DecimalString


class BookState(ResearchRecord):
    token_id: str
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    levels_recorded: NonnegativeInt
    best_bid: DecimalString | None
    best_ask: DecimalString | None
    best_bid_qty: DecimalString | None
    best_ask_qty: DecimalString | None
    mid: DecimalString | None
    spread: DecimalString | None
    exchange_at_us: NonnegativeInt | None
    received_at_us: NonnegativeInt | None
    applied_at_us: NonnegativeInt
    book_seq: NonnegativeInt
    snapshot_generation: NonnegativeInt
    source: Literal["WS", "REST"]
    valid: bool

    @field_validator("bids", "asks")
    @classmethod
    def top_five(cls, levels: tuple[Level, ...]) -> tuple[Level, ...]:
        if len(levels) > 5:
            raise ValueError("BookState holds at most five levels per side")
        return levels


class FeatureVector(ResearchRecord):
    first_side: Side | None
    favorite_side: Side | None

    first_fill_price: FiniteFloat | None
    first_fill_qty: FiniteFloat | None
    first_vwap: FiniteFloat | None
    first_qty: FiniteFloat | None
    first_cost_usd: FiniteFloat | None
    since_first_fill_s: FiniteFloat | None
    market_elapsed_s: FiniteFloat | None
    time_to_end_s: FiniteFloat | None
    time_to_forced_s: FiniteFloat | None
    time_to_cutoff_s: FiniteFloat | None
    yes_qty: FiniteFloat | None
    no_qty: FiniteFloat | None
    paired_qty: FiniteFloat | None
    unpaired_qty: FiniteFloat | None
    theoretical_pair_pnl_usd: FiniteFloat | None
    quoted_pair_pnl_usd: FiniteFloat | None
    executable_pair_ev_usd: FiniteFloat | None
    peak_quoted_pair_pnl_usd: FiniteFloat | None
    drawdown_from_peak_usd: FiniteFloat | None
    pair_quote_price: FiniteFloat | None
    pair_quote_qty: FiniteFloat | None
    pair_quote_distance_bid_ticks: FiniteFloat | None
    pair_quote_distance_ask_ticks: FiniteFloat | None
    pair_order_age_s: FiniteFloat | None
    queue_ahead_proxy_qty: FiniteFloat | None
    own_fill_qty_10s: FiniteFloat | None
    own_order_cancel_count_10s: FiniteFloat | None
    maker_fill_intensity_10s: FiniteFloat | None
    opposite_bid_depletion_10s: FiniteFloat | None
    second_leg_fill_probability_3s: FiniteFloat | None
    favorite_price_now: FiniteFloat | None
    favorite_price_at_first_fill: FiniteFloat | None
    held_mid_change_since_fill: FiniteFloat | None
    held_mid_mfe: FiniteFloat | None
    held_mid_mae: FiniteFloat | None
    held_mid_reversion_5s: FiniteFloat | None
    market_processing_lag_ms: FiniteFloat | None
    btc_realized_vol_20s: FiniteFloat | None
    btc_acceleration_3s: FiniteFloat | None
    btc_open_distance_bps: FiniteFloat | None
    btc_direction_persistence_5s: FiniteFloat | None
    yes_bid: FiniteFloat | None
    yes_ask: FiniteFloat | None
    yes_mid: FiniteFloat | None
    yes_spread: FiniteFloat | None
    yes_bid_qty: FiniteFloat | None
    yes_ask_qty: FiniteFloat | None
    yes_bid_depth5: FiniteFloat | None
    yes_ask_depth5: FiniteFloat | None
    yes_imbalance5: FiniteFloat | None
    yes_book_age_s: FiniteFloat | None
    yes_imbalance_change_1s: FiniteFloat | None
    yes_imbalance_change_3s: FiniteFloat | None
    yes_imbalance_change_5s: FiniteFloat | None
    yes_bid_velocity_1s: FiniteFloat | None
    yes_bid_velocity_3s: FiniteFloat | None
    yes_bid_velocity_5s: FiniteFloat | None
    yes_spread_change_1s: FiniteFloat | None
    yes_spread_change_3s: FiniteFloat | None
    yes_spread_change_5s: FiniteFloat | None
    yes_sell_trade_qty_1s: FiniteFloat | None
    yes_sell_trade_qty_5s: FiniteFloat | None
    yes_sell_trade_qty_10s: FiniteFloat | None
    yes_buy_trade_qty_1s: FiniteFloat | None
    yes_buy_trade_qty_5s: FiniteFloat | None
    yes_buy_trade_qty_10s: FiniteFloat | None
    no_bid: FiniteFloat | None
    no_ask: FiniteFloat | None
    no_mid: FiniteFloat | None
    no_spread: FiniteFloat | None
    no_bid_qty: FiniteFloat | None
    no_ask_qty: FiniteFloat | None
    no_bid_depth5: FiniteFloat | None
    no_ask_depth5: FiniteFloat | None
    no_imbalance5: FiniteFloat | None
    no_book_age_s: FiniteFloat | None
    no_imbalance_change_1s: FiniteFloat | None
    no_imbalance_change_3s: FiniteFloat | None
    no_imbalance_change_5s: FiniteFloat | None
    no_bid_velocity_1s: FiniteFloat | None
    no_bid_velocity_3s: FiniteFloat | None
    no_bid_velocity_5s: FiniteFloat | None
    no_spread_change_1s: FiniteFloat | None
    no_spread_change_3s: FiniteFloat | None
    no_spread_change_5s: FiniteFloat | None
    no_sell_trade_qty_1s: FiniteFloat | None
    no_sell_trade_qty_5s: FiniteFloat | None
    no_sell_trade_qty_10s: FiniteFloat | None
    no_buy_trade_qty_1s: FiniteFloat | None
    no_buy_trade_qty_5s: FiniteFloat | None
    no_buy_trade_qty_10s: FiniteFloat | None
    held_mid_change_1s: FiniteFloat | None
    btc_log_return_1s: FiniteFloat | None
    held_mid_change_3s: FiniteFloat | None
    btc_log_return_3s: FiniteFloat | None
    held_mid_change_5s: FiniteFloat | None
    btc_log_return_5s: FiniteFloat | None
    held_mid_change_10s: FiniteFloat | None
    btc_log_return_10s: FiniteFloat | None
    held_mid_change_20s: FiniteFloat | None
    btc_log_return_20s: FiniteFloat | None
    data_valid: bool | None
    btc_available: bool | None
    pair_cancel_pending: bool | None


class Episode(ResearchRecord):
    schema_version: Literal[1]
    dataset_id: str
    record_id: str
    created_at_us: NonnegativeInt
    source_refs: tuple[SourceRef, ...]
    episode_id: str
    revision: PositiveInt
    revision_known_at_us: NonnegativeInt
    run_ids: tuple[str, ...]
    market_slug: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    market_start_us: NonnegativeInt
    market_end_us: NonnegativeInt
    strategy_version: str
    strategy_config_id: str
    first_side: Side
    first_order_id: str
    first_fill_key: str
    first_fill_exchange_us: NonnegativeInt | None
    first_fill_known_at_us: NonnegativeInt
    first_match_price: DecimalString
    first_match_qty: DecimalString
    first_leg_finalized_at_us: NonnegativeInt | None
    first_leg_final_qty: DecimalString | None
    first_leg_final_cost_usd: DecimalString | None
    first_leg_final_vwap: DecimalString | None
    first_order_ids: tuple[str, ...]
    pair_order_ids: tuple[str, ...]
    actual_pair_start_us: NonnegativeInt | None
    actual_pair_complete_us: NonnegativeInt | None
    actual_exit_trigger: Literal["NONE", "EMERGENCY_STOP", "PROBATION_FAILED", "TRAILING_STOP", "TIME_FORCED", "OTHER"]
    actual_terminal_state: str
    actual_paired_qty: DecimalString | None
    actual_residual_yes_qty: DecimalString | None
    actual_residual_no_qty: DecimalString | None
    actual_gross_pnl_usd: DecimalString | None
    actual_fees_usd: DecimalString | None
    actual_rebate_usd: DecimalString | None
    actual_settlement_cost_usd: DecimalString | None
    actual_net_pnl_usd: DecimalString | None
    actual_cash_received_usd: DecimalString | None
    payout_yes: DecimalString | None
    payout_no: DecimalString | None
    resolution_known_at_us: NonnegativeInt | None
    resolution_source_ref: SourceRef | None
    capture_start_us: NonnegativeInt
    capture_end_us: NonnegativeInt | None
    capture_status: Literal["OPEN", "COMPLETE", "CENSORED", "INVALID"]
    fill_truth_status: Literal["PROVISIONAL", "COMPLETE", "UNRESOLVED", "INVALID"]
    quality_reasons: tuple[str, ...]


class StateSnapshot(ResearchRecord):
    schema_version: Literal[1]
    dataset_id: str
    record_id: str
    created_at_us: NonnegativeInt
    source_refs: tuple[SourceRef, ...]
    snapshot_id: str
    episode_id: str
    session_id: str
    snapshot_seq: PositiveInt
    sample_kind: Literal["PERIODIC", "FILL", "ORDER", "SAFETY", "FINAL"]
    scheduled_at_us: NonnegativeInt | None
    available_at_us: NonnegativeInt
    monotonic_ns: NonnegativeInt
    knowledge_seq: NonnegativeInt
    episode_revision: PositiveInt
    market_elapsed_s: FiniteFloat
    seconds_to_end: FiniteFloat
    seconds_to_forced_pair: FiniteFloat
    seconds_to_order_cutoff: FiniteFloat
    since_first_known_fill_s: FiniteFloat
    since_first_exchange_fill_s: FiniteFloat | None
    first_side: Side
    first_qty: DecimalString
    first_cost_usd: DecimalString
    first_vwap: DecimalString | None
    pair_qty: DecimalString
    pair_cost_usd: DecimalString
    pair_vwap: DecimalString | None
    yes_qty: DecimalString
    no_qty: DecimalString
    paired_qty: DecimalString
    unpaired_first_qty: DecimalString
    unpaired_opposite_qty: DecimalString
    first_finalized: bool
    pair_terminal_authoritative: bool
    baseline_state: str
    baseline_pair_trigger: str | None
    baseline_peak_quoted_pnl_usd: DecimalString | None
    baseline_quote: Quote | None
    effective_quote: Quote | None
    working_orders: tuple[WorkingOrder, ...]
    yes_book: BookState
    no_book: BookState
    market_connected: bool
    user_connected: bool
    market_generation: NonnegativeInt
    user_generation: NonnegativeInt
    fill_truth_ready: bool
    order_state_resolved: bool
    safety_reasons: tuple[str, ...]
    tick_size: DecimalString
    venue_min_order_qty: DecimalString
    theoretical_mid_pair_pnl_usd: DecimalString | None
    quoted_pair_pnl_usd: DecimalString | None
    candidate_pair_quote: Quote | None
    pair_submission_feasible: bool
    all_postfill_peak_quoted_pnl_usd: DecimalString | None
    drawdown_quoted_pnl_usd: DecimalString | None
    execution_estimate: None  # reserved: M4A emits no execution model values
    features: FeatureVector
    coverage_valid: bool
    quality_reasons: tuple[str, ...]


class ResearchEvent(ResearchRecord):
    schema_version: Literal[1]
    session_id: str
    event_seq: PositiveInt
    condition_id: str
    episode_id: str | None
    kind: Literal["BOOK", "DELTA", "TRADE", "TICK", "REST_BOOK", "CONNECTION", "ORDER_SUBMIT", "ORDER_ACK", "CANCEL_REQUEST", "CANCEL_ACK", "ORDER_STATE", "FILL_REVISION", "BASELINE_DECISION", "SAFETY", "GAP"]
    exchange_at_us: NonnegativeInt | None
    received_at_us: NonnegativeInt | None
    available_at_us: NonnegativeInt
    monotonic_ns: NonnegativeInt
    source: Literal["MARKET_WS", "USER_WS", "REST", "ENGINE", "OBSERVER"]
    connection_generation: NonnegativeInt | None
    source_ref: SourceRef | None
    payload: Mapping[str, Any]

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, payload: Any) -> Mapping[str, Any]:
        def freeze(value: Any) -> Any:
            if value is None or type(value) in (str, bool, int):
                return value
            if type(value) is float and math.isfinite(value):
                return value
            if type(value) in (list, tuple):
                return tuple(freeze(item) for item in value)
            if isinstance(value, Mapping) and all(type(key) is str for key in value):
                forbidden = {"private_key", "api_secret", "builder_secret", "builder_key",
                             "builder_passphrase", "authorization", "auth_header", "password"}
                if any(key.lower() in forbidden for key in value):
                    raise ValueError("payload contains a credential field")
                return MappingProxyType({key: freeze(item) for key, item in value.items()})
            raise ValueError("payload must contain only finite JSON primitives")
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be an object")
        return cast(Mapping[str, Any], freeze(payload))

    @field_serializer("payload")
    def serialize_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        def thaw(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, tuple):
                return [thaw(item) for item in value]
            return value
        return cast(dict[str, Any], thaw(payload))


class DatasetManifest(ResearchRecord):
    schema_version: Literal[1]
    dataset_id: str
    session_id: str
    code_commit: str
    strategy_config_id: str
    effective_strategy_config: Mapping[str, str | int | float | bool | None]
    config_sha256: str
    feature_version: str | None
    label_version: str | None
    policy_version: str | None
    execution_version: str | None
    payload_versions: Mapping[str, int]
    condition_id: str
    market_slug: str
    yes_token_id: str
    no_token_id: str
    split_boundaries_us: Mapping[str, int]
    started_at_us: NonnegativeInt
    ended_at_us: NonnegativeInt | None
    file_sha256: Mapping[str, str]
    event_watermark: NonnegativeInt
    gap_count: NonnegativeInt
    close_status: Literal["OPEN", "COMPLETE", "INCOMPLETE"]

    @field_validator("payload_versions", "file_sha256", "effective_strategy_config", "split_boundaries_us")
    @classmethod
    def freeze_mapping(cls, values: Mapping[str, Any]) -> Mapping[str, Any]:
        if any(key.lower() in {"private_key", "api_secret", "builder_secret", "builder_key",
                               "builder_passphrase", "authorization", "password"}
               for key in values):
            raise ValueError("manifest contains a credential field")
        if any(type(value) is float and not math.isfinite(value) for value in values.values()):
            raise ValueError("manifest contains a non-finite value")
        return MappingProxyType(dict(values))

    @field_serializer("payload_versions", "file_sha256", "effective_strategy_config", "split_boundaries_us")
    def serialize_mapping(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return dict(values)


class QualityReport(ResearchRecord):
    schema_version: Literal[1]
    dataset_id: str
    session_id: str
    audited_at_us: NonnegativeInt
    event_count: NonnegativeInt
    episode_count: NonnegativeInt
    snapshot_count: NonnegativeInt
    gap_count: NonnegativeInt
    valid: bool
    reasons: tuple[str, ...]
