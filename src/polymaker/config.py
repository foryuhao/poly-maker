"""Configuration: pydantic models over local TOML files + .env secrets.

Replaces the v1 Google Sheets config entirely. Three files under config/:
  config.toml    engine/wallet/risk/execution settings
  strategy.toml  named parameter profiles
  markets.toml   the trade list (market -> profile + overrides)

Secrets (private key, wallet address) come only from the environment / .env.
"""

from __future__ import annotations

import os
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values, load_dotenv
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigAliasError(ValueError):
    """A live credential alias is invalid or disagrees with its counterpart."""


def _key_identity(value: str) -> str:
    """Compare harmless textual variants without exposing key material."""
    key = value.strip()
    if key[:2].lower() == "0x":
        key = key[2:]
    return key.lower() if key and all(char in "0123456789abcdefABCDEF" for char in key) else key


def _address_identity(value: str, name: str) -> str:
    from web3 import Web3

    address = value.strip()
    if not address:
        return ""
    if not Web3.is_address(address):
        raise ConfigAliasError(f"CONFIG_INVALID_ADDRESS: {name}")
    return Web3.to_checksum_address(address)


def _resolve_alias_pair(source: dict[str, Any], legacy: str, canonical: str,
                        *, address: bool = False) -> str | None:
    """Resolve one alias pair or fail before constructing live clients."""
    present = [name for name in (legacy, canonical) if name in source]
    if not present:
        return None
    normalized = {
        name: (_address_identity(str(source[name] or ""), name) if address
               else _key_identity(str(source[name] or "")))
        for name in present
    }
    if len(present) == 2 and normalized[legacy] != normalized[canonical]:
        raise ConfigAliasError(
            f"CONFIG_ALIAS_CONFLICT: {legacy} and {canonical} are both set but differ")
    return normalized[present[0]] if address else str(source[present[0]] or "").strip()


class WalletConfig(BaseModel):
    chain_id: int = 137
    signature_type: int = 2
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    data_api_host: str = "https://data-api.polymarket.com"
    polygon_rpc: str = "https://polygon-bor-rpc.publicnode.com"


class EngineConfig(BaseModel):
    debounce_ms: int = 200
    # baseline periodic re-quote (book reactions are event-driven & instant; this
    # is just a slow refresh for cool-off re-entry / exit-urgency updates). A
    # precise wake is also scheduled for the exact moment an EVENT cool-off ends.
    quoter_tick_s: float = 60.0
    reconcile_interval_s: float = 30.0
    catalog_refresh_s: float = 900.0
    heartbeat: bool = True
    heartbeat_interval_s: float = 5.0
    journal: bool = True
    loop: str = "uvloop"


class RiskConfig(BaseModel):
    max_total_exposure_usdc: float = 5000.0
    max_event_group_loss_usdc: float = 1000.0
    max_market_notional_usdc: float = 800.0
    daily_loss_kill_usdc: float = 250.0
    ws_stale_halt_s: float = 10.0
    # user WS down this long -> we can't see our fills -> pull all quotes
    user_ws_blind_halt_s: float = 15.0
    # consecutive heartbeat failures -> exchange is auto-cancelling us -> halt
    heartbeat_halt_failures: int = 3
    max_order_error_rate: float = 0.25


class ExecutionConfig(BaseModel):
    rate_budget_fraction: float = 0.25
    post_only: bool = True
    max_orders_per_batch: int = 15


class RDPObiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: Decimal = Field(default=Decimal("0.30"), gt=0, le=1, allow_inf_nan=False)
    normal_confirmations_before_60: int = Field(default=2, ge=1, strict=True)
    normal_confirmations_from_60: int = Field(default=3, ge=1, strict=True)
    signal_horizon_sec: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    rebalance_order_ttl_sec: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    max_book_age_ms: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    max_signal_age_ms: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    max_pair_skew_ms: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    # Historical name retained for existing configs: now the individual slow-request threshold.
    max_rtt_p95_ms: float = Field(default=500.0, gt=0, allow_inf_nan=False)
    # Compatibility/telemetry only; historical RTT is no longer a signal-age veto.
    max_signal_plus_rtt_ms: float = Field(default=500.0, gt=0, allow_inf_nan=False)
    network_min_samples: int = Field(default=10, ge=1)
    network_window_sec: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    network_sample_max_age_sec: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    network_recovery_samples: int = Field(default=3, ge=1)
    network_recent_window_sec: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    network_slow_sample_count: int = Field(default=3, ge=2)
    network_slow_trigger_count: int = Field(default=2, ge=2)
    network_pause_sec: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    network_max_pause_sec: float = Field(default=8.0, gt=0, allow_inf_nan=False)
    network_probe_interval_sec: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    cancel_retry_sec: float = Field(default=0.25, gt=0, allow_inf_nan=False)
    # The first live experiment never rearms a consumed continuous direction.
    repeat_same_signal: Literal[False] = False

    @model_validator(mode="after")
    def _budgets(self):
        if self.max_signal_age_ms >= self.signal_horizon_sec * 1000:
            raise ValueError("signal age must be shorter than its horizon")
        if self.network_sample_max_age_sec > self.network_window_sec:
            raise ValueError("network sample age exceeds rolling window")
        if self.network_recent_window_sec > self.network_window_sec:
            raise ValueError("network recent window exceeds diagnostic window")
        if self.network_slow_trigger_count > self.network_slow_sample_count:
            raise ValueError("network slow trigger exceeds sample count")
        if self.network_max_pause_sec < self.network_pause_sec:
            raise ValueError("maximum network pause is shorter than initial pause")
        return self


class RDPBANetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    post_budget_floor_ms: float = Field(default=750., gt=0, allow_inf_nan=False)
    min_live_budget_ms: float = Field(default=500., gt=0, allow_inf_nan=False)
    max_cancel_rtt_ms: float = Field(default=750., gt=0, allow_inf_nan=False)


class RDPBAConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_sell_shares: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)
    min_depletion_shares: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)
    min_refill_shares: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)
    observation_sec: float = Field(default=.75, gt=0, allow_inf_nan=False)
    signal_lifetime_sec: float = Field(default=2., gt=0, allow_inf_nan=False)
    max_sell_age_ms: float = Field(default=500., gt=0, allow_inf_nan=False)
    max_book_age_ms: float = Field(default=100., gt=0, allow_inf_nan=False)
    max_pair_skew_ms: float = Field(default=200., gt=0, allow_inf_nan=False)
    max_entry_age_ms: float = Field(default=1000., gt=0, allow_inf_nan=False)
    out_of_order_tolerance_ms: float = Field(default=10., ge=0, allow_inf_nan=False)
    network: RDPBANetworkConfig = Field(default_factory=RDPBANetworkConfig)

    @model_validator(mode="after")
    def _deadlines(self):
        if self.observation_sec >= self.signal_lifetime_sec:
            raise ValueError("BA observation must end before order deadline")
        if self.max_sell_age_ms >= self.signal_lifetime_sec * 1000:
            raise ValueError("BA sell age must be shorter than signal lifetime")
        if not self.observation_sec*1000 < self.max_entry_age_ms < self.signal_lifetime_sec*1000:
            raise ValueError("BA observation < entry age < signal lifetime is required")
        if (self.network.post_budget_floor_ms+self.network.min_live_budget_ms
                >= self.signal_lifetime_sec*1000):
            raise ValueError("BA execution budget must fit inside signal lifetime")
        return self


