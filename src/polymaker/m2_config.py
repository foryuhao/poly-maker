"""Independent public M2 research settings; never loads wallet configuration."""

import hashlib
import json
import tomllib
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from polymaker.config import RDPM1SignalConfig


def signal_defaults():
    return RDPM1SignalConfig(
        profile="m2-candidates-v1", min_round_shares=".5",
        min_round_depth_fraction=".0001", round_window_sec=.5,
        ap_min_shares="1", ap_min_depth_fraction=".0001", ap_min_share=".65",
        ap_min_records=2, ap_max_concentration=".95", rf_rounds=2,
        rf_sequence_span_sec=15, rf_min_median_recovery=".50", rf_ttl_sec=8,
        combination_span_sec=20, track_visible_anchor=True,
        flow_requires_top_match=False, mirror_complement_flow=True,
    )


class M2Config(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    signal: RDPM1SignalConfig = Field(default_factory=signal_defaults)
    start_sec: float = Field(default=15, ge=0, lt=240)
    cutoff_sec: float = Field(default=240, gt=0, le=300)
    close_sec: float = Field(default=300, ge=300, le=300)
    observation_interval_sec: float = Field(default=.25, ge=.1, le=1)
    attack_lifetime_sec: float = Field(default=20, gt=0, le=60)
    risk_usdc: Decimal = Field(default=Decimal(5), gt=0)
    market_budget_usdc: Decimal = Field(default=Decimal(20), gt=0)
    max_entries_per_direction: int = Field(default=1, ge=1, le=1)
    include_m3_m4: bool = False
    m4_frequency_study: bool = False
    # Exploratory comparisons only. No one threshold is designated validated M2.
    research_ratios: tuple[Decimal, ...] = (Decimal(".25"), Decimal(".50"),
                                           Decimal(".75"), Decimal("1.00"))
    out_of_order_tolerance_ms: int = Field(default=20, ge=0, le=20)
    max_output_mib: int = Field(default=512, ge=16)
    min_free_disk_mib: int = Field(default=2048, ge=256)
    host_cpu_guard_enabled: bool = True
    m1_cpu_guard_enabled: bool = True
    max_host_cpu_busy_pct: float = Field(default=85, ge=0, lt=100)
    max_m1_cpu_pressure_pct: float = Field(default=20, ge=0, le=100)
    cpu_busy_grace_sec: float = Field(default=5, ge=0, le=10)

    @model_validator(mode="after")
    def valid(self):
        if self.m4_frequency_study and not self.include_m3_m4:
            raise ValueError("M4_FREQUENCY_REQUIRES_SHARED_M4_ROUNDS")
        if self.start_sec >= self.cutoff_sec:
            raise ValueError("M2_WINDOW_INVALID")
        if self.market_budget_usdc < self.risk_usdc:
            raise ValueError("M2_BUDGET_INVALID")
        if (not self.research_ratios or len(set(self.research_ratios)) != len(self.research_ratios)
                or any(not r.is_finite() or not 0 < r <= 1 for r in self.research_ratios)):
            raise ValueError("M2_RESEARCH_RATIOS_INVALID")
        if not self.signal.track_visible_anchor:
            raise ValueError("M2_REQUIRES_FIXED_VISIBLE_ANCHOR")
        return self

    @property
    def fingerprint(self):
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    @property
    def rules(self):
        return {"all_candidates": None,
                **{f"buy_ratio_le_{r:.2f}": r for r in self.research_ratios}}

    @classmethod
    def load(cls, path=None):
        return cls.model_validate(tomllib.loads(Path(path).read_text())) if path else cls()
