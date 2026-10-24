"""Configured NORMAL confirmations use genuine WS events; execution I/O is fake."""

import asyncio
import copy
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPObiConfig, RDPSMConfig, Secrets
from polymaker.rdp_sm_marketdata import SessionFeed
from polymaker.rdp_sm_normal import NormalController
from test_rdp_sm_m1 import CID, SLUG, START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi import feed_fixture
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env
from test_rdp_sm_residual_exit import mature_sigma


def controller(elapsed=60, cfg=None):
    feed, clock, rows = feed_fixture()
    if cfg is not None:
        old = feed
        feed = SessionFeed(old.meta, cfg, monotonic=old.monotonic, wall=old.wall)
        feed.verify_ticks({token: book.tick for token, book in old.books.items()})
        feed.connect()
        feed.apply(rows)
    executor = SimpleNamespace(
        cfg=SimpleNamespace(rdp_sm=SimpleNamespace(obi=feed.cfg)),
        slug=SLUG,
        official_time=START + elapsed,
        time_anchor=0.0,
        monotonic=lambda: clock.now,
    )
    return NormalController(executor, SimpleNamespace(feed=feed)), feed, clock, rows


def frame(feed, rows, *, direction="YES", n=0):
    rows = copy.deepcopy(rows)
    for row in rows:
        strong = (row["asset_id"] == "1") == (direction == "YES")
        bid, ask = (80 + n, 20 + n) if strong else (20 + n, 80 + n)
        if direction == "NONE":
            bid = ask = 50 + n
        row["timestamp"] = str(feed.wall() * 1000)
        row["bids"][0]["size"] = str(bid)
        row["asks"][0]["size"] = str(ask)
    return rows


@pytest.mark.parametrize(
    "elapsed,required", [(0, 2), (15, 2), (59.9, 2), (60, 3), (70, 3), (119.9, 3)]
)
@pytest.mark.parametrize("direction", ["YES", "NO"])
def test_distinct_same_direction_events_required(elapsed, required, direction):
    normal, feed, clock, rows = controller(elapsed)
    # A direction change starts at one sample; it cannot borrow the old YES.
    if direction == "NO":
        clock.now = 0.001
        feed.apply(frame(feed, rows, direction=direction))
    for _ in range(3):
        assert normal.select(feed.snapshot())[0] is None
    assert normal.signal_block_reason == "NORMAL_SIGNAL_CONFIRMATION_PENDING"
    for count in range(2, required + 1):
        clock.now += 0.01
        feed.apply(frame(feed, rows, direction=direction, n=count))
        assert normal.select(feed.snapshot())[0] == (direction if count == required else None)
    evidence = normal.signal_confirmation
    assert evidence["required"] == required
    assert len(evidence["samples"]) == required
    assert len({s["event_mono"] for s in evidence["samples"]}) == required
    assert {s["label"] for s in evidence["samples"]} == {"VALID_" + direction}


@pytest.mark.parametrize("elapsed", [20, 70])
def test_duplicate_book_and_repeated_reads_cannot_supply_more_signals(elapsed):
    normal, feed, clock, rows = controller(elapsed)
    clock.now = 0.01
    feed.apply(rows)  # Receipt and sequence refresh, depth/signal event unchanged.
    assert feed.sequence > 1
    for _ in range(10):
        assert normal.select(feed.snapshot())[0] is None
    assert len(normal.signal_confirmation["samples"]) == 1


@pytest.mark.parametrize("interruption", ["NONE", "opposite", "invalid", "reconnect"])
@pytest.mark.parametrize("elapsed,required", [(20, 2), (70, 3)])
def test_intermediate_event_resets_even_when_controller_did_not_observe_it(
    interruption, elapsed, required
):
    normal, feed, clock, rows = controller(elapsed)
    for count in range(1, required):
        clock.now += 0.01
        feed.apply(frame(feed, rows, n=count))
    assert normal.select(feed.snapshot())[0] == "YES"
    clock.now += 0.01
    if interruption == "NONE":
        feed.apply(frame(feed, rows, direction="NONE", n=2))
    elif interruption == "opposite":
        feed.apply(frame(feed, rows, direction="NO", n=2))
    elif interruption == "invalid":
        # Only YES refreshes after a gap; NO is stale and the pair is INVALID.
        clock.now = 0.2
        feed.apply(frame(feed, rows, n=2)[:1])
        assert not feed.snapshot().valid
    else:
        feed.connect()
    clock.now += 0.01
    feed.apply(frame(feed, rows, n=3))
    assert normal.select(feed.snapshot())[0] is None
    assert len(normal.signal_confirmation["samples"]) == 1
    for count in range(2, required + 1):
        clock.now += 0.01
        feed.apply(frame(feed, rows, n=3 + count))
        assert normal.select(feed.snapshot())[0] == ("YES" if count == required else None)


