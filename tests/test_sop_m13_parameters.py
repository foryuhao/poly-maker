"""Source-SOP numeric binding and finite, bounded frequency-only revisions."""

import json
import re
from dataclasses import asdict
from decimal import Decimal as D
from pathlib import Path

import pytest
from sop_signals.m13 import Parameters
from sop_signals.parameters import load_revision, strategy_from_parameters


def doc(**parameters):
    return {"strategy": "M13", "revision_round": 1,
            "hypothesis": "SYNTHETIC_PARAMETER_CONTRACT_CHECK_NOT_A_SEARCH_ROUND",
            "pnl_used_for_selection": False, "parameters": parameters}


def test_m13_original_numbers_bound_to_external_sop_and_common():
    root = Path.cwd()
    docs = root / "research/signals"
    if not docs.exists():
        docs = root / "documents/research/signals"

    def spec(name):
        return json.loads(re.search(r"```json\s*(.*?)\s*```", (docs / name).read_text(), re.S).group(1))

    p, record = load_revision({**doc(), "revision_round": 0})
    sop, common = spec("M13.md"), spec("COMMON.md")
    assert p == Parameters() and strategy_from_parameters(p) == "M13"
    for field, key in (("ttl", "prior_support_ttl_ms"), ("lw_native_ttl", "lw_native_ttl_ms"),
                       ("round_seconds", "new_round_window_ms"),
                       ("formation_after_lw_max", "formation_after_lw_max_ms"),
                       ("post_retest_ttl", "post_retest_ttl_ms"),
                       ("lw_root_hard_ttl", "lw_root_hard_ttl_ms")):
        assert getattr(p, field) * 1000 == sop[key]
    assert [p.ratio_min, p.ratio_max] == [D(v) for v in sop["normalized_hit_ratio"]]
    assert [p.ratio_min, p.ratio_max] == [D(v) for v in sop["normalized_loss_ratio"]]
    assert p.bid_recovery == D(sop["retest_recovery_min"])
    assert p.stable_seconds * 1000 == common["stable_continuous_hold_ms"]
    assert p.stable_recovery == D(common["stable_recovery_min"])
    assert record["strategy"] == "M13" and record["pnl_used_for_selection"] is False


@pytest.mark.parametrize("field,value", [
    ("round_seconds", .25), ("stable_seconds", .05), ("stable_recovery", ".4"),
    ("ttl", 10), ("lw_native_ttl", 1.5), ("formation_after_lw_max", 2),
    ("lw_root_hard_ttl", 8), ("lw_hold_seconds", .05), ("lw_keep_residual_min", ".01"),
])
def test_causal_windows_original_deadlines_and_maintenance_cannot_be_tuned(field, value):
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE"):
        load_revision(doc(**{field: value}))


@pytest.mark.parametrize("parameters", [
    {"bid_recovery": ".49"}, {"ratio_min": 0}, {"ratio_min": 5, "ratio_max": 4},
    {"lw_residual_min": ".09"}, {"lw_residual_loss_min": ".24"},
    {"lw_loss_min": ".1", "lw_residual_min": ".2"},
])
def test_tuning_may_not_remove_real_hit_stability_or_residual_meaning(parameters):
    with pytest.raises(ValueError, match="SEMANTICS"):
        load_revision(doc(**parameters))


def test_baseline_must_be_original_and_no_pnl_or_fourth_revision():
    for document in ({**doc(ratio_min=".1"), "revision_round": 0},
                     {**doc(), "revision_round": 4}, {**doc(), "pnl_used_for_selection": True}):
        with pytest.raises(ValueError):
            load_revision(document)
    base, _ = load_revision({**doc(), "revision_round": 0})
    _, revision = load_revision(doc(ratio_min=".1", ratio_max="10"))
    assert revision["parameters"]["ratio_min"] == "0.1"
    assert {k: str(v) if isinstance(v, D) else v for k, v in asdict(base).items() if k not in {"ratio_min", "ratio_max"}} == {
        k: v for k, v in revision["parameters"].items() if k not in {"ratio_min", "ratio_max"}}


def test_third_revision_quarter_round_requires_explicit_version_and_keeps_original_stability():
    document = {**doc(round_seconds=.25), "revision_round": 3,
                "explicit_short_round_candidate": "M13_250MS_KEEP_100MS_HALF_RECOVERY_R3"}
    p, record = load_revision(document)
    assert p.round_seconds == .25 and p.stable_seconds == .1 and p.stable_recovery == D(".50")
    assert p.lw_native_ttl == .75 and p.formation_after_lw_max == 1.25 and p.lw_root_hard_ttl == 3
    assert record["fixed_causal_rules"]["round_seconds"] == .25
    assert record["pnl_used_for_selection"] is False


@pytest.mark.parametrize("override", [
    {"revision_round": 0}, {"revision_round": 2},
    {"parameters": {"round_seconds": .125}},
    {"parameters": {"round_seconds": .25, "stable_seconds": .05}},
])
def test_short_round_exception_does_not_allow_other_rounds_or_remove_stability(override):
    document = {**doc(round_seconds=.25), "revision_round": 3,
                "explicit_short_round_candidate": "M13_250MS_KEEP_100MS_HALF_RECOVERY_R3", **override}
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE"):
        load_revision(document)
