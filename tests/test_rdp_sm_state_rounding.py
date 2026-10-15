"""Whole-share state selection through actual quotes, submission and CLI output."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymaker.rdp_sm import _market
from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.rdp_sm_loop import make_quotes
from test_rdp_sm_m1 import SLUG, START, Venue
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_trading_phases import advance


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("gap,state", [
    ("4.499999", "NORMAL"), ("4.5", "REBALANCE"),
    ("4.994407", "REBALANCE"), ("5", "REBALANCE"),
    ("5.503207", "REBALANCE"),
])
@pytest.mark.parametrize("sign", [1, -1])
def test_rounded_state_keeps_exact_inventory_based_quote_size(gap, state, sign):
    venue = Venue()
    meta, _ = _market(venue.raw, SLUG)
    books = {t: {"best_bid": float(b["bids"][0]["price"]),
                 "best_ask": float(b["asks"][0]["price"]), "tick": float(venue.ticks[t])}
             for t, b in venue.books.items()}
    delta = Decimal(gap) * sign
    observed, quotes = make_quotes(meta, books, max(delta, 0), max(-delta, 0), "w")
    assert observed == state
    short = "NO" if sign > 0 else "YES"
    if state == "NORMAL":
        assert {q.outcome: q.size for q in quotes} == {short: 9.5, "YES" if sign > 0 else "NO": 5}
    else:
        assert len(quotes) == 1 and quotes[0].outcome == short
        # 5.503207 must submit 5.51, not the state comparison's rounded 6.
        assert quotes[0].size == (5.51 if gap == "5.503207" else 5)
    assert quotes[0].price == pytest.approx(
        books[quotes[0].token_id]["best_bid"] - (books[quotes[0].token_id]["tick"] if state == "NORMAL" else 0))


@pytest.mark.parametrize("gap", [4.499999, 4.5, 4.994407])
@pytest.mark.parametrize("initial_side", ["YES", "NO"])
@pytest.mark.parametrize("late", [False, True])
async def test_fractional_fill_state_matches_loop_submission_cutoff_and_log(env, gap, initial_side, late):
    venue, cfg = env
    venue.time = START + 110 if late else START + 15
    log = HumanRunLog()
    output = []

    def observe(kind, payload):
        output.extend(log.render(kind, payload))

    def fill(v, index):
        if index == 1:
            v.fill(current_orders(v)[initial_side], "fractional-first", gap)
            if late:
                advance(v, 200)

    on_windows(venue, fill, stop_after=2)
    try:
        result = await run_current_market(cfg, sleep=venue.sleep,
                                          monotonic=lambda: venue.mono,
                                          observer=SimpleNamespace(emit=observe))
    finally:
        log.close()
    state = "NORMAL" if gap < 4.5 else "REBALANCE"
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert result.delta == pytest.approx(gap if initial_side == "YES" else -gap)
    assert f"Next Trading State: {state} (inventory state; next market/time check still required)" in output
    if late and state == "NORMAL":
        assert [w["state"] for w in result.windows] == ["NORMAL"]
        assert [len(p) for p in venue.post_requests] == [2]
    else:
        assert [w["state"] for w in result.windows] == ["NORMAL", state]
        assert [len(p) for p in venue.post_requests] == [2, 1 if state == "REBALANCE" else 2]
        if state == "REBALANCE":
            quote = result.windows[1]["result"]["requested_quotes"][0]
            assert quote["outcome"] == ("NO" if initial_side == "YES" else "YES")
            assert quote["size"] == 5
        if late:
            assert venue.post_times[-1] == 200