def test_late_confirmations_cannot_borrow_pre_60_signals():
    normal, feed, clock, rows = controller(59.98)
    clock.now = 0.01
    feed.apply(frame(feed, rows, n=1))
    assert normal.select(feed.snapshot())[0] == "YES"
    clock.now = 0.021
    feed.apply(frame(feed, rows, n=2))
    assert normal.select(feed.snapshot())[0] is None
    assert len(normal.signal_confirmation["samples"]) == 1
    clock.now = 0.031
    feed.apply(frame(feed, rows, n=3))
    assert normal.select(feed.snapshot())[0] is None
    assert len(normal.signal_confirmation["samples"]) == 2
    clock.now = 0.041
    feed.apply(frame(feed, rows, n=4))
    assert normal.select(feed.snapshot())[0] == "YES"


@pytest.mark.parametrize("elapsed,required", [(20, 2), (70, 3)])
def test_stale_snapshot_cannot_reuse_a_later_confirmation(elapsed, required):
    normal, feed, clock, rows = controller(elapsed)
    stale = feed.snapshot()
    for count in range(1, required):
        clock.now += 0.01
        feed.apply(frame(feed, rows, n=count))
    assert normal.select(feed.snapshot())[0] == "YES"
    assert normal.select(stale)[0] is None


@pytest.mark.parametrize("elapsed,required", [(20, 2), (70, 3)])
@pytest.mark.parametrize("side", ["YES", "NO"])
def test_threshold_is_inclusive_for_all_signals(elapsed, required, side):
    normal, feed, clock, rows = controller(elapsed)
    clock.now = 0.001
    feed.apply(frame(feed, rows, direction="NONE"))
    for i in range(1, required + 1):
        clock.now = i * 0.01
        exact = copy.deepcopy(rows)
        for row in exact:
            row["timestamp"] = str(feed.wall() * 1000)
            strong = (row["asset_id"] == "1") == (side == "YES")
            by, ay = (65 * i, 35 * i) if strong else (35 * i, 65 * i)
            row["bids"][0]["size"] = str(by)
            row["asks"][0]["size"] = str(ay)
        feed.apply(exact)
        direction, signal = normal.select(feed.snapshot())
        assert abs(signal.imbalance) == feed.cfg.threshold
        assert direction == (side if i == required else None)


@pytest.mark.parametrize(
    "field", ["normal_confirmations_before_60", "normal_confirmations_from_60"]
)
@pytest.mark.parametrize("value", [0, -1, True, 2.5, "3"])
def test_confirmation_counts_require_positive_integers(field, value):
    with pytest.raises(ValidationError):
        RDPObiConfig(**{field: value})


@pytest.mark.parametrize(
    "early,late,elapsed,required", [(1, 4, 20, 1), (1, 4, 70, 4), (5, 2, 20, 5), (5, 2, 70, 2)]
)
def test_configured_counts_control_entry_and_history_capacity(
    tmp_path, monkeypatch, early, late, elapsed, required
):
    (tmp_path / "config.toml").write_text(
        f"[rdp_sm.obi]\nnormal_confirmations_before_60 = {early}\nnormal_confirmations_from_60 = {late}\n"
    )
    monkeypatch.setattr("polymaker.config.Secrets", lambda: Secrets.model_construct())
    cfg = Config.load(tmp_path, load_env=False).rdp_sm.obi
    normal, feed, clock, rows = controller(elapsed, cfg)
    for count in range(1, required + 1):
        if count > 1:
            clock.now += 0.01
            feed.apply(frame(feed, rows, n=count))
        assert normal.select(feed.snapshot())[0] == ("YES" if count == required else None)
    assert len(normal.signal_confirmation["samples"]) == required


@pytest.mark.parametrize("directory", ["config", "livecfg"])
def test_shipped_configs_and_omitted_fields_default_to_two_and_three(directory):
    cfg = RDPSMConfig(
        **tomllib.loads(
            (Path(__file__).resolve().parents[1] / directory / "config.toml").read_text()
        )["rdp_sm"]
    ).obi
    # The working configuration explicitly selects one early confirmation;
    # livecfg and omitted settings retain their existing two/three defaults.
    assert cfg.normal_confirmations_before_60 == (1 if directory == "config" else 2)
    assert cfg.normal_confirmations_from_60 == 3
    assert RDPObiConfig().normal_confirmations_before_60 == 2
    assert RDPObiConfig().normal_confirmations_from_60 == 3


