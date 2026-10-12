"""F01 regression evidence: real M1/FL readers, existing offline HTTP fixtures."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from polymaker.rdp_sm import SessionStatus, run_preflight
from tests.test_rdp_sm_m1 import SLUG, order
from tests.test_rdp_sm_m1 import cfg as cfg
from tests.test_rdp_sm_m1 import venue as venue


def market_tokens(venue, yes, no):
    venue.raw["clobTokenIds"] = json.dumps([no, yes])
    # Supply consistent fake books so alias rejection cannot pass merely because
    # the external fixture has no book for the invalid spelling (Reviewer D2/D3).
    for token in (yes, no):
        if isinstance(token, str) and token not in venue.books:
            venue.books[token] = deepcopy(venue.books["1"])
            venue.books[token]["asset_id"] = token
            venue.ticks[token] = venue.ticks["1"]


@pytest.mark.parametrize("no", ["01", "1 ", "1"])
async def test_f01_t01_t02_duplicate_or_aliased_market_tokens_block(venue, cfg, no):
    market_tokens(venue, "1", no)
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED
    assert result.checks[-1].name == "market" and result.account is None
    assert not result.trading_authorized
    assert all(path == "/markets" for _, path, _ in venue.calls)


@pytest.mark.parametrize("token", ["01", "1 "])
async def test_f01_t03_positive_position_alias_cannot_be_hidden(venue, cfg, token):
    # Canonical market identity, noncanonical account identity (Reviewer D1).
    venue.positions = [{"token_id": token, "current_size": 2, "avg_price": 0.5}]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and result.account is None
    assert result.checks[-1].name == "account"
    assert result.checks[-1].detail == "TOKEN_IDENTITY_INVALID"
    assert any(c.name == "market" and c.outcome == "PASS" for c in result.checks)
    assert any(path == "/v2/positions" for _, path, _ in venue.calls)


@pytest.mark.parametrize("token", ["01", "1 "])
async def test_f01_t04_open_order_alias_cannot_be_hidden_or_stripped(venue, cfg, token):
    # Validate the actual raw SDK response before Gateway strips asset_id.
    venue.orders = [order(token)]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and result.account is None
    assert result.checks[-1].name == "account"
    assert any(c.name == "market" and c.outcome == "PASS" for c in result.checks)
    assert any(path == "/data/orders" for _, path, _ in venue.calls)


async def test_f01_reviewer_d3_market_alias_with_canonical_inventory_and_order(venue, cfg):
    market_tokens(venue, "01", "2")
    venue.positions = [{"token_id": "1", "current_size": 2, "avg_price": 0.5}]
    venue.orders = [order("1")]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED
    assert result.checks[-1].name == "market" and result.account is None
    assert result.checks[-1].detail == "TOKEN_IDENTITY_INVALID"


@pytest.mark.parametrize("yes", ["1", str(2**256 - 1)])
async def test_f01_t05_distinct_canonical_tokens_keep_ready_path(venue, cfg, yes):
    market_tokens(venue, yes, "2")
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.READY
    assert result.market["yes"]["token_id"] == yes and result.market["no"]["token_id"] == "2"
    assert result.account["yes_inventory"] == result.account["no_inventory"] == 0
    assert result.account["target_open_orders"] == 0
    assert all(c.outcome == "PASS" for c in result.checks)
    assert result.read_only and not result.trading_authorized


@pytest.mark.parametrize(
    "source,token",
    [
        ("market", str(2**256)),
        ("market", "١"),
        ("position", str(2**256)),
        ("position", "invalid"),
        ("order", str(2**256)),
        ("order", "١"),
    ],
)
async def test_f01_t06_invalid_identity_blocks_at_actual_read_boundary(venue, cfg, source, token):
    if source == "market":
        market_tokens(venue, token, "2")
    elif source == "position":
        venue.positions = [{"token_id": token, "current_size": 2, "avg_price": 0.5}]
    else:
        venue.orders = [order(token)]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and result.account is None
    assert result.checks[-1].name == ("market" if source == "market" else "account")


async def test_f01_conflicting_account_position_aliases_block(venue, cfg):
    venue.positions = [
        {"token_id": "1", "current_size": 2, "avg_price": 0.5},
        {"token_id": "01", "current_size": 1, "avg_price": 0.5},
    ]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and result.account is None
    assert result.checks[-1].detail == "TOKEN_IDENTITY_INVALID"


async def test_f01_book_identity_alias_blocks(venue, cfg):
    venue.books["1"]["asset_id"] = "01"
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and result.market is None
    assert result.checks[-1].name == "books"
    assert result.checks[-1].detail == "TOKEN_IDENTITY_INVALID"
