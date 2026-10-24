"""BA admission clocks and real SDK guards, with all trading boundaries mocked."""

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import RDPBAConfig, RDPObiConfig
from polymaker.rdp_sm_ba import BidAbsorption, entry_admission
from polymaker.rdp_sm_network import NetworkHealth
from test_rdp_sm_ba import absorption, ba_feed, depth, execution_setup, trade
from test_rdp_sm_ba import env as ba_env
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi import feed_fixture
from test_rdp_sm_obi_execution import cleanup


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from ba_env.__wrapped__(monkeypatch, tmp_path)


def ready_network():
    clock = SimpleNamespace(now=0.)
    network = NetworkHealth(RDPObiConfig(), lambda: clock.now)
    for _ in range(10):
        network.observe("GET", .1)
    assert network.snapshot().allowed
    return network, clock


def live_ba():
    return RDPBAConfig(signal_lifetime_sec=3., max_book_age_ms=200,
                       max_pair_skew_ms=200, max_entry_age_ms=1000)


def test_recent_writes_are_immutable_deduplicated_and_do_not_certify_recovery():
    network, clock = ready_network()
    assert network.recent_writes().post.status == "UNKNOWN"
    assert network.recent_writes().delete.status == "UNKNOWN"
    network.observe("POST", .9, operation_id="business-rejection", outcome="HTTP_REJECTION")
    network.observe("POST", .2, operation_id="business-rejection")
    network.observe("DELETE", .7, operation_id="scope-a", batch_id="old")
    network.observe("DELETE", .3, operation_id="scope-ab", batch_id="old")
    evidence = network.recent_writes()
    assert evidence.post.operations == evidence.delete.operations == 1
    assert evidence.post.max_ms == 900 and evidence.delete.max_ms == 700
    with pytest.raises(FrozenInstanceError):
        evidence.delete.max_ms = 0
    assert network.snapshot().allowed  # Summarizing did not introduce a new circuit.
    clock.now = 5.001
    assert network.recent_writes().post.status == "UNKNOWN"
    assert evidence.post.max_ms == 900  # The old immutable packet did not change.
    network.observe("DELETE", .1, success=False)
    for _ in range(10):
        network.observe("GET", .001)
    assert network.recent_writes().delete.status == "UNKNOWN"
    assert network.snapshot().state == "PAUSED"


@pytest.mark.parametrize("age_ms,reason", [(999, None), (1000, None), (1001, "BA_ENTRY_STALE")])
def test_entry_age_is_independent_of_obi_100ms(age_ms, reason):
    network, clock = ready_network()
    clock.now = age_ms/1000
    evidence = entry_admission(live_ba(), t0=0., deadline=3., writes=network.recent_writes())
    assert evidence.reason == reason
    assert not network.snapshot(age_ms).allowed  # OBI's own clock remains strict.
    assert network.snapshot().allowed


@pytest.mark.parametrize("latency_ms,reason", [(749, None), (750, None), (751, "BA_CANCEL_LATENCY_HIGH")])
def test_cancel_latency_cap_does_not_change_shared_network_health(latency_ms, reason):
    network, clock = ready_network()
    network.observe("DELETE", latency_ms/1000, batch_id="cancelled")
    assert network.snapshot().allowed  # One slow operation is not two.
    evidence = entry_admission(live_ba(), t0=0., deadline=3., writes=network.recent_writes())
    assert evidence.reason == reason
    clock.now = 5.001
    fresh = entry_admission(live_ba(), t0=5., deadline=8., writes=network.recent_writes())
    assert fresh.reason is None and fresh.writes.delete.status == "UNKNOWN"


@pytest.mark.parametrize("latency_ms,reason", [(1999, None), (2000, None), (2001, "BA_EXECUTION_BUDGET_INSUFFICIENT")])
def test_remaining_budget_uses_recent_post_max_not_an_average(latency_ms, reason):
    network, clock = ready_network()
    network.observe("POST", latency_ms/1000, operation_id="one")
    network.observe("POST", .1, operation_id="one")
    clock.now = .5
    evidence = entry_admission(live_ba(), t0=0., deadline=3., writes=network.recent_writes())
    assert evidence.reason == reason
    assert evidence.post_budget_ms == latency_ms
    assert evidence.remaining_ms == 2500 and evidence.writes.post.operations == 1


@pytest.mark.parametrize("settings", [
    {"max_entry_age_ms": 750}, {"max_entry_age_ms": 2000},
    {"max_entry_age_ms": float("nan")}, {"max_pair_skew_ms": 0},
    {"network": {"max_cancel_rtt_ms": float("inf")}},
    {"network": {"post_budget_floor_ms": 1500}},
    {"network": {"min_live_budget_ms": 0}}, {"network": {"unknown": 1}},
])
def test_invalid_ba_network_config_is_rejected(settings):
    with pytest.raises(ValidationError):
        RDPBAConfig(**settings)


@pytest.mark.parametrize("age_ms,valid", [(199, True), (201, False)])
def test_ba_local_book_age_200ms_and_obi_stays_100ms(age_ms, valid):
    feed, clock, rows = ba_feed(max_book_age_ms=200)
    clock.now = .01
    rows = deepcopy(rows)
    for row in rows:
        row["timestamp"] = str(feed.wall()*1000)
    feed.apply(rows)
    absorption(feed)
    clock.now += age_ms/1000
    assert feed.snapshot().valid is valid
    assert feed.ba.legal(feed.books["1"], clock.now) is valid
    obi, obi_clock, _ = feed_fixture()
    obi_clock.now = .15
    assert obi.snapshot().reason == "WS_BOOK_STALE"


