import gzip
import json
from copy import deepcopy

import pytest
from m1223_replay import (
    file_hash,
    replay_initial_event,
    replay_market,
    validate_dataset,
    verify_source,
)
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m12 import Parameters
from sop_signals.m14 import Parameters as M14Parameters
from sop_signals.parameters import load_revision

from m1234_fixtures import fixture_events
from polymaker.m1234_sim.events import encode
from polymaker.m1234_sim.report import read_state


def dataset():
    return {
        "schema": "m12-predeclared-development-dataset-v1",
        "sample_role": "DEVELOPMENT_ONLY",
        "denominator": 24,
        "markets": [
            {"market": f"btc-updown-5m-{e}", "source_validity": "VALID"}
            for e in range(1800000000, 1800000000 + 24 * 300, 300)
        ],
    }


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "out_of_order", "invalid", "wrong_role"]
)
def test_replay_rejects_subset_resampling_or_independent_validation_as_development(change):
    d = dataset()
    if change == "missing":
        d["markets"].pop()
    elif change == "duplicate":
        d["markets"][-1] = d["markets"][0]
    elif change == "out_of_order":
        d["markets"].reverse()
    elif change == "invalid":
        d["markets"][3]["source_validity"] = "INVALID"
    else:
        d["sample_role"] = "INDEPENDENT_VALIDATION"
    with pytest.raises(ValueError):
        validate_dataset(d)


def m14_revision_fixture():
    baseline, source_revision = load_revision({
        "strategy": "M14", "revision_round": 0, "parameters": {}, "pnl_used_for_selection": False})
    parameters, revision = load_revision({
        "strategy": "M14", "revision_round": 1, "parameters": {"ask_loss": "0.02"},
        "hypothesis": "SYNTHETIC_METADATA_BINDING_TEST_NOT_FREQUENCY_SELECTION",
        "pnl_used_for_selection": False})
    initial = deepcopy(fixture_events()[0])
    initial['definition']['settings'].update(sop=definition(baseline), sop_parameter_revision=source_revision)
    return initial, parameters, revision, source_revision


def test_native_replay_revision_matches_selected_values_and_preserves_original_source_label():
    initial, parameters, revision, source_revision = m14_revision_fixture()
    original = deepcopy(initial)
    replayed = replay_initial_event(initial, parameters, {'mode': 'SYNTHETIC_TEST_ONLY'}, revision)
    settings = replayed['definition']['settings']
    assert settings['sop_parameter_revision'] == revision
    assert settings['sop']['parameter_sha256'] == revision['parameter_sha256']
    assert settings['sop_replay']['source_sop_parameter_revision'] == source_revision
    assert initial == original and settings['sop_parameter_revision']['revision_round'] == 1
    assert isinstance(parameters, M14Parameters)


def test_changed_native_replay_values_cannot_keep_implicit_baseline_revision():
    initial, parameters, _, _ = m14_revision_fixture()
    with pytest.raises(ValueError, match='REQUIRE_EXPLICIT_REVISION'):
        replay_initial_event(initial, parameters, {})


@pytest.mark.parametrize('changed', ['strategy', 'parameter_sha256', 'parameters', 'revision_round',
                                    'pnl_used_for_selection'])
def test_native_replay_rejects_mismatched_parameter_revision(changed):
    initial, parameters, revision, _ = m14_revision_fixture()
    if changed == 'strategy':
        revision[changed] = 'M13'
    elif changed == 'parameter_sha256':
        revision[changed] = 'old-baseline-identity'
    elif changed == 'parameters':
        revision[changed] = {**revision[changed], 'ask_loss': '0.05'}
    elif changed == 'revision_round':
        revision[changed] = 4
    else:
        revision[changed] = True
    with pytest.raises(ValueError, match='NATIVE_PARAMETER_REVISION_DIFFERS'):
        replay_initial_event(initial, parameters, {}, revision)


@pytest.mark.asyncio
async def test_raw_replay_preserves_economics_and_detects_changed_input(tmp_path):
    tape = fixture_events()
    tape[0]["definition"]["settings"].update(
        profiles={},
        sop=definition(),
        sop_execution=execution_configuration(),
        scenarios={
            "trade-through": {
                "post_ms": 100,
                "ack_ms": 200,
                "cancel_ms": 200,
                "ahead_cancel_fraction": "0",
            }
        },
    )
    source = tmp_path / "source"
    e = SopEngine(source)
    for event in tape:
        await e.ingest(deepcopy(event))
    await e.close()
    raw = source / "input.jsonl.gz"
    with gzip.open(raw, "wt") as f:
        for event in tape:
            f.write(encode(event) + "\n")
    record = {
        "market": e.meta.slug,
        "source": str(source),
        "source_input": str(raw),
        "source_input_sha256": file_hash(raw),
        "source_database_sha256": file_hash(source / "simulation.sqlite3"),
        "snapshot_input_seq": e.seq,
        "recorded_eligibility": {"status": "ELIGIBLE", "reason": "SYNTHETIC_TEST_ONLY"},
    }
    result = await replay_market(
        record,
        tmp_path / "replayed",
        Parameters(),
        {"mode": "SYNTHETIC_TEST_ONLY"},
        {"scope": "SYNTHETIC_TEST_ONLY"},
    )
    assert result["input_seq"] == e.seq and result["validity"] == "VALID"
    before, after = read_state(e.store.path), read_state(tmp_path / "replayed/simulation.sqlite3")
    assert before["brokers"] == after["brokers"]
    assert before["sop_signal_history"] == after["sop_signal_history"]
    binding = json.loads((tmp_path / "replayed/replay-source.json").read_text())
    assert binding["requires_retained_original_input"] and not binding["input_copied"]
    raw.write_bytes(raw.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="SOURCE_INPUT_MISSING_OR_CHANGED"):
        verify_source(record)
