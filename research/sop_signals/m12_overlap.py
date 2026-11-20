"""Explicit M12_VALIDITY_OVERLAP; historical M12 remains a separate contract."""

from dataclasses import asdict, dataclass
from decimal import Decimal as D

from .m12 import M12, Parameters as BaselineParameters, parameter_identity

VARIANT = "M12_VALIDITY_OVERLAP"
VERSION = "m12-live-support-weak-ask-overlap-v1/raw-path-v1"
SOP_FILE = "V_M12_VALIDITY_OVERLAP.md"
SOP_SHA256 = "99c100e5d03e51f648a62f63090f5b45cbc38e31864086bac2c76effccf7b1e0"
DOCUMENT_PARAMETERS_SHA256 = "dec8201b4b49bd91de9347c79aaeed8ef13c87d8a14a0f6813942e9e75d37c7e"


@dataclass(frozen=True)
class Parameters(BaselineParameters):
    ask_recovery: D = D(".40")
    ask_loss: D = D(".02")
    ratio_min: D = D(".10")
    ratio_max: D = D("10")
    pair_gap: None = None
    variant_id: str = VARIANT


def signal_version(parameters):
    return VERSION + "/p-" + parameter_identity(parameters)[1][:12]


class M12Overlap(M12):
    def __init__(self, emit, parameters=None):
        parameters = parameters or Parameters()
        if not isinstance(parameters, Parameters) or parameters.variant_id != VARIANT:
            raise ValueError("EXPLICIT_M12_OVERLAP_PARAMETERS_REQUIRED")
        super().__init__(emit, parameters)
        self.version = signal_version(self.p)

    def publish(self):
        # Component clocks, guards and subsequent-round counterevidence are
        # inherited unchanged. Neither gap nor cross-side loss is a filter.
        pairs = []
        for a in self.components.values():
            if a.side != "ASK" or a.id in self.used_asks:
                continue
            for b in self.components.values():
                if (b.side != "BID" or b.token != a.token
                        or b.generation != a.generation or b.price >= a.price):
                    continue
                if self.p.ratio_min <= a.u / b.u <= self.p.ratio_max:
                    pairs.append(((max(a.key, b.key), a.key, b.key), a, b))
        for _, a, b in sorted(pairs, key=lambda item: item[0]):
            if a.id in self.used_asks:
                continue
            deadline = min(a.deadline, b.deadline)
            if self.now >= deadline:
                continue
            self.used_asks.add(a.id)
            gap = abs(a.key[0] - b.key[0]) * 1000
            identity = f"{self.strategy}:pair:{a.id}"
            signal = {
                "id": identity, "token": a.token, "root_id": a.id,
                "version": self.version, "variant_id": VARIANT,
                "component_ids": [b.id, a.id], "confirmed": self.now,
                "deadline": deadline, "anchor_price": b.price,
                "bid": asdict(b), "ask": asdict(a), "quality_status": "PROVEN",
                "diagnostics": {
                    "gap_ms": gap, "gap_le_1500ms": gap <= 1500,
                    "first_confirmed_side": ("BID_FIRST" if b.key[0] < a.key[0]
                        else "ASK_FIRST" if a.key[0] < b.key[0] else "SAME_AVAILABLE"),
                    "normalized_hit_ratio": a.u / b.u,
                    "normalized_loss_ratio": a.d / b.d if b.d else None,
                    "bid_age_seconds": self.now - b.key[0],
                    "ask_age_seconds": self.now - a.key[0],
                    "remaining_lifetime_seconds": deadline - self.now,
                },
            }
            self.signals[identity] = signal
            self.event("SIGNAL_CONFIRMED", **signal)
