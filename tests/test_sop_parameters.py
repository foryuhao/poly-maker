import json
import re
from pathlib import Path

import pytest
from sop_signals.m12 import Parameters, parameter_identity, signal_version
from sop_signals.parameters import load_revision


def document(**changes):
    return {
        "strategy": "M12",
        "revision_round": 1,
        "hypothesis": "SYNTHETIC_CONTRACT_TEST_ONLY",
        "pnl_used_for_selection": False,
        "parameters": changes,
    }


@pytest.mark.parametrize(
    "name,value",
    [("stable_seconds", 0.01), ("stable_recovery", ".40"), ("round_seconds", 0.3), ("ttl", 8.0)],
)
def test_formation_hold_window_and_original_deadline_are_not_frequency_knobs(name, value):
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE"):
        load_revision(document(**{name: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"ask_recovery": ".50"},
        {"bid_recovery": ".49"},
        {"ratio_min": "5", "ratio_max": "4"},
        {"ask_loss": "0"},
        {"pair_gap": 3.0},
    ],
)
def test_revisions_keep_weak_ask_and_stable_bid_semantics(changes):
    with pytest.raises(ValueError, match="SEMANTICS"):
        load_revision(document(**changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision_round", 4),
        ("revision_round", True),
        ("hypothesis", ""),
        ("pnl_used_for_selection", True),
    ],
)
def test_search_limit_hypothesis_and_no_pnl_selection_are_explicit(field, value):
    d = document()
    d[field] = value
    with pytest.raises(ValueError):
        load_revision(d)


def test_baseline_is_exact_sop_and_each_revision_has_a_distinct_identity():
    baseline, base = load_revision({**document(), "revision_round": 0})
    changed, record = load_revision(document(ask_loss=".04"))
    assert baseline == Parameters()
    assert base["parameter_sha256"] != record["parameter_sha256"]
    assert signal_version(baseline) != signal_version(changed)
    assert record["fixed_causal_rules"] == base["fixed_causal_rules"]


def test_baseline_numbers_match_external_authoritative_sop_document():
    # Tests may run from either the workspace or a frozen source bundle.
    root = Path.cwd()
    sop = root / "research/signals/M12.md"
    if not sop.exists():
        sop = root / "documents/research/signals/M12.md"
    spec = json.loads(re.search(r"```json\s*(.*?)\s*```", sop.read_text(), re.S).group(1))
    p = Parameters()
    assert p.round_seconds * 1000 == spec["round_window_ms"]
    assert p.pair_gap * 1000 == spec["pair_confirmation_gap_max_ms"]
    assert p.ttl * 1000 == spec["component_ttl_ms"]
    values, _ = parameter_identity(p)
    assert values["bid_recovery"] == spec["bid_recovery_min"]
    assert values["ask_recovery"] == spec["ask_recovery_max"]
    assert values["ask_loss"] == spec["ask_loss_fraction_min"]