@pytest.mark.parametrize("kind", ["receipt", "source"])
@pytest.mark.parametrize("skew_ms,valid", [(199, True), (201, False)])
def test_ba_pair_skew_checks_subsecond_source_and_receipt(kind, skew_ms, valid):
    feed, clock, _ = ba_feed(max_book_age_ms=300, max_pair_skew_ms=200)
    clock.now = .3
    for token, book in feed.books.items():
        feed.books[token] = replace(book, received_mono=clock.now, source_time=feed.wall())
    older = feed.books["2"]
    field = "received_mono" if kind == "receipt" else "source_time"
    feed.books["2"] = replace(older, **{field: getattr(older, field)-skew_ms/1000})
    assert feed.snapshot().valid is valid
    if not valid:
        assert feed.snapshot().reason == "WS_PAIR_TIME_SKEW"


def configure_live_ba(e, feed):
    e.cfg.rdp_sm.ba = live_ba()
    feed.ba = BidAbsorption(feed, e.cfg.rdp_sm.ba)


@pytest.mark.parametrize("delay", [.3, .6])
async def test_late_ba_confirmation_reaches_real_maker_post_and_fixed_three_second_close(env, monkeypatch, delay):
    setup = await execution_setup(env, monkeypatch)
    v, e, feed, _, normal, wire = setup[:6]
    try:
        configure_live_ba(e, feed)
        feed.apply(trade(feed))
        t0 = feed.ba.states["1"].signal_start_mono
        await asyncio.sleep(delay)
        # A later distinct SELL keeps source evidence fresh without resetting T0.
        feed.apply([trade(feed, size="6"), depth(feed, feed.books["1"].best_bid_size-5),
                    depth(feed, feed.books["1"].best_bid_size)])
        assert feed.ba.states["1"].signal_start_mono == t0
        assert feed.ba.evaluate(feed.snapshot()).direction == "YES"
        result, _ = await normal.enter(D(0), D(0), "late-confirm")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        request = v.post_requests[0][0]
        assert request["postOnly"] and request["order"]["side"] == "BUY"
        assert request["orderType"] == "GTC"
        assert result.fill_deadline_monotonic == pytest.approx(t0+3)
        first_delete = next(t[2] for t in wire.timeline if t[0] == "DELETE")
        assert t0+3 <= first_delete < t0+3.2
        logs = journal_records(e.journal.path)
        decision = next(r["data"]["payload"]["intent"] for r in logs if r["kind"] == "sm_ba_final_decision")
        admission = decision["ba_admission"]
        assert delay*1000 <= admission["entry_age_ms"] <= 1000
        assert admission["reason"] is None and admission["network_allowed"]
        assert admission["writes"]["delete"]["status"] == "UNKNOWN"
        assert admission["deadline"] == result.fill_deadline_monotonic
        assert feed.ba.states["1"].consumed
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault,reason", [
    ("entry_age", "BA_ENTRY_STALE"),
    ("cancel", "BA_CANCEL_LATENCY_HIGH"),
    ("budget", "BA_EXECUTION_BUDGET_INSUFFICIENT"),
    ("transport", "NETWORK_PAUSED"),
])
async def test_native_post_guard_rechecks_entry_and_network_after_preparation(env, monkeypatch, fault, reason):
    setup = await execution_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    try:
        configure_live_ba(e, feed)
        absorption(feed)
        native = e.gateway.send_prepared

        async def changed(*args, **kwargs):
            if fault == "entry_age":
                await asyncio.sleep(1.02)
            elif fault == "cancel":
                e.reads.network.observe("DELETE", .751, batch_id="prior-close")
            elif fault == "budget":
                e.reads.network.observe("POST", 2.6, operation_id="prior-post")
            else:
                e.reads.network.observe("GET", .1, success=False)
            return await native(*args, **kwargs)

        monkeypatch.setattr(e.gateway, "send_prepared", changed)
        await normal.enter(D(0), D(0), "changed-after-preparation")
        assert not v.post_requests and not feed.ba.states["1"].consumed
        logs = journal_records(e.journal.path)
        decision = next(r["data"]["payload"]["intent"] for r in logs if r["kind"] == "sm_ba_final_decision")
        assert decision["aborted_reason"] == reason
        assert decision["post_started"] is None and decision["ba_admission"]
        assert decision["health"] is None  # Never claim native POST admission.
    finally:
        await cleanup(setup)


async def test_slow_cancel_blocks_early_preparation_but_does_not_consume_episode(env, monkeypatch):
    setup = await execution_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    try:
        configure_live_ba(e, feed)
        absorption(feed)
        e.reads.network.observe("DELETE", .751, batch_id="prior-close")
        assert e.network_health().allowed
        assert await normal.enter(D(0), D(0), "slow-cancel") is None
        assert not v.post_requests and not feed.ba.states["1"].consumed
        logs = journal_records(e.journal.path)
        wait = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_ba_wait")
        assert wait["reason"] == "BA_CANCEL_LATENCY_HIGH"
        assert wait["ba_admission"]["writes"]["delete"]["max_ms"] == 751
    finally:
        await cleanup(setup)