class RDPCAObserveConfig(BaseModel):
    """Read-only CA observation; all timing/SELL budgets come from NORMAL BA."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, strict=True)
    min_buy_shares: Decimal = Field(default=Decimal("10"), gt=0, allow_inf_nan=False)
    output_dir: str = "research_outputs/ca_observe"


class RDPCAMatchedTPConfig(BaseModel):
    """Fixed opposite maker BUY, admitted only after an owned CA fill."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, strict=True)
    restore_after_ba: bool = Field(default=False, strict=True)
    profit_per_share: Decimal = Field(default=Decimal("0.20"), gt=0, lt=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _restore_requires_tp(self):
        if self.restore_after_ba and not self.enabled:
            raise ValueError("restore_after_ba requires ca_matched_tp.enabled")
        return self


class RDPSyncObserveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    use_for_execution: bool = False
    order_interval_s: float = Field(default=1.0, ge=.01, allow_inf_nan=False)
    account_interval_s: float = Field(default=2.0, ge=.01, allow_inf_nan=False)
    output_dir: str = "research_outputs/periodic_sync"

    @model_validator(mode="after")
    def _execution_requires_service(self):
        if self.use_for_execution and not self.enabled:
            raise ValueError("execution cache requires sync_observe.enabled")
        return self


class RDPRepairPathConfig(BaseModel):
    """User-confirmed 2026-10-19 starting values, not execution/EV validation."""

    model_config = ConfigDict(extra="forbid")
    tp_enabled: bool = Field(default=True, strict=True)
    sl_enabled: bool = Field(default=True, strict=True)
    tp_profit_rate: Decimal | None = Field(default=Decimal("0.002"), gt=0, allow_inf_nan=False)
    shock_window_sec: float | None = Field(default=2., gt=0, le=30, allow_inf_nan=False)
    shock_max_age_sec: float = Field(default=3., gt=0, le=30, allow_inf_nan=False)
    shock_min_ticks: int = Field(default=2, ge=1, strict=True)
    shock_z: float | None = Field(default=1.5, gt=0, allow_inf_nan=False)
    drawdown_per_share: Decimal | None = Field(default=Decimal("0.06"), gt=0, allow_inf_nan=False)
    calibration_status: Literal["UNCALIBRATED", "PARAMETERS_CONFIRMED", "REPLAY_VALIDATED"] = "PARAMETERS_CONFIRMED"
    parameter_reference: str = "docs/development/REPAIR_PARAMETER_PROPOSAL_20261019.md"
    validation_reference: str | None = None

    def exit_enabled(self, mode: str) -> bool:
        return self.tp_enabled if mode == "TP" else self.sl_enabled if mode == "SL" else False

    @model_validator(mode="after")
    def _shock_age(self):
        if self.shock_window_sec is not None and self.shock_max_age_sec < self.shock_window_sec:
            raise ValueError("shock_max_age_sec must be >= shock_window_sec")
        return self


class RDPRepairAbandonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, strict=True)
    min_elapsed_sec: float = Field(default=120.0, ge=0, le=300, allow_inf_nan=False)
    entry_price_threshold: Decimal = Field(default=Decimal("0.80"), gt=0, le=1, allow_inf_nan=False)


class RDPUnknownRecoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    confirmation_rounds: int = Field(default=3, ge=2, le=20)


class RDPInventoryLagConfig(BaseModel):
    """Opt-in, one-order REPAIR permission; never a NORMAL or close proof."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, strict=True)
    exclusive_market_writer: bool = Field(default=False, strict=True)
    grace_sec: float = Field(default=2.0, gt=0, le=5, allow_inf_nan=False)
    max_unreported_shares: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _exclusive_writer(self):
        if self.enabled and not self.exclusive_market_writer:
            raise ValueError("inventory_lag requires exclusive_market_writer")
        return self


class RDPOppositeBAExecutionConfig(BaseModel):
    """Ask-minus-tick execution; quotes stay fixed once posted."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, strict=True)
    # Accept legacy configurations; this field no longer constrains execution.
    price_tolerance: Decimal | None = Field(default=None, ge=0, lt=1, allow_inf_nan=False)


