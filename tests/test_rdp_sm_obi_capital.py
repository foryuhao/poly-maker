"""NORMAL cycle budget regressions with real controllers and fake venue I/O."""

import asyncio
from decimal import Decimal

import pytest

from polymaker.rdp_sm_rebalance import RebalanceController
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env


async def fill_two_pairs(setup, *, replay_2325=False):
    v, e, _, lifecycle, normal = setup[:5]
    state = setup[7]
    filled = set()

    def fill_on_cancel(venue):
        for oid, row in list(venue.rows.items()):
            if oid in filled:
                continue
            filled.add(oid)
            venue.fill(oid, tid=f"fill-{len(filled)}", qty=5)
            cost = Decimal(row['price']) * 5
            venue.capital['balance'] = str(int(venue.capital['balance']) - int(cost * 1_000_000))
        venue.positions = [
            {'token_id': token, 'current_size': str(sum(
                (Decimal(row['size_matched']) for row in venue.rows.values()
                 if row['asset_id'] == token), Decimal(0))), 'avg_price': '.5'}
            for token in ('1', '2')
        ]

    v.on_cancel = fill_on_cancel
    e.cfg.rdp_sm.obi.rebalance_order_ttl_sec = .05
    rebalance = RebalanceController(e, lifecycle)
    prices = [('.52', '.47'), ('.36', '.63'), ('.46', '.53'), ('.57', '.42')]
    if not replay_2325:
        prices = [('.50', '.50')] * 4
    for index, ((yes_bid, no_bid), phase) in enumerate(zip(
        prices, ('NORMAL', 'REBALANCE', 'NORMAL', 'REBALANCE'), strict=True
    )):
        state.update(direction='YES' if index < 2 else 'NO', bid=yes_bid, no_bid=no_bid,
                     yes_ask=str(Decimal(yes_bid) + Decimal('.01')),
                     no_ask=str(Decimal(no_bid) + Decimal('.01')))
        await asyncio.sleep(.025)
        yes = sum((Decimal(str(f.size)) for f in e.confirmed() if f.token_id == '1'), Decimal(0))
        no = sum((Decimal(str(f.size)) for f in e.confirmed() if f.token_id == '2'), Decimal(0))
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        if phase == 'NORMAL':
            outcome = await normal.enter(yes, no, f'prior-{index}')
        else:
            outcome = await rebalance.repair(yes, no, f'prior-{index}')
        assert outcome is not None, lifecycle.wait_reason
        assert outcome[0].close_barrier == 'SAFE_TO_CONTINUE', outcome[0].block_reason
    assert len(v.post_requests) == 4 and len(e.confirmed()) == 4
    assert e.committed() == Decimal('11.25' if replay_2325 else '10')


@pytest.mark.parametrize('cached', [False, True])
async def test_2325_normal_entries_block_with_1125_already_committed(env, monkeypatch, tmp_path, cached):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal = setup[:5]
    companion = None
    try:
        if cached:
            companion, _ = await attach(setup, tmp_path)
        await fill_two_pairs(setup, replay_2325=True)
        for window, yes_bid, no_bid in [('w5', '.60', '.39'), ('w6', '.71', '.28'), ('w7', '.69', '.30')]:
            setup[7].update(direction='YES', bid=yes_bid, no_bid=no_bid,
                            yes_ask=str(Decimal(yes_bid) + Decimal('.01')),
                            no_ask=str(Decimal(no_bid) + Decimal('.01')))
            await asyncio.sleep(.025)
            lifecycle.account = (await e._snapshot(include_books=False))[3]
            assert await normal.enter(Decimal(10), Decimal(10), window) is None
            assert lifecycle.wait_reason[0] == 'OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT'
        assert len(v.post_requests) == 4 and e.committed() == Decimal('11.25')
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize('shares,balance,allowed', [
    ('5', '100000000', True),  # 10 + 5 == 15 is admitted.
    ('5.01', '100000000', False),  # Actual submitted quantity, including fractions.
    ('5', '3000000', True),  # Cash pays the 2.50 order, not the 5.00 capital budget.
    ('5', '1000000', False),  # Cycle budget does not bypass actual cash checks.
])
async def test_normal_share_budget_boundary_and_cash_check(env, monkeypatch, shares, balance, allowed):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal = setup[:5]
    try:
        await fill_two_pairs(setup)
        e.cfg.rdp_sm.base_size = Decimal(shares)
        v.capital['balance'] = balance
        setup[7]['direction'] = 'YES'
        await asyncio.sleep(.025)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        outcome = await normal.enter(Decimal(10), Decimal(10), 'boundary')
        assert (outcome is not None) is allowed, lifecycle.wait_reason
        assert len(v.post_requests) == (5 if allowed else 4)
        if allowed:
            assert outcome[0].close_barrier == 'SAFE_TO_CONTINUE', outcome[0].block_reason
        else:
            assert lifecycle.wait_reason[0] == 'OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT'
    finally:
        await cleanup(setup)


async def test_rebalance_still_uses_order_notional_above_ten_dollars_committed(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal = setup[:5]
    try:
        await fill_two_pairs(setup)
        setup[7].update(direction='YES', no_bid='.49', no_ask='.51')
        await asyncio.sleep(.025)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        entry, _ = await normal.enter(Decimal(10), Decimal(10), 'last-normal')
        assert entry.close_barrier == 'SAFE_TO_CONTINUE'
        assert e.committed() == Decimal('12.5')
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        repair, _ = await RebalanceController(e, lifecycle).repair(Decimal(15), Decimal(10), 'repair')
        assert repair.close_barrier == 'SAFE_TO_CONTINUE', repair.block_reason
        assert len(v.post_requests) == 6 and e.committed() == Decimal('14.95')
        assert repair.yes_inventory == repair.no_inventory == 15
    finally:
        await cleanup(setup)
