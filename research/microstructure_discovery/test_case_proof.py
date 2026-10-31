import gzip
import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from seal_crossvenue_ws import seal_and_replay
from verify_crossvenue_case import price_case


def test_fee_bound_survives_fragmentation_but_rejects_expensive_hedge():
    result = price_case()
    assert D(result["normal_profit_after_002_reserve"]) == D(".7538")
    assert D(".49") < D(result["fragmented_profit_after_002_reserve"]) < D(".50")
    assert D(price_case(kp=D(".40"))["normal_profit_after_002_reserve"]) < 0


@pytest.mark.parametrize("compressed", [False, True])
def test_sealed_replay_respects_entry_time_and_excludes_future_prefix(tmp_path, compressed):
    wsroot = tmp_path / "stream"
    wsroot.mkdir()
    initial = dict(
        received=0.0,
        generation=1,
        event=dict(
            event_type="book",
            asset_id="a",
            timestamp="0",
            bids=[dict(price=".68", size="1000")],
            asks=[dict(price=".69", size="1000")],
        ),
    )

    def changed(t):
        return dict(
            received=t,
            generation=1,
            event=dict(
                event_type="price_change",
                timestamp=str(int(t * 1000)),
                price_changes=[
                    dict(
                        asset_id="a",
                        side="SELL",
                        price=".69",
                        size="0",
                        best_bid=".68",
                        best_ask=".80",
                    ),
                    dict(
                        asset_id="a",
                        side="SELL",
                        price=".80",
                        size="1000",
                        best_bid=".68",
                        best_ask=".80",
                    ),
                ],
            ),
        )

    def write(t):
        text = (
            "\n".join(
                json.dumps(r)
                for r in [initial, changed(t), dict(received=30.0, generation=1, event={})]
            )
            + "\n"
        )
        if compressed:
            (wsroot / "raw.jsonl.gz").write_bytes(gzip.compress(text.encode()))
        else:
            (wsroot / "raw.jsonl").write_text(text)

    first = tmp_path / "first"
    first.mkdir()
    write(2.6)
    a = seal_and_replay(wsroot, first, 1.0, "a")
    assert [s["cash_budget_available"] for s in a["samples"]] == [True, True]
    assert a["first_budget_unavailable"]["received"] == 2.6
    assert a["lines"] == 2
    write(2.4)
    assert seal_and_replay(wsroot, first, 1.0, "a")["sha256"] == a["sha256"]
    second = tmp_path / "second"
    second.mkdir()
    b = seal_and_replay(wsroot, second, 1.0, "a")
    assert [s["cash_budget_available"] for s in b["samples"]] == [True, False]
