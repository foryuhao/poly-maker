"""A book move before preparation must not terminate an unpaired REPAIR."""

import asyncio
import time
from decimal import Decimal

import pytest

from polymaker.config import RDPRepairPathConfig
from polymaker.rdp_sm import SessionStatus, _Issue
from polymaker.rdp_sm_path_repair import PathRepairController
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions


async def setup_no_sl(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle, normal, _, _, pump, _ = setup
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+time.monotonic()-anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    pump.update(direction="NO", bid=".60", yes_ask=".61", no_bid=".39", no_ask=".40")
    await asyncio.sleep(.025)

    def fill_opening(venue):
        for oid, row in venue.rows.items():
            if row["asset_id"] == "2" and not row["associate_trades"]:
                venue.fill(oid, "opening-no", 5)
        reflect_positions(venue)

    v.on_cancel = fill_opening
    opened, _ = await normal.enter(Decimal(0), Decimal(0), "opening-no")
    v.on_cancel = None
    assert opened.close_barrier == "SAFE_TO_CONTINUE", opened.block_reason
    assert e.economic_inventory() == (Decimal(0), Decimal(5))
    e.cfg.rdp_sm.repair.path = RDPRepairPathConfig()
    repair = PathRepairController(e, lifecycle)
    monkeypatch.setattr(repair, "sigma", lambda: .02)
    pump.update(bid=".67", yes_ask=".68", no_bid=".32", no_ask=".33")
    await asyncio.sleep(.025)
    repair.observe(feed.snapshot(repair=True))
    pump.update(bid=".73", yes_ask=".74", no_bid=".26", no_ask=".27")
    await asyncio.sleep(.025)
    repair.observe(feed.snapshot(repair=True))
    assert repair.policy.mode == "SL"
    lifecycle.account = (await e._snapshot(include_books=False))[3]
    return setup, repair


@pytest.mark.parametrize("mode", ["TP"])
async def test_quote_crosses_during_flush_retries_and_pairs_without_session_exit(
        env, monkeypatch, mode):
    setup, repair = (await setup_no_sl(env, monkeypatch) if mode == "SL"
                     else await setup_policy(env, monkeypatch))
    v, e, _, lifecycle, _, _, _, pump, _ = setup
    original_flush = e.journal.flush
    moved = []
    expected_side, expected_price = ("YES", .72) if mode == "SL" else ("NO", .42)
    expected_cost = Decimal("1.95") if mode == "SL" else Decimal("1.5")

    async def move_before_preparation():
        await original_flush()
        if not moved:
            moved.append(True)
            # The plan was legal when formed. During its WAL flush, the
            # opposite ask moves down to the planned bid: no POST is legal.
            pump.update(**({"bid": ".72", "yes_ask": ".73", "no_bid": ".27", "no_ask": ".28"}
                           if mode == "SL" else
                           {"bid": ".54", "yes_ask": ".56", "no_bid": ".44", "no_ask": ".46"}))
            await asyncio.sleep(.025)

    monkeypatch.setattr(e.journal, "flush", move_before_preparation)
    try:
        assert await repair.repair(*e.economic_inventory(), "quote-race") is None
        assert moved and len(v.post_requests) == len(e.attempts) == 1
        assert e.fatal is None and e.safe_to_continue
        assert e.committed() == expected_cost
        assert repair.policy.mode == mode and repair.plan is None
        assert repair.next_review <= e.monotonic()  # bypass the old review lease
        assert "quote-race" not in e.used_windows

        def fill_repair(venue, oid):
            row = venue.rows[oid]
            if row["asset_id"] == ("1" if mode == "SL" else "2") and row["status"] == "LIVE":
                venue.fill(oid, "requoted-repair", 5)
                reflect_positions(venue)

        v.on_order_read = fill_repair
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        outcome = await repair.repair(*e.economic_inventory(), "quote-race")
        assert outcome is not None
        result, _ = outcome
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        quote = result.requested_quotes[0]
        assert (quote.outcome, quote.price, quote.size) == (expected_side, expected_price, 5)
        assert len(v.post_requests) == len(e.attempts) == 2
        assert e.economic_inventory() == (Decimal(5), Decimal(5))
        assert e.repair_episode is None and e.committed() <= 15
        records = journal_records(e.journal.path)
        invalidated = [r["data"]["payload"] for r in records
                       if r["kind"] == "sm_repair_quote_invalidated"]
        assert len(invalidated) == 1
        assert invalidated[0]["reason"] == "QUOTE_WOULD_TAKE"
        assert invalidated[0]["mode"] == mode
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("reason", [
    "QUOTE_SESSION_IDENTITY_INVALID", "QUOTE_SIZE_INVALID", "PREVIOUS_WINDOW_UNRESOLVED",
])
async def test_preparation_hard_failures_still_propagate(env, monkeypatch, reason):
    setup, repair = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle, *_ = setup

    async def failed_prepare(*args):
        raise _Issue(SessionStatus.BLOCKED, reason)

    monkeypatch.setattr(lifecycle, "prepare", failed_prepare)
    try:
        with pytest.raises(_Issue, match=reason):
            await repair.repair(*e.economic_inventory(), "hard-failure")
        assert len(v.post_requests) == len(e.attempts) == 1
    finally:
        await cleanup(setup)


async def test_activation_failure_is_not_retried_as_unsent_preparation(env, monkeypatch):
    setup, repair = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle, *_ = setup

    async def failed_activate(*args):
        raise _Issue(SessionStatus.BLOCKED, "QUOTE_WOULD_TAKE")

    monkeypatch.setattr(lifecycle, "activate", failed_activate)
    try:
        with pytest.raises(_Issue, match="QUOTE_WOULD_TAKE"):
            await repair.repair(*e.economic_inventory(), "activation-failure")
        assert len(v.post_requests) == len(e.attempts) == 1
    finally:
        await cleanup(setup)
