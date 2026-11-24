"""Independent live settings; loading M1 never rewrites the CA configuration."""

import hashlib
import json
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from polymaker.config import RDPM1Config, RDPM1SignalConfig, RDPSMConfig


def calibrated_signal():
    return RDPM1SignalConfig(profile="cal-16", min_round_shares=".5",
        min_round_depth_fraction=".0001", round_window_sec=.5, ap_min_shares="1",
        ap_min_depth_fraction=".0001", ap_min_share=".65", ap_min_records=2,
        ap_max_concentration=".95", rf_rounds=2, rf_sequence_span_sec=15.,
        rf_min_median_recovery=".50", rf_ttl_sec=8., combination_span_sec=20.,
        track_visible_anchor=True, flow_requires_top_match=False, mirror_complement_flow=True)


class M1LiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_budget_usdc: Literal[1] = 1
    session_budget_usdc: Literal[20] = 20
    max_opportunities_per_direction: int = Field(default=1, ge=1, le=5)
    start_sec: Literal[15] = 15
    cutoff_sec: Literal[240] = 240
    close_sec: Literal[300] = 300
    minimum_price: Decimal = Field(default=Decimal(".01"), ge=Decimal(".01"), le=Decimal(".19"))
    maximum_price: Decimal = Field(default=Decimal(".19"), ge=Decimal(".01"), le=Decimal(".19"))
    out_of_order_tolerance_ms: float = Field(default=20, ge=0, le=20, allow_inf_nan=False)
    account_max_age_sec: float = Field(default=2., gt=0, le=5, allow_inf_nan=False)
    account_poll_sec: float = Field(default=.5, ge=.1, le=2, allow_inf_nan=False)
    post_budget_floor_ms: float = Field(default=400, gt=0, lt=750, allow_inf_nan=False)
    heartbeat_sec: float = Field(default=10, ge=1, le=60, allow_inf_nan=False)
    retry_initial_sec: float = Field(default=.1, gt=0, le=1, allow_inf_nan=False)
    retry_max_sec: float = Field(default=2, ge=1, le=10, allow_inf_nan=False)
    signal: RDPM1SignalConfig = Field(default_factory=calibrated_signal)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.minimum_price > self.maximum_price:
            raise ValueError("M1_PRICE_RANGE_INVALID")
        return self

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, directory):
        path = Path(directory)/"m1.toml"
        return cls.model_validate(tomllib.loads(path.read_text()) if path.exists() else {})

    def execution_config(self, original):
        cfg = original.model_copy(deep=True)
        # Only transport/feed primitives use this compatibility object. Entry
        # time, amount and order lifetime are owned exclusively by M1LiveConfig.
        sm = RDPSMConfig(normal_entry_mode="m1_v1",
                        m1=RDPM1Config(observation_only=False, signal=self.signal,
                            out_of_order_tolerance_ms=self.out_of_order_tolerance_ms,
                            output_dir="research_outputs/m1_live/events", record_events=False))
        cfg.rdp_sm = sm
        # Keep the original journal root, credentials and endpoints.
        return cfg
