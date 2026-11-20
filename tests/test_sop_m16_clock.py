"""Actual reviewed receiver/recorder proof, replay metadata and negative cases."""

from types import SimpleNamespace
import json
import time

import pytest

from polymaker.m1234_sim.events import encode
from polymaker.m1234_sim.runner import CaptureFeed
from polymaker.rdp_sm_marketdata import SessionFeed
from sop_signals.m16_clock import capture_clock_evidence, check_clock_evidence, roundtrip_preserves_floats


def proof():
    return capture_clock_evidence(receiver_class=SessionFeed, capture_class=CaptureFeed, encoder=encode)


def test_actual_capture_chain_records_a_verified_local_quantum_envelope():
    p = proof()
    assert p["status"] == "LOCAL_QUANTIZATION_PROVEN", p
    r = check_clock_evidence(p, received=time.monotonic())
    assert r["status"] == "LOCAL_QUANTIZATION_PROVEN" and 0 < r["quantum"] <= .001
    # Metadata survives the same JSON path used by native INIT/settings.
    assert check_clock_evidence(json.loads(encode(p))) == check_clock_evidence(p)
    assert p["clock_padding_quanta"] == 2
    assert "NOT_EXCHANGE_OR_NETWORK" in p["scope"]


@pytest.mark.parametrize("change", [
    {"status": "UNKNOWN"}, {"source_hashes": {}}, {"serialization_verified": False},
    {"clock_padding_quanta": 0}, {"receiver_quantum_seconds": "0"},
    {"receiver_quantum_seconds": "NaN"}, {"float_ulp_upper": "0"},
    {"valid_received_max": "Infinity"}, {"serialization_samples": True},
])
def test_changed_or_missing_metadata_is_not_a_zero_padding_default(change):
    p = proof()
    p.update(change)
    r = check_clock_evidence(p)
    assert r["status"] == "TIMING_UNAVAILABLE" and r["quantum"] is None


def test_replay_uses_original_bound_and_does_not_substitute_current_clock():
    p = proof()
    lower, upper = float(p["valid_received_min"]), float(p["valid_received_max"])
    assert check_clock_evidence(p, received=lower)["status"] == "LOCAL_QUANTIZATION_PROVEN"
    assert check_clock_evidence(p, received=upper)["status"] == "LOCAL_QUANTIZATION_PROVEN"
    assert check_clock_evidence(p, received=upper + 1)["reason"] == "RECEIVED_OUTSIDE_CLOCK_PROOF_ENVELOPE"
    assert check_clock_evidence(None)["quantum"] is None


@pytest.mark.parametrize("fields", [dict(monotonic=False), dict(adjustable=True),
                                    dict(resolution=.002), dict(implementation="OTHER_CLOCK")])
def test_insufficient_or_different_clock_capability_is_unavailable(monkeypatch, fields):
    info = time.get_clock_info("monotonic")
    values = dict(implementation=info.implementation, resolution=info.resolution,
                  monotonic=info.monotonic, adjustable=info.adjustable)
    values.update(fields)
    monkeypatch.setattr(time, "get_clock_info", lambda _: SimpleNamespace(**values))
    p = proof()
    assert p["status"] == "TIMING_UNAVAILABLE"
    assert check_clock_evidence(p)["quantum"] is None


def test_source_or_provider_changes_need_a_new_review():
    class OtherReceiver:
        def __init__(self, *, monotonic=time.monotonic):
            pass
    p = capture_clock_evidence(receiver_class=OtherReceiver, capture_class=CaptureFeed, encoder=encode)
    assert p["status"] == "TIMING_UNAVAILABLE" and p["reason"] == "RECEIVER_PROVIDER_NOT_REVIEWED"


def test_float_digits_alone_are_not_a_recording_precision_test():
    samples = [123456.123456789, 123456.987654321]
    assert roundtrip_preserves_floats(encode, samples)
    assert not roundtrip_preserves_floats(lambda _: json.dumps({"received": [123456.123, 123456.987]}), samples)
    assert not roundtrip_preserves_floats(lambda _: json.dumps({"received": [123456, 123457]}), samples)
