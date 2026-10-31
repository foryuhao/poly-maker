import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import execution_study as execution
from study import Book


def test_vwap_capacity_and_sign():
    b = Book()
    b.update(
        dict(
            event_type="book",
            bids=[dict(price=".40", size="2"), dict(price=".39", size="5")],
            asks=[dict(price=".42", size="2"), dict(price=".43", size="5")],
        )
    )
    assert abs(execution.vwap(b, "BUY") - 0.426) < 1e-8
    assert abs(execution.vwap(b, "SELL") - 0.394) < 1e-8
    assert execution.vwap(b, "BUY", 100) is None


def test_signal_canonical_side_and_spacing(tmp_path):
    meta = [
        dict(asset="a", market="m", question="Q", outcome="Y"),
        dict(asset="b", market="m", question="Q", outcome="N"),
    ]
    (tmp_path / "markets.json").write_text(json.dumps(meta))
    pre = dict(bid=0.4, ask=0.41, mid=0.405, spread=0.01, bid_depth=900, ask_depth=100)

    def trade(asset, t, side):
        return dict(
            asset_id=asset,
            received=t,
            timestamp=str(int(t * 1000)),
            side=side,
            price=".4",
            size="200",
            pre=pre,
            generation=1,
        )

    ts = [
        trade("a", 1000, "SELL"),
        trade("b", 1000, "BUY"),
        trade("a", 1050, "BUY"),
        trade("a", 1121, "BUY"),
    ]
    (tmp_path / "trades.jsonl").write_text("".join(json.dumps(t) + "\n" for t in ts))
    ss = execution.signals(tmp_path)
    assert all(s["time"] != 1050 for s in ss)
    assert all(s["time"] in (1000, 1121) for s in ss)
    fade = next(s for s in ss if s["rule"] == "fade_imbalance")
    assert fade["asset"] == "a"  # Fade sell by buying primary, never naked short.
    follow = next(s for s in ss if s["rule"] == "follow_large_flow" and s["time"] == 1000)
    assert follow["asset"] == "b"


def test_failed_exit_is_loss_not_dropped(tmp_path):
    s = dict(
        id=0,
        time=1000,
        generation=1,
        asset="a",
        market="m",
        question="Q",
        outcome="Y",
        rule="follow_large_flow",
    )
    events = [
        dict(
            received=999.5,
            generation=1,
            event=dict(
                event_type="book",
                asset_id="a",
                bids=[dict(price=".4", size="100")],
                asks=[dict(price=".41", size="100")],
            ),
        ),
        dict(received=1002, generation=1, event={}),
        dict(received=1030.5, generation=2, event={}),
        dict(received=1032, generation=2, event={}),
        dict(received=1122, generation=2, event={}),
    ]
    (tmp_path / "raw.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    rows, checks = execution.replay(tmp_path, [s], 1122)
    assert rows
    assert all(r["net_per_share"] < -0.4 for r in rows)
    assert checks["exit_zero_recovery"] == len(rows)


def test_execution_never_uses_next_received_book(tmp_path):
    s = dict(
        id=0,
        time=1000,
        generation=1,
        asset="a",
        market="m",
        question="Q",
        outcome="Y",
        rule="follow_large_flow",
    )

    def book(t, bid, ask):
        return dict(
            received=t,
            generation=1,
            event=dict(
                event_type="book",
                asset_id="a",
                bids=[dict(price=str(bid), size="100")],
                asks=[dict(price=str(ask), size="100")],
            ),
        )

    events = [
        book(999.5, 0.4, 0.41),
        dict(received=1000, generation=1, event={}),
        book(1001, 0.7, 0.71),
        dict(received=1002, generation=1, event={}),
        book(1031, 0.3, 0.31),
        dict(received=1032, generation=1, event={}),
        dict(received=1122, generation=1, event={}),
    ]
    (tmp_path / "raw.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    rows, _ = execution.replay(tmp_path, [s], 1122)
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["entry"]["price"] == 0.41  # Quote change at 1001 is not known at target 1000.5.
    assert r["exit"]["price"] == 0.7  # Quote change at 1031 is not known at target 1030.5.


def test_mismatched_bbo_blocks_entry_until_full_snapshot(tmp_path):
    s = dict(
        id=0,
        time=1000,
        generation=1,
        asset="a",
        market="m",
        question="Q",
        outcome="Y",
        rule="follow_large_flow",
    )
    initial = dict(
        received=999,
        generation=1,
        event=dict(
            event_type="book",
            asset_id="a",
            timestamp="999000",
            bids=[dict(price=".4", size="100")],
            asks=[dict(price=".41", size="100")],
        ),
    )
    inconsistent = dict(
        received=999.5,
        generation=1,
        event=dict(
            event_type="price_change",
            timestamp="999500",
            price_changes=[
                dict(
                    asset_id="a", side="BUY", price=".39", size="5", best_bid=".39", best_ask=".41"
                )
            ],
        ),
    )
    events = [
        initial,
        inconsistent,
        dict(received=1000, generation=1, event={}),
        dict(received=1002, generation=1, event={}),
        dict(received=1122, generation=1, event={}),
    ]
    (tmp_path / "raw.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    rows, checks = execution.replay(tmp_path, [s], 1122)
    assert rows == []
    assert checks["no_entry"] == 6


def test_source_regression_does_not_recover_on_matching_bbo(tmp_path):
    s = dict(
        id=0,
        time=1000,
        generation=1,
        asset="a",
        market="m",
        question="Q",
        outcome="Y",
        rule="follow_large_flow",
    )

    def change(t, source):
        return dict(
            received=t,
            generation=1,
            event=dict(
                event_type="price_change",
                timestamp=str(source),
                price_changes=[
                    dict(
                        asset_id="a",
                        side="BUY",
                        price=".4",
                        size="100",
                        best_bid=".4",
                        best_ask=".41",
                    )
                ],
            ),
        )

    events = [
        dict(
            received=999,
            generation=1,
            event=dict(
                event_type="book",
                asset_id="a",
                timestamp="999000",
                bids=[dict(price=".4", size="100")],
                asks=[dict(price=".41", size="100")],
            ),
        ),
        change(999.5, 998000),
        change(999.8, 999800),
        dict(received=1000, generation=1, event={}),
        dict(received=1002, generation=1, event={}),
        dict(received=1122, generation=1, event={}),
    ]
    (tmp_path / "raw.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    rows, checks = execution.replay(tmp_path, [s], 1122)
    assert rows == []
    assert checks["source_regression"] == 1
