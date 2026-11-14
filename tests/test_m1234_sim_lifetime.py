"""Independent hand-computed lifecycle checks and unchanged-baseline raw replay."""

import socket
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from m1234_sim_lifetime import LifetimeEngine, LifetimePolicy
from m1234_sim_lifetime_replay import summarize_market

from m1234_fixtures import fixture_events
from polymaker.m2_config import M2Config
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import digest
from polymaker.m1234_sim.policy import M234Policy
from test_m1234_sim_core import broker, sell
from test_rdp_sm_obi import feed_fixture


def setup(seconds=10, strategy="M2"):
    f, _, _ = feed_fixture()
    b, trace = broker()
    c = {
        "id": "first",
        "strategy": strategy,
        "token": "1",
        "outcome": "YES",
        "price": D(".50"),
        "confirmed": 0.0,
        "deadline": 0.5,
        "generation": 0,
        "root": "root",
    }
    candidates = [c]
    f.generation = 0
    f.m1 = SimpleNamespace(legal=lambda book, now: True)
    f.monotonic = lambda: b.now
    f.m1_watermark = lambda: b.now
    f.hub = SimpleNamespace(candidates=lambda: deepcopy(candidates))
    signals = SimpleNamespace(m234=f)
    p = LifetimePolicy(signals, b, lambda k, v: trace.append((k, v)), M2Config(), seconds)
    p.poll(20)
    o = next(iter(b.orders.values()))
    b.advance(0.1)
    return p, b, o, candidates, trace


@pytest.mark.parametrize("seconds", [3, 10, 30])
def test_trigger_expiry_does_not_cancel_fixed_deadline(seconds):
    p, b, o, candidates, trace = setup(seconds)
    candidates.clear()
    b.advance(0.6)
    p.poll(20.6)
    assert o.state == "LIVE" and o.cancel_requested is None
    assert o.intent.deadline == seconds
    b.advance(seconds - 0.01)
    p.poll(20 + seconds - 0.01)
    assert o.cancel_requested is None
    b.advance(seconds)
    p.poll(20 + seconds)
    assert o.cancel_requested == seconds
    assert o.state == "CANCEL_IN_FLIGHT"
    b.advance(seconds + 0.2)
    assert o.state == "CANCELLED"


def test_same_price_refresh_retains_order_queue_and_original_deadline():
    p, b, o, candidates, trace = setup()
    before = deepcopy(b.queues)
    candidates[0].update(id="updated-support", confirmed=0.4, deadline=8.0)
    b.advance(0.4)
    p.poll(20.4)
    assert len(b.orders) == 1 and o.cancel_requested is None
    assert b.queues == before and o.intent.deadline == 10
    assert sum(k == "TRADE_INTENT_CREATED" for k, _ in trace) == 1


def test_partial_fill_after_signal_end_and_during_cancel_then_settlement():
    p, b, o, candidates, trace = setup(3)
    candidates.clear()
    b.advance(1)
    p.poll(21)
    b.trade(sell("85"), "first")
    assert o.filled == 5  # 80 ahead then five own shares
    b.advance(3)
    p.poll(23)
    b.advance(3.1)
    b.trade(sell("2", trade_id="second"), "second")
    assert o.filled == 7
    b.advance(3.2)
    b.settle("YES")
    assert o.state == "CANCELLED" and o.filled == 7
    assert b.account("M2").payout - b.account("M2").spent == D("3.50")
    b.check()


@pytest.mark.parametrize(
    "cause", ["uncertain", "generation", "book", "lower_bid", "opposite", "cutoff"]
)
def test_independent_hard_cancellations(cause):
    p, b, o, candidates, trace = setup(30)
    b.advance(0.4)
    elapsed = 20.4
    if cause == "uncertain":
        b.gap("WS_DISCONNECTED")
    elif cause == "generation":
        p.signals.m234.generation = 1
    elif cause == "book":
        p.signals.m234.m1.legal = lambda book, now: False
    elif cause == "lower_bid":
        book = p.signals.m234.books["1"]
        p.signals.m234.books["1"] = replace(book, bids=((D(".49"), D("80")),))
    elif cause == "opposite":
        candidates.append(
            {**candidates[0], "id": "opposite", "token": "2", "outcome": "NO", "confirmed": 0.3}
        )
    elif cause == "cutoff":
        elapsed = 240
    p.poll(elapsed)
    assert o.cancel_requested == 0.4
    assert any(k == "LIFETIME_CANCEL_DECISION" for k, _ in trace)
    if cause == "uncertain":
        b.advance(0.6)
        assert o.uncertain  # cancel acknowledgement doesn't erase historical uncertainty