class RDPRepairConfig(BaseModel):
    """Versioned REPAIR model inputs. Defaults are research, not fitted estimates."""

    model_config = ConfigDict(extra="forbid")
    policy: Literal["ev_legacy", "hd_tp_sl_v1", "opposite_ba_v1"] = "ev_legacy"
    path: RDPRepairPathConfig = Field(default_factory=RDPRepairPathConfig)
    opposite_ba: RDPBAConfig = Field(default_factory=RDPBAConfig)
    opposite_ba_execution: RDPOppositeBAExecutionConfig = Field(default_factory=RDPOppositeBAExecutionConfig)
    abandon_high_entry: RDPRepairAbandonConfig = Field(default_factory=RDPRepairAbandonConfig)
    unknown_recovery: RDPUnknownRecoveryConfig = Field(default_factory=RDPUnknownRecoveryConfig)
    inventory_lag: RDPInventoryLagConfig = Field(default_factory=RDPInventoryLagConfig)
    model_version: Literal["bounded_martingale_v1", "review_lease_v2"] = "review_lease_v2"
    parameter_status: Literal["UNCALIBRATED"] = "UNCALIBRATED"
    delta_limit: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)
    review_sec: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    cutoff_sec: float = Field(default=240.0, gt=0, le=300, allow_inf_nan=False)
    liquidity_rate: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    queue_scale_sec: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    depth_decay: float = Field(default=50.0, ge=0, allow_inf_nan=False)
    submit_delay_sec: float = Field(default=.2, ge=0, allow_inf_nan=False)
    cancel_delay_sec: float = Field(default=.2, ge=0, allow_inf_nan=False)
    fill_chunk: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    cross_fill_shares: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    cross_fill_probability: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    grid_step: float = Field(default=.005, ge=.001, le=.05, allow_inf_nan=False)
    continuation_grid_step: float = Field(default=.02, ge=.005, le=.05, allow_inf_nan=False)
    time_step_sec: float = Field(default=.1, ge=.05, le=1, allow_inf_nan=False)
    solve_timeout_sec: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    horizon_sec: tuple[float, ...] = (2, 5, 10, 20, 40, 80, 120, 140)

    @model_validator(mode="after")
    def _limits(self):
        if self.opposite_ba_execution.enabled and self.policy != "opposite_ba_v1":
            raise ValueError("opposite_ba_execution requires repair.policy=opposite_ba_v1")
        if self.policy == "ev_legacy" and not (self.path.tp_enabled and self.path.sl_enabled):
            raise ValueError("TP/SL switches require repair.policy=hd_tp_sl_v1")
        if self.delta_limit != Decimal("5"):
            raise ValueError("REPAIR v1 requires session Delta limit 5")
        if not self.horizon_sec or any(not 0 < h <= 240 for h in self.horizon_sec):
            raise ValueError("repair horizons must be finite positive seconds <=240")
        return self


class RDPCooldownConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_sec: float = Field(default=20.0, ge=0, allow_inf_nan=False)
    loss_sec_per_dollar: float = Field(default=100.0, ge=0, allow_inf_nan=False)


class RDPSMConfig(BaseModel):
    normal_entry_mode: Literal["legacy", "obi_v1", "ba_v1", "ca_v1"] = "legacy"
    normal_cutoff_sec: float = Field(default=120.0, gt=0, le=300, allow_inf_nan=False)
    obi: RDPObiConfig = Field(default_factory=RDPObiConfig)
    ba: RDPBAConfig = Field(default_factory=RDPBAConfig)
    ca_observe: RDPCAObserveConfig = Field(default_factory=RDPCAObserveConfig)
    ca_matched_tp: RDPCAMatchedTPConfig = Field(default_factory=RDPCAMatchedTPConfig)
    repair: RDPRepairConfig = Field(default_factory=RDPRepairConfig)
    cooldown: RDPCooldownConfig = Field(default_factory=RDPCooldownConfig)
    sync_observe: RDPSyncObserveConfig = Field(default_factory=RDPSyncObserveConfig)
    base_size: Decimal = Field(default=Decimal("5"), gt=0, allow_inf_nan=False)
    target_delta: Decimal = Field(default=Decimal("0"), ge=0, le=1, allow_inf_nan=False)
    favorite_threshold: Decimal = Field(default=Decimal("0.70"), gt=Decimal("0.5"),
                                      lt=1, allow_inf_nan=False)
    max_order_size: Decimal = Field(default=Decimal("15"), gt=0, allow_inf_nan=False)
    matched_remaining_tolerance_shares: Decimal = Field(
        default=Decimal("0.1"), ge=0, allow_inf_nan=False,
        description="Inclusive remaining-share tolerance for MATCHED orders only",
    )

    @model_validator(mode="after")
    def _sizing(self) -> RDPSMConfig:
        if self.ca_matched_tp.enabled and (
            self.normal_entry_mode != "ca_v1" or self.repair.policy != "opposite_ba_v1"
        ):
            raise ValueError("ca_matched_tp requires ca_v1 and repair.policy=opposite_ba_v1")
        if self.ca_observe.enabled and self.normal_entry_mode not in {"ba_v1", "ca_v1"}:
            raise ValueError("CA observation requires normal_entry_mode=ba_v1 or ca_v1")
        if self.normal_entry_mode == "ca_v1" and not self.ca_observe.enabled:
            raise ValueError("normal_entry_mode=ca_v1 requires ca_observe.enabled=true")
        if self.repair.policy in {"hd_tp_sl_v1", "opposite_ba_v1"} and self.normal_entry_mode not in {"obi_v1", "ba_v1", "ca_v1"}:
            raise ValueError(f"{self.repair.policy} requires normal_entry_mode=obi_v1, ba_v1 or ca_v1")
        if self.target_delta not in (0, 1):
            raise ValueError("target_delta must be 0 or 1")
        if self.max_order_size < self.base_size:
            raise ValueError("max_order_size must be >= base_size")
        if any(q % Decimal(".01") for q in (self.base_size, self.max_order_size)):
            raise ValueError("base_size and max_order_size must use the SDK 0.01 share grid")
        return self


