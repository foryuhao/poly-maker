"""Exercise the crash boundary between durable observation and immutable planning."""
import pytest

from m12_follow.tests.test_execution_flow import test_follow_native_fak as follow_flow


@pytest.mark.asyncio
async def test_resumes_unplanned_admission(tmp_path, monkeypatch):
    from polymaker.m4_live_budget_market import BudgetMarket

    collect = BudgetMarket.collect
    interrupted = False

    class ProcessCrash(BaseException):
        pass

    def interrupt_first_admission(market):
        nonlocal interrupted
        if interrupted or not hasattr(market.feed.m1, "resume_unplanned"):
            return collect(market)
        try:
            with monkeypatch.context() as patch:
                def crash(*args, **kwargs):
                    raise ProcessCrash()
                patch.setattr(market.ledger, "plan_signal", crash)
                return collect(market)
        except ProcessCrash:
            interrupted = True
            [row] = market.ledger.rows()
            assert row["state"] == "OBSERVED"
            assert not market.ledger.attempts() and not market.ledger.facts("taker_plan")
            old = market.feed.m1
            market.feed.m1 = type(old)(market.feed, old.settings, old.reader, old.run_id)
            market.restore()
            assert market.ledger.row(row["id"])["reason"] == "M4_INCOMPLETE_UNSENT_INTENT"
            return collect(market)

    monkeypatch.setattr(BudgetMarket, "collect", interrupt_first_admission)
    await follow_flow(tmp_path, monkeypatch, "ack")
    assert interrupted