async def manual_setup(env, monkeypatch, elapsed, direction="YES"):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed = setup[:3]
    setup[7]["running"] = False
    await setup[-1]
    v.time = START + elapsed
    e.official_time, e.time_anchor = v.time, e.monotonic()

    async def server_time():
        e.reads.network.observe("GET", 0.001, endpoint="/time")
        return e.official_time + e.monotonic() - e.time_anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    feed.connect()
    rows = [
        dict(
            event_type="book",
            market=CID,
            asset_id=token,
            timestamp=str(feed.wall() * 1000),
            bids=[dict(price=".50" if token == "1" else ".49", size="1")],
            asks=[dict(price=".52" if token == "1" else ".51", size="1")],
        )
        for token in ("1", "2")
    ]
    feed.apply(frame(feed, rows, direction=direction))
    mature_sigma(feed)
    latest = {"n": 0}

    async def refresh(_sequence, _timeout):
        # Wake activation with a real receipt but no extra depth event.
        feed.apply(frame(feed, rows, direction=direction, n=latest["n"]))
        await asyncio.sleep(0)

    monkeypatch.setattr(feed, "wait_after", refresh)
    return setup, rows, latest


@pytest.mark.parametrize(
    "elapsed,required,early,late", [(20, 2, 2, 3), (70, 3, 2, 3), (20, 1, 1, 4), (70, 4, 1, 4)]
)
@pytest.mark.parametrize("direction,token", [("YES", "1"), ("NO", "2")])
async def test_real_gateway_only_posts_after_required_confirmation(
    env, monkeypatch, elapsed, required, early, late, direction, token
):
    env[1].rdp_sm.obi = RDPObiConfig(
        normal_confirmations_before_60=early, normal_confirmations_from_60=late
    )
    setup, rows, latest = await manual_setup(env, monkeypatch, elapsed, direction)
    v, e, feed, _, normal = setup[:5]
    try:
        for count in range(1, required):
            result = await normal.enter(*e.economic_inventory(), "confirm-signals")
            assert result is None and not v.post_requests and not e.attempts
            assert normal.signal_block_reason == "NORMAL_SIGNAL_CONFIRMATION_PENDING"
            # A genuine depth event changes L1 sizes, not quote prices.
            latest["n"] = count
            feed.apply(frame(feed, rows, direction=direction, n=count))
        result = await normal.enter(*e.economic_inventory(), "confirm-signals")
        window, _ = result
        assert window.close_barrier == "SAFE_TO_CONTINUE", window.block_reason
        assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 1
        assert v.post_requests[0][0]["order"]["tokenId"] == token
        assert feed.episodes.consumed
        await e.journal.flush()
        decision = next(
            r["data"]["payload"]["intent"]
            for r in journal_records(e.journal.path)
            if r["kind"] == "sm_obi_final_decision"
        )
        evidence = decision["normal_signal_confirmation"]
        assert evidence["required"] == required and len(evidence["samples"]) == required
        assert len({s["event_mono"] for s in evidence["samples"]}) == required
        assert {s["label"] for s in evidence["samples"]} == {"VALID_" + direction}
        # Original one-shot episode ownership still prevents duplicate orders.
        assert await normal.enter(*e.economic_inventory(), "same-episode") is None
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("jump", [None, 60.1, 120.1, 14.9])
async def test_final_post_guard_rechecks_confirmation_and_time_boundaries(env, monkeypatch, jump):
    elapsed = 70 if jump is None else 59
    setup, rows, latest = await manual_setup(env, monkeypatch, elapsed)
    v, e, feed, _, normal = setup[:5]
    try:
        required = 3 if jump is None else 2
        for count in range(1, required):
            latest["n"] = count
            feed.apply(frame(feed, rows, n=count))
        native = e.gateway.send_prepared

        async def before_sdk(*args, **kwargs):
            if jump is None:
                feed.apply(frame(feed, rows, direction="NONE", n=2))
                feed.apply(frame(feed, rows, n=3))  # One valid signal after reset.
            else:
                e.official_time, e.time_anchor = START + jump, e.monotonic()
            return await native(*args, **kwargs)

        monkeypatch.setattr(e.gateway, "send_prepared", before_sdk)
        result = await normal.enter(*e.economic_inventory(), "guard-recheck")
        assert result is not None
        assert not v.post_requests and not feed.episodes.consumed
        if jump in (14.9, 120.1):
            assert setup[3].guard_reason == "MARKET_SUBMISSION_CUTOFF"
        if jump is None or jump < 120:
            assert normal.signal_confirmation["required"] == (2 if jump == 14.9 else 3)
            if jump != 14.9:
                assert len(normal.signal_confirmation["samples"]) < 3
    finally:
        await cleanup(setup)