class PathsConfig(BaseModel):
    db: str = "state.db"
    journal_dir: str = "journal"
    log_dir: str = "logs"


class StrategyProfile(BaseModel):
    """One named parameter set. Every knob the quoter uses lives here."""

    model_config = ConfigDict(extra="forbid")

    # fair value
    micro_levels: int = 3
    flow_ewma_halflife_s: float = 120.0
    # spread / skew
    gamma: float = 0.5
    delta_min_ticks: int = 2
    c_vol: float = 1.2
    c_tox: float = 2.0
    # vol horizons
    vol_short_halflife_s: float = 10.0
    vol_long_halflife_s: float = 900.0
    # sizing / inventory
    base_size_usdc: float = 50.0
    q_max_usdc: float = 500.0
    q_soft_frac: float = 0.6
    layers: int = 2
    layer_step_ticks: int = 2
    # multiplier on the market's reward min-size that reward-eligible orders are
    # bumped to (margin above the scoring floor). 1.5 => 100-share min -> 150.
    reward_size_mult: float = 1.0
    # placement / churn
    reprice_ticks: int = 2
    resize_frac: float = 0.15
    min_edge_ticks: int = 1
    # regime
    event_cooloff_s: float = 60.0
    event_jump_ticks: int = 8
    event_sweep_levels: int = 3
    # sweep = a print >= event_sweep_mult order-sizes AND >= event_sweep_frac of
    # the near-touch depth it consumed (both must hold to flag a toxic sweep)
    event_sweep_mult: float = 4.0
    event_sweep_frac: float = 0.8
    trend_flow_z: float = 1.5
    # short/long realized-vol ratio that trips TRENDING (half size). On a thin
    # book microprice jitter inflates this without real trade flow, so raise it
    # for reward-farming markets that trade rarely.
    trend_vol_ratio: float = 2.0
    # lifecycle
    end_date_taper_days: float = 7.0
    reduce_only_hours: float = 24.0
    halt_before_hours: float = 2.0
    # exits
    exit_urgency_s: float = 900.0
    merge_min_size: float = 20.0

    def with_overrides(self, overrides: dict[str, Any]) -> StrategyProfile:
        """Return a copy with per-market override values applied."""
        if not overrides:
            return self
        data = self.model_dump()
        for k, v in overrides.items():
            if k in data:
                data[k] = v
        return StrategyProfile(**data)


# Keys allowed on a market entry that are NOT profile overrides.
_MARKET_RESERVED = {"slug", "condition_id", "profile", "enabled"}


class MarketEntry(BaseModel):
    """One line of the trade list. Extra keys are treated as profile overrides."""

    model_config = ConfigDict(extra="allow")

    slug: str | None = None
    condition_id: str | None = None
    profile: str = "political-longdated"
    enabled: bool = True

    @model_validator(mode="after")
    def _need_identifier(self) -> MarketEntry:
        if not self.slug and not self.condition_id:
            raise ValueError("market entry needs a slug or condition_id")
        return self

    @property
    def overrides(self) -> dict[str, Any]:
        extra = self.model_extra or {}
        return {k: v for k, v in extra.items() if k not in _MARKET_RESERVED}

    @property
    def ref(self) -> str:
        return self.slug or self.condition_id or "?"


