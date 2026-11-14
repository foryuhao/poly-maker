"""Mode-correct observation results; entirely synthetic market state."""
from decimal import Decimal

import pytest
from m4_helpers import close, setup


@pytest.mark.parametrize('live', [False, True])
async def test_completion_reports_mode_without_releasing_risk_or_repeating_posts(tmp_path, live):
    market, executor, _, _ = await setup(tmp_path, live=live)
    executor.display_events = []
    try:
        await market.submit(next(iter(market.pending.values())))
        before = market.ledger.metric()
        market.complete_window()
        market.complete_window()
        results = [payload for kind, payload in executor.display_events if kind == 'sm_run_result']
        assert len(results) == 1
        policy = 'PLATFORM_AUTO_SETTLEMENT' if live else 'PUBLIC_RESOLUTION_OF_MODELED_RECEIPTS'
        status = 'AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED' if live else 'AWAITING_PUBLIC_RESOLUTION'
        assert results[0]['settlement_policy'] == market.ledger.get('settlement_policy') == policy
        assert results[0]['settlement_status'] == market.ledger.get('settlement_status') == status
        assert market.ledger.metric() == before
        assert results[0]['m4_submissions'] == before
        assert before['native_posts'] == 1
        assert Decimal(before['committed_usdc']) == Decimal('4.97')
    finally:
        await close(market, executor)
