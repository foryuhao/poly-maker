import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ws_gate import Gate


def initial(price=".79", size="100"):
    return dict(
        event_type="book",
        asset_id="a",
        timestamp="100000",
        bids=[dict(price=".78", size="100")],
        asks=[dict(price=price, size=size)],
    )


def test_rejects_rest_cap_already_removed_on_ws_or_insufficient_current_cash():
    gate = Gate(["a"])
    gate.connected = True
    spec = dict(poly_budget=8.07, poly_cap=0.79, rate=0.05)
    gate.update(initial(), 100)
    assert gate.check("a", spec, 100.1)["eligible"]
    gate.update(initial(".82"), 100.1)
    assert not gate.check("a", spec, 100.2)["eligible"]
    gate.update(initial(size="1"), 100.2)
    assert not gate.check("a", spec, 100.3)["eligible"]
    gate.update(initial(".77"), 100.3)
    assert not gate.check("a", spec, 100.4)["eligible"]


def test_disconnect_generation_or_stale_stream_cannot_credit_cached_quote():
    gate = Gate(["a"])
    gate.connected = True
    gate.update(initial(), 100)
    spec = dict(poly_budget=8.07, poly_cap=0.79, rate=0.05)
    assert not gate.check("a", spec, 101)["eligible"]
    gate.connected = False
    assert not gate.check("a", spec, 100.1)["eligible"]
    gate.reset()
    gate.connected = True
    gate.last_frame = 100.1
    assert not gate.check("a", spec, 100.2)["eligible"]


def test_reported_bbo_mismatch_stays_invalid_until_full_book_resynchronizes():
    gate = Gate(["a"])
    gate.connected = True
    gate.update(initial(), 100)
    spec = dict(poly_budget=8.07, poly_cap=0.79, rate=0.05)

    def change(best):
        return dict(
            event_type="price_change",
            timestamp="100100",
            price_changes=[
                dict(
                    asset_id="a",
                    side="SELL",
                    price=".79",
                    size="100",
                    best_bid=".78",
                    best_ask=best,
                )
            ],
        )

    gate.update(change(".90"), 100.1)
    assert not gate.check("a", spec, 100.2)["eligible"]
    gate.update(change(".79"), 100.2)
    assert not gate.check("a", spec, 100.3)["eligible"]
    gate.update(dict(initial(), timestamp="100200"), 100.3)
    assert gate.check("a", spec, 100.4)["eligible"]
