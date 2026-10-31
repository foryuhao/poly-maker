"""Checks for economic signs, missing horizons, duplicate handling, L2 updates."""

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("study", Path(__file__).with_name("study.py"))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def test_book_absolute_sizes_and_crossed_quotes():
    b = study.Book()
    b.update(
        dict(
            event_type="book",
            bids=[dict(price=".4", size="10")],
            asks=[dict(price=".5", size="20")],
        )
    )
    b.update(dict(event_type="price_change", side="BUY", price=".4", size="7"))
    assert b.quote()["bid_depth"] == 7
    b.update(dict(event_type="price_change", side="BUY", price=".6", size="1"))
    assert b.quote() is None
    b.update(dict(event_type="price_change", side="BUY", price=".6", size="0"))
    assert b.quote()["bid"] == 0.4


def test_markout_sign_dedup_maturity_and_generation(tmp_path):
    meta = [dict(asset="a", market="m", question="Q", outcome="Y", category="test", tick=0.01)]
    (tmp_path / "markets.json").write_text(json.dumps(meta))
    pre = dict(bid=0.4, ask=0.42, mid=0.41, spread=0.02, bid_depth=100, ask_depth=100)
    trade = dict(
        received=1000,
        timestamp="1000000",
        generation=1,
        asset_id="a",
        pre=pre,
        side="BUY",
        price=".42",
        size="10",
    )
    # BUY raises mid by .04 at 5s; it fully reverts by 120s.
    qs = [
        dict(
            received=t,
            generation=g,
            asset="a",
            **dict(pre, mid=mid, bid=mid - 0.01, ask=mid + 0.01),
        )
        for t, mid, g in [(1005, 0.45, 1), (1030, 0.44, 2), (1120, 0.40, 1)]
    ]
    (tmp_path / "quotes.jsonl").write_text("".join(json.dumps(q) + "\n" for q in qs))
    (tmp_path / "trades.jsonl").write_text((json.dumps(trade) + "\n") * 2)
    (tmp_path / "discovery_raw.json").write_text("[]")
    (tmp_path / "raw.jsonl").write_text("")
    study.analyze(tmp_path)
    e = json.loads((tmp_path / "events.jsonl").read_text())
    assert abs(e["impact_5"] - 0.04) < 1e-8
    assert abs(e["impact_120"] + 0.01) < 1e-8
    assert "impact_30" not in e  # Never bridge a reconnect.
    assert e["fade_gross_120"] < 0  # Midpoint reversal alone is not tradable profit.
    quality = json.loads((tmp_path / "quality.json").read_text())
    assert quality["duplicate_signature"] == 1
    assert quality["unique_accepted"] == 1


def test_sell_sign_and_missing_future(tmp_path):
    meta = [dict(asset="a", market="m", question="Q", outcome="Y", category="test", tick=0.01)]
    (tmp_path / "markets.json").write_text(json.dumps(meta))
    pre = dict(bid=0.4, ask=0.42, mid=0.41, spread=0.02, bid_depth=100, ask_depth=100)
    t = dict(
        received=1000,
        timestamp="1000000",
        generation=1,
        asset_id="a",
        pre=pre,
        side="SELL",
        price=".40",
        size="10",
    )
    future = dict(received=1005, generation=1, asset="a", **dict(pre, mid=0.38, bid=0.37, ask=0.39))
    (tmp_path / "quotes.jsonl").write_text(json.dumps(future) + "\n")
    (tmp_path / "trades.jsonl").write_text(json.dumps(t) + "\n")
    (tmp_path / "discovery_raw.json").write_text("[]")
    (tmp_path / "raw.jsonl").write_text("")
    study.analyze(tmp_path)
    e = json.loads((tmp_path / "events.jsonl").read_text())
    assert abs(e["impact_5"] - 0.03) < 1e-8
    assert abs(e["fade_gross_5"] + 0.05) < 1e-8
    assert "impact_120" not in e  # No forward-fill extrapolation.
    assert json.loads((tmp_path / "summary.json").read_text())[0]["mature_120"] == 0
