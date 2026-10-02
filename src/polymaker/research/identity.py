"""Canonical FavoriteFirst configuration identity for one Research session."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

from polymaker.strategy.favorite_first import FavoriteFirstConfig

NORMALIZATION_VERSION = "favorite-first-effective-v1"
_FLOAT_FIELDS = frozenset({
    "max_pair_loss_usd", "normal_reprice_min_s", "urgent_reprice_min_s",
    "first_leg_qty", "entry_start_s", "last_new_entry_s", "favorite_min",
    "favorite_max", "favorite_confirm_s", "first_leg_risk_window_s",
    "trailing_arm_usd", "trailing_drawdown_fraction", "forced_pair_start_s",
    "hard_order_cutoff_s",
})
_INTEGER_FIELD = "runner_passive_offset_ticks"
_SOURCE_FIELDS = (_FLOAT_FIELDS - {"first_leg_risk_window_s"}) | {
    "probation_max_s", "probation_passive_offset_ticks", _INTEGER_FIELD,
}


def normalize_favorite_config(config: FavoriteFirstConfig) -> dict[str, float | int]:
    """Project only active strategy values, including dataclass defaults."""
    if {field.name for field in fields(config)} != _SOURCE_FIELDS:
        raise ValueError("FavoriteFirst config fields changed; update identity contract")
    source = asdict(config)
    source["first_leg_risk_window_s"] = source.pop("probation_max_s")
    source.pop("probation_passive_offset_ticks")
    normalized: dict[str, float | int] = {}
    for name in _FLOAT_FIELDS:
        value = source[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"invalid FavoriteFirst identity field: {name}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"nonfinite FavoriteFirst identity field: {name}")
        normalized[name] = 0.0 if number == 0 else number
    value = source[_INTEGER_FIELD]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid FavoriteFirst identity field: {_INTEGER_FIELD}")
    normalized[_INTEGER_FIELD] = value
    return normalized


def canonical_config_bytes(values: Mapping[str, Any]) -> bytes:
    if set(values) != _FLOAT_FIELDS | {_INTEGER_FIELD}:
        raise ValueError("unexpected FavoriteFirst effective config fields")
    normalized: dict[str, float | int] = {}
    for name in _FLOAT_FIELDS:
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"invalid FavoriteFirst identity field: {name}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"nonfinite FavoriteFirst identity field: {name}")
        normalized[name] = 0.0 if number == 0 else number
    value = values[_INTEGER_FIELD]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid FavoriteFirst identity field: {_INTEGER_FIELD}")
    normalized[_INTEGER_FIELD] = value
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class ResearchConfigIdentity:
    """Immutable session-owned binding to the actual strategy config instance."""

    dataset_id: str
    session_id: str
    strategy_config: FavoriteFirstConfig
    normalized_items: tuple[tuple[str, float | int], ...]
    config_id: str
    normalization_version: str = NORMALIZATION_VERSION

    @property
    def effective_config(self) -> dict[str, float | int]:
        return dict(self.normalized_items)


def research_config_identity(config: FavoriteFirstConfig, *, dataset_id: str,
                             session_id: str) -> ResearchConfigIdentity:
    values = normalize_favorite_config(config)
    digest = hashlib.sha256(canonical_config_bytes(values)).hexdigest()
    return ResearchConfigIdentity(dataset_id, session_id, config,
                                  tuple(sorted(values.items())), digest)


def validate_research_config_identity(identity: ResearchConfigIdentity,
                                      actual_config: FavoriteFirstConfig, *,
                                      dataset_id: str, session_id: str) -> None:
    """Reject a claimed context that does not describe the running strategy."""
    expected = research_config_identity(actual_config, dataset_id=dataset_id,
                                        session_id=session_id)
    if (identity.strategy_config is not actual_config
            or identity.dataset_id != expected.dataset_id
            or identity.session_id != expected.session_id
            or identity.normalization_version != expected.normalization_version
            or identity.normalized_items != expected.normalized_items
            or identity.config_id != expected.config_id):
        raise ValueError("Research identity differs from the effective strategy config")