class Secrets(BaseSettings):
    """Loaded from environment / .env. Never written to disk by us."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def __init__(self, **values: Any) -> None:
        env_file = values.get("_env_file", self.model_config.get("env_file"))
        source: dict[str, Any] = dict(dotenv_values(env_file)) if env_file is not None else {}
        source.update(os.environ)
        for legacy, canonical, address in (
            ("PK", "POLYMARKET_PRIVATE_KEY", False),
            ("BROWSER_ADDRESS", "POLYMARKET_WALLET_ADDRESS", True),
        ):
            explicit = {name: values[name] for name in (legacy, canonical) if name in values}
            resolved = _resolve_alias_pair(explicit or source, legacy, canonical, address=address)
            if resolved is not None:
                values[legacy] = resolved
                values.pop(canonical, None)
        super().__init__(**values)

    pk: str = Field(default="", validation_alias=AliasChoices("PK", "POLYMARKET_PRIVATE_KEY"))
    browser_address: str = Field(default="", validation_alias=AliasChoices(
        "BROWSER_ADDRESS", "POLYMARKET_WALLET_ADDRESS"))
    polygon_rpc: str | None = Field(default=None, alias="POLYGON_RPC")
    alert_webhook_url: str | None = Field(default=None, alias="ALERT_WEBHOOK_URL")
    # Polymarket builder API creds (self-generated via L2 auth: clob.create_builder_api_key)
    # + relayer URL — needed to merge a V2 DepositWallet (sig_type 1/3), whose execute()
    # only accepts calls from its factory/relayer. See merge.py.
    builder_key: str | None = Field(default=None, alias="POLY_BUILDER_KEY")
    builder_secret: str | None = Field(default=None, alias="POLY_BUILDER_SECRET")
    builder_passphrase: str | None = Field(default=None, alias="POLY_BUILDER_PASSPHRASE")
    relayer_url: str = Field(default="https://relayer-v2.polymarket.com", alias="POLY_RELAYER_URL")

    @property
    def has_wallet(self) -> bool:
        return bool(self.pk and self.browser_address)

    @property
    def has_builder_creds(self) -> bool:
        return bool(self.builder_key and self.builder_secret and self.builder_passphrase)


class Config(BaseModel):
    """Fully-resolved configuration tree."""

    wallet: WalletConfig = WalletConfig()
    engine: EngineConfig = EngineConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()
    rdp_sm: RDPSMConfig = Field(default_factory=RDPSMConfig)
    paths: PathsConfig = PathsConfig()
    profiles: dict[str, StrategyProfile] = {}
    markets: list[MarketEntry] = []
    secrets: Secrets = Field(default_factory=Secrets)
    config_dir: Path = Path("config")

    @property
    def proxy(self) -> str | None:
        # Standard proxy env var; ALL_PROXY lets you route through an SSH tunnel
        # (e.g. simulate colocation during local testing). httpx and web3 honor
        # it automatically once load_dotenv() has run.
        return os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY")

    @property
    def enabled_markets(self) -> list[MarketEntry]:
        return [m for m in self.markets if m.enabled]

    def profile_for(self, entry: MarketEntry) -> StrategyProfile:
        base = self.profiles.get(entry.profile)
        if base is None:
            raise KeyError(f"unknown strategy profile: {entry.profile!r}")
        return base.with_overrides(entry.overrides)

    @classmethod
    def load(cls, config_dir: str | Path = "config", *, load_env: bool = True) -> Config:
        cdir = Path(config_dir)
        if load_env:
            load_dotenv()
        main = _read_toml(cdir / "config.toml")
        strat = _read_toml(cdir / "strategy.toml")
        mkts = _read_toml(cdir / "markets.toml")

        profiles = {
            name: StrategyProfile(**params)
            for name, params in (strat.get("profiles") or {}).items()
        }
        markets = [MarketEntry(**m) for m in (mkts.get("markets") or [])]

        return cls(
            wallet=WalletConfig(**main.get("wallet", {})),
            engine=EngineConfig(**main.get("engine", {})),
            risk=RiskConfig(**main.get("risk", {})),
            execution=ExecutionConfig(**main.get("execution", {})),
            rdp_sm=RDPSMConfig(**main.get("rdp_sm", {})),
            paths=PathsConfig(**main.get("paths", {})),
            profiles=profiles,
            markets=markets,
            secrets=Secrets(),
            config_dir=cdir,
        )

    def reload_markets(self) -> Config:
        """Re-read markets.toml only (used by the hot-reload path)."""
        mkts = _read_toml(self.config_dir / "markets.toml")
        self.markets = [MarketEntry(**m) for m in (mkts.get("markets") or [])]
        return self


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)