def test_m4_ask_recovery_ends_remaining_order_but_expiry_alone_does_not():
    p, b, o, candidates, trace = setup(30, "M4")
    candidates.clear()
    b.advance(1)
    p.poll(21)
    assert o.cancel_requested is None
    p.observe_signal(
        {
            "strategy": "M4",
            "event": "ASK_REFERENCE_CONFIRMED",
            "payload": {"round": {"token": "1"}},
        },
        1.0,
    )
    p.poll(21)
    assert o.cancel_requested == 1


def test_higher_bid_does_not_cancel_or_chase_and_intent_matches_submit():
    p, b, o, candidates, trace = setup()
    book = p.signals.m234.books["1"]
    p.signals.m234.books["1"] = replace(book, bids=((D(".51"), D("80")),))
    b.advance(0.4)
    p.poll(20.4)
    assert o.cancel_requested is None and o.intent.price == "0.50"
    intent = next(v["intent"] for k, v in trace if k == "INTENT")
    submit = next(v["intent"] for k, v in trace if k == "SUBMIT")
    assert intent == submit and intent["lifetime"] == "INDEPENDENT_10S"


def test_original_baseline_still_cancels_when_candidate_expires():
    p, b, o, candidates, trace = setup()
    # A separate baseline on the same feed, retaining original policy implementation.
    base, events = broker()
    baseline = M234Policy(p.signals, base, lambda *args: None, M2Config())
    baseline.poll(20)
    candidates.clear()
    base.advance(0.6)
    baseline.poll(20.6)
    assert next(iter(base.orders.values())).cancel_requested == 0.6


async def test_raw_replay_preserves_baseline_and_cohort_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(
        socket, "socket", lambda *a, **k: pytest.fail("offline replay accessed network")
    )
    tape = fixture_events()
    states = []
    for cls, name in ((Engine, "baseline"), (LifetimeEngine, "one"), (LifetimeEngine, "two")):
        e = cls(tmp_path / name)
        for event in tape:
            await e.ingest(event)
        states.append(e.snapshot())
        await e.close()
    for name, expected in states[0]["brokers"].items():
        assert digest(expected) == digest(states[1]["brokers"][name])
    assert digest(states[1]) == digest(states[2])
    rows, orders = summarize_market(states[1], tmp_path / "one")
    assert len(rows) == 24
    assert {r["variant"] for r in rows} == {"baseline", "hold-3s", "hold-10s", "hold-30s"}
    assert all(
        D(o["gross_pnl_lower"]) <= D(o["known_gross_pnl"]) <= D(o["gross_pnl_upper"])
        for o in orders
    )


def test_frozen_archive_is_verified_and_never_overwrites_workspace(tmp_path):
    import hashlib
    import json
    import tarfile

    from m1234_sim_frozen_source import materialize

    original = tmp_path / "original"
    file = original / "src/polymaker/sample.py"
    file.parent.mkdir(parents=True)
    file.write_text("value = 1\n")
    run = tmp_path / "run"
    launch = run / "launch"
    launch.mkdir(parents=True)
    expected = {"src/polymaker/sample.py": hashlib.sha256(file.read_bytes()).hexdigest()}
    (launch / "implementation.json").write_text(json.dumps(expected))
    with tarfile.open(launch / "source.tar.gz", "w:gz") as archive:
        archive.add(file, arcname="src/polymaker/sample.py")
    root, identity = materialize(run, tmp_path / "cache")
    assert len(identity) == 64
    assert (root / "src/polymaker/sample.py").read_text() == "value = 1\n"
    file.write_text("value = 2\n")
    assert materialize(run, tmp_path / "cache")[0] == root
    assert file.read_text() == "value = 2\n"
    (root / "src/polymaker/sample.py").write_text("corrupted\n")
    with pytest.raises(ValueError, match="FROZEN_SOURCE_HASH_MISMATCH"):
        materialize(run, tmp_path / "cache")


async def test_candidate_events_match_unchanged_detector(tmp_path):
    from polymaker.m1234_sim.store import read_audit

    tape = fixture_events()
    candidates = []
    for cls, name in ((Engine, "original"), (LifetimeEngine, "experiment")):
        e = cls(tmp_path / name)
        for event in tape:
            await e.ingest(event)
        await e.close()
        candidates.append(
            [
                (r["input_seq"], r["payload"])
                for r in read_audit(tmp_path / name / "simulation.sqlite3")
                if r["kind"] == "SIGNAL"
                and r["payload"]["strategy"] in ("M2", "M3", "M4")
                and r["payload"]["event"] in ("M2_CANDIDATE", "CANDIDATE")
            ]
        )
    assert candidates[0] == candidates[1]
