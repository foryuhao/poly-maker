"""Low-notional CA/BA orders at the real SDK/POST and fill-proof boundaries."""

import asyncio
from decimal import Decimal as D
from types import MethodType, SimpleNamespace

import pytest

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm_execution import SMWindowExecutor, repair_buy_quantity
from test_rdp_sm_ba import trade
from test_rdp_sm_ca import buy
from test_rdp_sm_ca_entry import ca_setup
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_repair_ba_tolerance import book, tolerant_setup


@pytest.mark.parametrize("token", ["1", "2"])
@pytest.mark.parametrize("price", [".19", ".14", ".10", ".05", ".01"])
async def test_ca_low_notional_entry_posts_five_shares_once(env, monkeypatch, token, price):
    setup = await ca_setup(env, monkeypatch)
    v, _, feed, _, normal = setup[:5]
    try:
        p = D(price)
        other = "2" if token == "1" else "1"
        tick = feed.books[token].tick
        feed.apply([book(feed, p, p + tick, token),
                    book(feed, 1-p-feed.books[other].tick, 1-p, other)])
        feed.apply([trade(feed, token), buy(feed, token)])
        episode = feed.ca.states[token]
        result, _ = await normal.enter(D(0), D(0), "low-ca")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert [(q.outcome, D(str(q.price)), q.size) for q in result.requested_quotes] == [
            ("YES" if token == "1" else "NO", p, 5)
        ]
        assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 1
        posted = v.post_requests[0][0]
        assert posted["postOnly"] is True and posted["orderType"] == "GTC"
        assert D(posted["order"]["makerAmount"]) == p * 5_000_000 < 1_000_000
        assert D(posted["order"]["takerAmount"]) == 5_000_000
        assert result.t0_monotonic == episode.t0
        assert result.fill_deadline_monotonic == episode.deadline
        assert await normal.enter(D(0), D(0), "no-ca-repost") is None
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("price", [".14", ".10", ".05", ".01"])
@pytest.mark.parametrize("filled", [2, 5])
async def test_ba_fixed_low_price_repair_posts_exact_gap_and_tracks_partial_fill(
    env, monkeypatch, price, filled
):
    setup, controller = await tolerant_setup(env, monkeypatch)
    v, executor, feed = setup[:3]
    try:
        p, tick = D(price), feed.books["2"].tick
        setup[-2].update(bid=str(1-p-feed.books["1"].tick), yes_ask=str(1-p),
                         no_bid=str(p), no_ask=str(p+tick))
        await asyncio.sleep(.02)
        absorption(feed, "2")
        episode = next(iter(feed.opposite_ba.opportunities.values()))

        def fill(venue):
            attempt = executor.attempts[-1]
            oid = attempt.expected_order_id
            if attempt.quote.outcome == "NO" and not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "low-price-repair", filled)
            reflect_positions(venue)

        v.on_cancel = fill
        result, sizing = await controller.repair(*executor.economic_inventory(), "low-ba")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert [(q.outcome, D(str(q.price)), q.size) for q in result.requested_quotes] == [
            ("NO", p, 5)
        ]
        assert len(v.post_requests) == 2
        posted = v.post_requests[-1][0]
        assert posted["postOnly"] is True and posted["orderType"] == "GTC"
        assert D(posted["order"]["makerAmount"]) == p * 5_000_000 < 1_000_000
        assert D(posted["order"]["takerAmount"]) == 5_000_000
        assert executor.economic_inventory() == (D(5), D(filled))
        assert executor.repair_episode is None if filled == 5 else executor.repair_episode is not None
        assert sizing["quote_fixed"]
        assert result.fill_deadline_monotonic == episode.order_deadline
        assert await controller.repair(*executor.economic_inventory(), "no-ba-repost") is None
        assert len(v.post_requests) == 2
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("side,delta", [("NO", "5"), ("YES", "-5")])
@pytest.mark.parametrize("minimum,cash,maximum,expected", [
    ("5", ".70", "15", "5.00"),
    ("5", ".69", "15", "OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT"),
    ("6", "1", "15", "6.00"),
    ("16", "20", "15", "MINIMUM_ORDER_EXCEEDS_MAXIMUM"),
])
def test_low_notional_repair_preserves_funds_market_share_minimum_and_cap(
    side, delta, minimum, cash, maximum, expected
):
    executor = SimpleNamespace(
        cfg=SimpleNamespace(rdp_sm=RDPSMConfig(max_order_size=D(maximum))),
        meta=SimpleNamespace(min_order_size=D(minimum)),
    )
    executor.delta_candidate_allowed = MethodType(SMWindowExecutor.delta_candidate_allowed, executor)
    if expected[0].isdigit():
        assert repair_buy_quantity(executor, side, D(".14"), D(delta), D(cash)) == D(expected)
    else:
        with pytest.raises(ValueError, match=expected):
            repair_buy_quantity(executor, side, D(".14"), D(delta), D(cash))
