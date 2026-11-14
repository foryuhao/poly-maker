"""Explicit main-policy lifecycle, price/budget, and raw-input isolation checks."""
import socket
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from m234_sim_main import MainEngine, MainPolicy, experiment_definition
from m234_sim_main_replay import summarize_market

from m1234_fixtures import fixture_events
from polymaker.m2_config import M2Config
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import digest
from test_m1234_sim_core import broker, sell
from test_rdp_sm_obi import feed_fixture


def setup(strategy="M2", anchor=".50", bid=".50"):
    f, _, _ = feed_fixture()
    b, trace = broker()
    f.books["1"] = replace(f.books["1"], bids=((D(bid), D("80")),))
    candidates = [{"id": "first", "strategy": strategy, "token": "1", "outcome": "YES",
                   "price": D(anchor), "confirmed": 0.0, "deadline": .5,
                   "generation": 0, "root": "root"}]
    f.generation = 0
    f.m1 = SimpleNamespace(legal=lambda book, now: True)
    f.monotonic = lambda: b.now
    f.m1_watermark = lambda: b.now
    f.hub = SimpleNamespace(candidates=lambda: deepcopy(candidates))
    p = MainPolicy(SimpleNamespace(m234=f), b, lambda k, v: trace.append((k, v)), M2Config())
    p.poll(20)
    order = next(iter(b.orders.values()))
    b.advance(.1)
    return p, b, order, candidates, trace


@pytest.mark.parametrize("anchor,bid,expected", [(".50", ".49", ".49"),
                                                (".48", ".50", ".48")])
def test_minimum_limit_and_budget_even_without_existing_depth(anchor, bid, expected):
    p, b, o, candidates, trace = setup(anchor=anchor, bid=bid)
    assert D(o.intent.price) == D(expected)
    assert D(o.intent.price) * D(o.intent.quantity) <= 5
    assert 5 - D(o.intent.price) * D(o.intent.quantity) < D(".01")
    intent = next(v for k, v in trace if k == "INTENT")
    assert intent["signal_anchor"] == str(D(anchor))
    assert intent["submission_best_bid"] == str(D(bid))
    assert intent["intent"] == next(v["intent"] for k, v in trace if k == "SUBMIT")


@pytest.mark.parametrize("cause", ["expiry", "refresh", "book", "generation", "lower_bid",
                                   "opposite", "uncertain"])
def test_no_extra_cancellation_or_repricing(cause):
    p, b, o, candidates, trace = setup("M4")
    b.advance(40)
    if cause == "expiry":
        candidates.clear()
    elif cause == "refresh":
        candidates[0].update(id="new-independent-signal", deadline=50, confirmed=39)
    elif cause == "book":
        p.signals.m234.m1.legal = lambda *_: False
    elif cause == "generation":
        p.signals.m234.generation += 1
    elif cause == "lower_bid":
        f = p.signals.m234
        f.books["1"] = replace(f.books["1"], bids=((D(".30"), D(80)),))
    elif cause == "opposite":
        candidates[0].update(id="opposite", token="2", outcome="NO", confirmed=39)
    elif cause == "uncertain":
        b.gap("WS_DISCONNECTED")
    p.poll(60)
    assert o.cancel_requested is None and o.intent.price == "0.50"
    assert not any(k == "MAIN_CANCEL_DECISION" for k, v in trace)
    assert sum(x.intent.outcome == "YES" for x in b.orders.values()) == 1


def test_partial_fill_long_after_signal_then_t240_cancel_latency_and_settlement():
    p, b, o, candidates, trace = setup()
    candidates.clear()
    b.advance(40)
    p.poll(60)
    b.trade(sell("85"), "first")
    assert o.filled == 5
    b.advance(220)
    p.poll(240)
    assert o.cancel_requested == 220 and o.state == "CANCEL_IN_FLIGHT"
    b.advance(220.1)
    b.trade(sell("2", trade_id="second"), "second")
    b.advance(220.2)
    b.settle("YES")
    assert o.filled == 7 and o.state == "CANCELLED"
    assert b.account("M2").payout - b.account("M2").spent == D("3.5")
    assert [v["reason"] for k, v in trace if k == "MAIN_CANCEL_DECISION"] == ["T240"]
    b.check()


def test_uncertainty_survives_cutoff_cancel():
    p, b, o, candidates, trace = setup()
    b.gap("WS_DISCONNECTED")
    b.advance(218)
    p.poll(238, uncertainty=2)
    b.advance(218.2)
    assert o.uncertain and o.filled == 0 and b.account("M2").reserved > 0
    assert o.cancel_requested == 218


async def test_raw_baseline_exact_and_live_definition_replays(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network in replay"))
    tape = fixture_events()
    states = []
    for name in ("original", "derived", "live", "replay"):
        engine = (Engine(tmp_path / name) if name == "original" else
                  MainEngine(tmp_path / name, derive=name == "derived", compact=True))
        events = deepcopy(tape)
        if name in {"live", "replay"}:
            events[0]["definition"]["settings"]["m234_execution_main"] = experiment_definition()
        for event in events:
            await engine.ingest(event)
        states.append(engine.snapshot())
        await engine.close()
    for scenario, expected in states[0]["brokers"].items():
        assert digest(expected) == digest(states[1]["brokers"][scenario])
    assert digest(states[1]) == digest(states[2]) == digest(states[3])
    rows, _ = summarize_market(states[1], tmp_path / "derived")
    assert len(rows) == 12 and {r["variant"] for r in rows} == {"baseline", "main"}
