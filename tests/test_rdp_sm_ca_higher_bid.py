"""Original CA episode owns admission; higher-bid inventory owns entry/TP."""

import asyncio
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPSMConfig
from test_rdp_sm_ca_entry import ca_setup, confirm_ca
from test_rdp_sm_ca_matched_tp import fill, tp_setup
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions


async def set_prices(setup, higher, *, tie=False):
    values = {"1": ".67" if higher == "1" else ".32",
              "2": ".67" if higher == "2" else ".32"}
    if tie:
        values = {"1": ".49", "2": ".49"}
    pump = setup[-2]
    pump.update(bid=values["1"], yes_ask=str(D(values["1"])+D('.01')),
                no_bid=values["2"], no_ask=str(D(values["2"])+D('.01')))
    feed = setup[2]
    for token, bid in values.items():
        feed.apply({"event_type":"book", "market":CID, "asset_id":token,
                    "timestamp":str(feed.wall()*1000),
                    "bids":[{"price":bid,"size":"80"}],
                    "asks":[{"price":str(D(bid)+D('.01')),"size":"20"}]})


def test_signal_side_is_configured_and_higher_bid_remains_selectable():
    assert RDPSMConfig().ca_entry_direction == "signal"
    cfg = Config.load("config", load_env=False).rdp_sm
    assert cfg.normal_entry_mode == "ca_v1" and cfg.ca_entry_direction == "signal"
    assert cfg.ca_matched_tp.profit_per_share == D('.05') and cfg.ca_matched_tp.ttl_sec == 30
    assert not cfg.repair.ba_enabled
    with pytest.raises(ValidationError):
        RDPSMConfig(ca_entry_direction="unknown")


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("signal_token", ["1", "2"])
@pytest.mark.parametrize("higher", ["1", "2"])
async def test_either_ca_signal_posts_higher_bid_and_keeps_original_claim_and_deadline(
    env, monkeypatch, tmp_path, cached, signal_token, higher
):
    setup = await ca_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_entry_direction = "higher_bid"
    await set_prices(setup, higher)
    companion = (await attach(setup, tmp_path))[0] if cached else None
    try:
        await confirm_ca(feed, signal_token)
        ep = feed.ca.states[signal_token]
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "higher-entry"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        quote = result.requested_quotes[0]
        assert quote.token_id == higher and quote.price == .67 and quote.size == 5
        assert v.post_requests[0][0]["postOnly"]
        assert result.t0_monotonic == ep.t0 and result.fill_deadline_monotonic == ep.deadline
        assert ep.episode_id in feed.ca_entry.consumed
        assert sizing["ca_signal_side"] == ep.outcome
        assert sizing["ca_opening_side"] == quote.outcome
        assert sizing["ca_entry_direction"] == "higher_bid"
        assert await normal.enter(D(0), D(0), "same-signal-again") is None
        assert len(v.post_requests) == 1
        records = [r for r in journal_records(e.journal.path) if r["kind"] == "sm_ca_final_decision"]
        intent = records[0]["data"]["payload"]["intent"]
        assert intent["claim"][0] == ep.episode_id and intent["claim"][1] == ep.outcome
        posted = next(r["payload"] for r in feed.ca.records if r["event"] == "CA_NORMAL_POST")
        assert posted["signal_direction"] == ep.outcome and posted["direction"] == quote.outcome
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("signal_token,higher", [("1", "2"), ("2", "1")])
async def test_tp_hedges_actual_high_side_on_matched_even_when_ca_signal_is_opposite(
    env, monkeypatch, tmp_path, cached, signal_token, higher
):
    setup = await tp_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_entry_direction = "higher_bid"
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.restore_after_ba = True
    e.cfg.rdp_sm.repair.ba_enabled = False
    await set_prices(setup, higher)
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    opening = []

    async def send(signed, **kwargs):
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            opening.append(fill(v, oid, "high-opening", status="MATCHED"))
        else:
            assert opening[0]["status"] == "MATCHED"
            fill(v, oid, "opposite-tp")
            opening[0]["status"] = "CONFIRMED"
        v.rows[oid]["status"] = "MATCHED"
        reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        await confirm_ca(feed, signal_token)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "high-ca-tp"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2
        assert e.attempts[0].quote.token_id == higher
        assert e.attempts[1].quote.token_id == signal_token
        assert e.attempts[1].quote.price == .28 and e.attempts[1].quote.size == 5
        assert sizing["ca_matched_tp"]["reason"] == "TP_ORDER_TERMINAL"
        assert e.economic_inventory() == (D(5), D(5)) and e.repair_episode is None
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("fault", ["higher_side_flip", "higher_quote_move", "signal_anchor_move", "tie"])
async def test_native_guard_rechecks_original_ca_and_current_high_side(env, monkeypatch, fault):
    setup = await ca_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_entry_direction = "higher_bid"
    await set_prices(setup, "2")
    native = e.gateway.send_prepared

    async def changed(signed, **kwargs):
        token = "1" if fault == "signal_anchor_move" else "2"
        bid = {"higher_side_flip":".31", "higher_quote_move":".66",
               "signal_anchor_move":".33", "tie":".32"}[fault]
        setup[-2]["running"] = False
        feed.apply({"event_type":"book", "market":CID, "asset_id":token,
                    "timestamp":str(feed.wall()*1000),
                    "bids":[{"price":bid,"size":"80"}],
                    "asks":[{"price":str(D(bid)+D('.01')),"size":"20"}]})
        return await native(signed, **kwargs)

    monkeypatch.setattr(e.gateway, "send_prepared", changed)
    try:
        await confirm_ca(feed, "1")
        await asyncio.wait_for(normal.enter(D(0), D(0), "high-guard"), 5)
        assert not v.post_requests and not feed.ca_entry.consumed
        assert all(not a.post_started for a in e.attempts)
    finally:
        await cleanup(setup)


async def test_equal_bids_skip_and_fast_ca_still_cannot_open(env, monkeypatch):
    setup = await ca_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_entry_direction = "higher_bid"
    await set_prices(setup, "1", tie=True)
    try:
        await confirm_ca(feed, "1")
        assert await normal.enter(D(0), D(0), "tie") is None
        assert normal.signal_block_reason == "CA_ENTRY_PRICE_TIE"
        ep = feed.ca.states["1"]
        ep.tC = ep.t0 + .05
        assert await normal.enter(D(0), D(0), "fast") is None
        assert normal.signal_block_reason == "CA_CONFIRMATION_TOO_FAST"
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)
