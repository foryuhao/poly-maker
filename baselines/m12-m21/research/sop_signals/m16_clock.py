"""Attest the reviewed public receiver/recorder's local quantization contract.

The OS clock API describes local resolution, not network latency, scheduling
delay, timestamp accuracy at the exchange, or freedom from upstream batching.
Only the reviewed chain is accepted; changed source requires a fresh review.
"""

import hashlib
import inspect
import json
import math
import os
import platform
import time
from pathlib import Path

from .m16_timing import QUANTUM_MAX, number

VERSION = "m16-local-receiver-quantization-v1"
AUDITED_SOURCES = {
    "receiver": "3c9c88a5173a39976f834c5752f6c762d7ab49c576ecfe75a3c05b0df6068e9e",
    "capture": "78ef358b2951bda9991fd4b6ee574413e8bb76630431383bd4914a2c2abe45ef",
    "recorder": "ad98f44239c39fa5f6e73a7e75d42c23ef4d6605a8069f2ec3eb0e8983e716e4",
}
HORIZON_SECONDS = 172800  # Evidence envelope, not a changed runtime limit.


def roundtrip_preserves_floats(encoder, samples):
    try:
        decoded = json.loads(encoder({"received": samples}))["received"]
        return len(decoded) == len(samples) and all(
            type(b) is float and math.isfinite(b) and a.hex() == b.hex()
            for a, b in zip(samples, decoded)
        )
    except (TypeError, ValueError, KeyError):
        return False


def capture_clock_evidence(*, receiver_class, capture_class, encoder):
    """Run in the capture process before INIT; persist result in settings.

    receiver_class must be the original SessionFeed. Diagnostics subclasses may
    wrap CaptureFeed, but its original implementation must remain in the MRO.
    Raw FRAME received_mono is not re-timestamped at processing or recording.
    """
    proof = {"version": VERSION, "status": "TIMING_UNAVAILABLE", "reason": None,
             "scope": "LOCAL_RECEIVER_QUANTIZATION_ONLY_NOT_EXCHANGE_OR_NETWORK_DELAY",
             "process_id": os.getpid(), "platform": platform.platform(),
             "receiver": "time.monotonic", "horizon_seconds": HORIZON_SECONDS,
             "source_hashes": {}, "source_paths": {}}
    try:
        original_capture = next(c for c in capture_class.__mro__
                                if c.__name__ == "CaptureFeed"
                                and c.__module__ == "polymaker.m1234_sim.runner")
        if (receiver_class.__name__ != "SessionFeed"
                or receiver_class.__module__ != "polymaker.rdp_sm_marketdata"
                or inspect.signature(receiver_class.__init__).parameters["monotonic"].default
                is not time.monotonic):
            raise ValueError("RECEIVER_PROVIDER_NOT_REVIEWED")
        for name, obj in (("receiver", receiver_class), ("capture", original_capture),
                          ("recorder", encoder)):
            path = Path(inspect.getsourcefile(obj))
            proof["source_paths"][name] = str(path)
            proof["source_hashes"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if proof["source_hashes"] != AUDITED_SOURCES:
            raise ValueError("RECEIVER_OR_RECORDER_SOURCE_REVIEW_REQUIRED")
        info = time.get_clock_info("monotonic")
        proof["clock_info"] = dict(implementation=info.implementation, resolution=info.resolution,
                                   monotonic=info.monotonic, adjustable=info.adjustable)
        if (not info.monotonic or info.adjustable
                or info.implementation != "clock_gettime(CLOCK_MONOTONIC)"
                or not 0 < number(info.resolution) <= QUANTUM_MAX):
            raise ValueError("LOCAL_CLOCK_RESOLUTION_NOT_PROVEN")
        samples = [time.monotonic() for _ in range(16)]
        if not all(math.isfinite(x) and x >= 0 for x in samples) or samples != sorted(samples):
            raise ValueError("LOCAL_CLOCK_SAMPLES_INVALID")
        if not roundtrip_preserves_floats(encoder, samples):
            raise ValueError("RECEIVED_FLOAT_RECORDING_NOT_LOSSLESS")
        upper = samples[-1] + HORIZON_SECONDS
        # One full binary64 ULP is conservative for float representation;
        # decimal JSON is tested to round-trip the exact binary64 values.
        quantum = number(info.resolution) + number(math.ulp(upper))
        if quantum > QUANTUM_MAX:
            raise ValueError("LOCAL_CLOCK_FLOAT_ENVELOPE_OVER_1MS")
        proof.update(status="LOCAL_QUANTIZATION_PROVEN", clock_padding_quanta=2,
                     serialization="PYTHON_JSON_BINARY64_EXACT_ROUNDTRIP",
                     serialization_samples=len(samples), serialization_verified=True,
                     valid_received_min=str(number(samples[0])), valid_received_max=str(number(upper)),
                     float_ulp_upper=str(number(math.ulp(upper))),
                     receiver_quantum_seconds=str(quantum))
    except (OSError, StopIteration, TypeError, ValueError, KeyError, ArithmeticError) as error:
        proof["reason"] = str(error) or type(error).__name__
    return proof


def check_clock_evidence(proof, *, received=None):
    """Validate stored metadata on replay; never substitute today's clock.

    This is metadata consistency checking. Its provenance comes from the frozen
    run and original input manifest, not from trusting an arbitrary JSON flag.
    """
    try:
        if not isinstance(proof, dict) or proof.get("version") != VERSION:
            raise ValueError("RECEIVER_CLOCK_EVIDENCE_MISSING")
        if (proof.get("status") != "LOCAL_QUANTIZATION_PROVEN"
                or proof.get("source_hashes") != AUDITED_SOURCES
                or proof.get("receiver") != "time.monotonic"
                or proof.get("clock_padding_quanta") != 2
                or proof.get("serialization_verified") is not True
                or proof.get("serialization") != "PYTHON_JSON_BINARY64_EXACT_ROUNDTRIP"
                or type(proof.get("serialization_samples")) is not int
                or proof["serialization_samples"] < 1
                or proof.get("horizon_seconds") != HORIZON_SECONDS):
            raise ValueError("RECEIVER_CLOCK_EVIDENCE_NOT_PROVEN")
        info = proof["clock_info"]
        resolution = number(info["resolution"])
        if (info.get("monotonic") is not True or info.get("adjustable") is not False
                or info.get("implementation") != "clock_gettime(CLOCK_MONOTONIC)"
                or not 0 < resolution <= QUANTUM_MAX):
            raise ValueError("LOCAL_CLOCK_RESOLUTION_NOT_PROVEN")
        lower, upper = number(proof["valid_received_min"]), number(proof["valid_received_max"])
        ulp = number(proof["float_ulp_upper"])
        quantum = number(proof["receiver_quantum_seconds"])
        if (not 0 <= lower < upper or upper - lower > HORIZON_SECONDS + 1
                or not math.isfinite(float(upper))
                or ulp != number(math.ulp(float(upper))) or quantum != resolution + ulp
                or not 0 < quantum <= QUANTUM_MAX):
            raise ValueError("LOCAL_CLOCK_QUANTIZATION_ENVELOPE_INVALID")
        if received is not None and not lower <= number(received) <= upper:
            raise ValueError("RECEIVED_OUTSIDE_CLOCK_PROOF_ENVELOPE")
        return {"status": "LOCAL_QUANTIZATION_PROVEN", "reason": None, "quantum": quantum}
    except (TypeError, ValueError, KeyError, ArithmeticError) as error:
        return {"status": "TIMING_UNAVAILABLE", "reason": str(error), "quantum": None}
