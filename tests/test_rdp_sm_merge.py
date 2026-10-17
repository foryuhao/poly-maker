"""Merge admission, official SDK/RPC boundary, capital and append-only history."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import InventoryMismatch, RiskJournal
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_merge import merge_confirmed_pairs
from test_rdp_sm_m1 import ADDRESS, START
from test_rdp_sm_m2_execution import journal_records, open_executor, quotes
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_type2_account import type2


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


async def paired(env, *, no=5, prices=None):
    v, ex = await open_executor(env)
    orders = quotes()
    if prices is not None:
        orders = [replace(q, price=price) for q, price in zip(orders, prices, strict=True)]
        for q in orders:
            v.books[q.token_id]['bids'][0]['price'] = str(q.price)
            v.books[q.token_id]['asks'][0]['price'] = str(Decimal(str(q.price)) + Decimal('.01'))
    def fill(venue):
        venue.fill("o-1", "yes", 5)
        venue.fill("o-2", "no", no)
    v.on_cancel = fill
    window = await ex.execute_window("w1", orders)
    assert window.close_barrier == "SAFE_TO_CONTINUE"
    v.on_cancel = None
    v.positions = [{"token_id": "1", "current_size": 5, "avg_price": orders[0].price},
                   {"token_id": "2", "current_size": no, "avg_price": orders[1].price}]
    return v, ex


@pytest.mark.parametrize("time", [239, 240, 270, 300])
async def test_merge_starts_at_240_and_only_uses_confirmed_session_pairs(env, time):
    v, ex = await paired(env)
    v.time = START + time
    try:
        merged = await merge_confirmed_pairs(ex)
    except Exception:
        pytest.fail(str([r for r in journal_records(ex.journal.path) if r["kind"].startswith("sm_merge")]))
    assert merged is (time >= 240)
    assert len(v.merge_submissions) == (1 if time >= 240 else 0)
    if time >= 240:
        assert ex.merged_pairs == 5 and ex.committed() == 0
        assert ex.safe_to_continue
        assert len(ex.confirmed()) == 2
        assert not await merge_confirmed_pairs(ex)


async def test_merge_preserves_unpaired_shares_and_releases_only_verified_cost(env):
    v, ex = await paired(env, no=3)
    v.time = START + 240
    assert await merge_confirmed_pairs(ex)
    assert ex.merged_pairs == 3
    assert ex.committed() == Decimal(".98")
    *_, account = await ex._snapshot(include_books=False)
    assert account["yes_inventory"] == 2 and account["no_inventory"] == 0


@pytest.mark.parametrize("quantity,merged", [(0.099999, False), (0.1, True)])
async def test_dust_pairs_skip_chain_reads_and_exact_threshold_merges(env, quantity, merged):
    v, ex = await paired(env, no=quantity)
    v.time = START + 240
    assert await merge_confirmed_pairs(ex) is merged
    if merged:
        assert ex.merged_pairs == Decimal('0.1')
    else:
        assert not v.merge_rpc_calls and not v.merge_submissions
        assert ex.merged_pairs == 0 and ex.committed() > 0


async def test_merge_ignores_external_dust_without_merging_or_losing_its_balance(env, monkeypatch):
    from polymaker.rdp_sm_merge import SMMerger

    v, ex = await paired(env)
    v.time = START + 240
    native_prepare = SMMerger.prepare

    def prepare_with_physical_dust(merger, meta, expected):
        # Add a constant real balance at the RPC boundary, before and after burn.
        native_balance = merger.backend._balance
        monkeypatch.setattr(merger.backend, '_balance',
                            lambda context, token: native_balance(context, token) + 50_000)
        return native_prepare(merger, meta, expected)

    monkeypatch.setattr(SMMerger, 'prepare', prepare_with_physical_dust)
    assert await merge_confirmed_pairs(ex)
    assert ex.merged_pairs == 5 and ex.committed() == 0
    records = journal_records(ex.journal.path)
    intent = next(r['data']['payload'] for r in records if r['kind'] == 'sm_merge_intent')
    result = next(r['data']['payload'] for r in records if r['kind'] == 'sm_merge_result')
    assert intent['amount_raw'] == 5_000_000
    assert intent['before_yes_raw'] == intent['before_no_raw'] == 5_050_000
    assert result['after_yes_raw'] == result['after_no_raw'] == 50_000
    # Re-open the journal through its real historical proof validator.
    journal = RiskJournal(ex.cfg.paths.journal_dir, v.funder)
    journal.close()


async def test_merge_presentation_failure_cannot_interrupt_verified_accounting(env):
    v, ex = await paired(env)
    v.time = START + 240
    def broken_display():
        raise RuntimeError("display unavailable")
    assert await merge_confirmed_pairs(ex, on_progress=broken_display)
    assert len(v.merge_submissions) == 1 and ex.merged_pairs == 5
    assert ex.committed() == 0 and ex.safe_to_continue
    result = next(r["data"]["payload"] for r in journal_records(ex.journal.path)
                  if r["kind"] == "sm_merge_result")
    assert result["status"] == "CONFIRMED" and result["released_cost_usdc"] == "4.85"


async def test_stale_position_index_after_merge_waits_for_data_api_without_chain_fallback(env):
    v, ex = await paired(env, no=3)
    v.time = START + 240
    assert await merge_confirmed_pairs(ex)
    v.positions = [{"token_id": "1", "current_size": 5, "avg_price": .49},
                   {"token_id": "2", "current_size": 3, "avg_price": .48}]
    rpc_calls = list(v.merge_rpc_calls)
    with pytest.raises(InventoryMismatch):
        await ex._snapshot(include_books=False)
    assert v.merge_rpc_calls == rpc_calls
    v.positions = [{"token_id": "1", "current_size": 2, "avg_price": .49}]
    *_, account = await ex._snapshot(include_books=False)
    assert ex.inventory_mismatch is None and v.merge_rpc_calls == rpc_calls
    assert account["yes_inventory"] == 2 and account["no_inventory"] == 0


@pytest.mark.parametrize("fault", ["submit_timeout", "poll_timeout", "cash"])
async def test_unknown_merge_never_retries_or_releases_capital(env, fault):
    v, ex = await paired(env)
    v.time = START + 240
    v.merge_fault = fault
    with pytest.raises(RuntimeError, match="MERGE_UNKNOWN"):
        await merge_confirmed_pairs(ex)
    assert len(v.merge_submissions) == 1 and ex.merged_pairs == 0
    assert ex.committed() == Decimal("4.85") and not ex.safe_to_continue
    assert not await ex.verify_close()
    with pytest.raises(Exception, match="MERGE_CLOSE_BARRIER_REQUIRED"):
        await merge_confirmed_pairs(ex)
    assert len(v.merge_submissions) == 1


@pytest.mark.parametrize("fault", ["token", "owner", "missing_auth"])
async def test_merge_identity_or_missing_builder_auth_blocks_before_submission(env, fault):
    v, ex = await paired(env)
    v.time = START + 240
    if fault == "missing_auth":
        ex.cfg.wallet.signature_type = 2
        ex.cfg.secrets.builder_key = None
    else:
        v.merge_fault = fault
        if fault == "owner":
            ex.cfg.wallet.signature_type = 2
    with pytest.raises(Exception, match="MERGE_PREPARE_FAILED"):
        await merge_confirmed_pairs(ex)
    assert not v.merge_submissions and ex.merged_pairs == 0


@pytest.mark.parametrize("corrupt", [None, "incomplete", "cash", "summary"])
async def test_prior_merge_history_does_not_gate_another_session(env, corrupt):
    v, cfg = env
    def fill(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["YES"], "yes", 5)
            venue.fill(ids["NO"], "no", 5)
    on_windows(v, fill, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.merged_pairs == result.recovered_collateral_usdc == 5
    assert result.confirmed_yes == result.confirmed_no == 0
    path = Path(v.executor.journal.path)
    original = await asyncio.to_thread(path.read_bytes)
    if corrupt:
        rows = journal_records(path)
        if corrupt == "incomplete":
            rows = [r for r in rows if r["kind"] != "sm_merge_result"]
        elif corrupt == "cash":
            next(r for r in rows if r["kind"] == "sm_merge_result")["data"]["payload"]["collateral_after_raw"] += 1
        else:
            rows[-1]["data"]["payload"]["merged_pairs"] = 0
        await asyncio.to_thread(path.write_text, "".join(json.dumps(r) + "\n" for r in rows))
    if corrupt:
        journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
        assert journal.sequence == 0
        journal.close()
    else:
        journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
        journal.close()
        assert await asyncio.to_thread(path.read_bytes) == original


@pytest.mark.parametrize("corrupt", [None, "price", "maker", "side", "matched", "cost"])
async def test_fractional_merge_records_are_not_next_session_inputs(env, corrupt):
    """A real producer's fractional fills/Merge must pass its next-session reader."""
    v, cfg = env

    def fill(venue, index):
        if index == 1:
            ids = current_orders(venue)
            trade = venue.fill(ids["YES"], "fractional-yes", 4.987742)
            # Observed official maker price: a few decimal places above the
            # limit, accepted by the shared RDP price tolerance.
            trade["maker_orders"][0]["price"] = str(
                float(venue.rows[ids["YES"]]["price"]) + 0.0000000649895042)
            venue.fill(ids["NO"], "fractional-no", 4.99)

    on_windows(v, fill, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.merged_pairs == result.recovered_collateral_usdc == 4.987742
    assert result.confirmed_yes == 0 and result.confirmed_no == .002258
    path = Path(v.executor.journal.path)
    rows = journal_records(path)
    merge = next(r["data"]["payload"] for r in rows if r["kind"] == "sm_merge_result")
    assert merge["status"] == "CONFIRMED" and merge["after_no_raw"] == 2258
    if corrupt:
        close = next(r["data"]["payload"] for r in rows
                     if r["kind"] == "sm_close_barrier" and r["data"]["payload"]["confirmed_fills"])
        fill = next(f for f in close["confirmed_fills"] if f["token_id"] == "1")
        if corrupt == "price":
            fill["price"] = float(v.rows[fill["maker_order_id"]]["price"]) + .010001
        elif corrupt == "maker":
            fill["maker_order_id"] = "unknown-order"
        elif corrupt == "side":
            fill["our_side"] = "SELL"
        elif corrupt == "matched":
            fill["status"] = "MATCHED"
        else:
            merge["released_cost_usdc"] = str(Decimal(merge["released_cost_usdc"]) + Decimal(".000001"))
        # Even consistently altered old close proofs must stay confined to
        # that Session's audit file; a new Journal never imports them.
        if corrupt != "cost":
            for row in rows:
                if row["kind"] == "sm_close_barrier":
                    for proof_fill in row["data"]["payload"]["confirmed_fills"]:
                        if proof_fill["token_id"] == "1":
                            proof_fill.update(fill)
        await asyncio.to_thread(path.write_text, "".join(json.dumps(r) + "\n" for r in rows))
    original = await asyncio.to_thread(path.read_bytes)
    if corrupt:
        journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
        assert journal.sequence == 0
        journal.close()
    else:
        journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
        journal.close()
    assert await asyncio.to_thread(path.read_bytes) == original


async def test_merge_cannot_bypass_an_unresolved_close_barrier(env):
    v, ex = await paired(env)
    v.time = START + 240
    ex.invalidate("UNKNOWN_EVIDENCE")
    with pytest.raises(Exception, match="MERGE_CLOSE_BARRIER_REQUIRED"):
        await merge_confirmed_pairs(ex)
    assert not v.merge_submissions


async def test_safe_merge_uses_the_gateway_funder_and_owner(env):
    v, cfg, safe = type2(env)
    v, ex = await paired(env)
    v.time = START + 240
    assert await merge_confirmed_pairs(ex)
    assert v.merge_submissions[0][3].lower() == safe.lower() == ex.gateway.funder.lower()


async def test_config_funder_change_cannot_merge_a_different_safe(env):
    type2(env)
    v, ex = await paired(env)
    v.time = START + 240
    ex.cfg.secrets.browser_address = "0x" + "88" * 20
    with pytest.raises(Exception, match="MERGE_ACCOUNT_IDENTITY_CHANGED"):
        await merge_confirmed_pairs(ex)
    assert not v.merge_submissions


async def test_confirmed_failed_merge_keeps_inventory_and_cost(env):
    v, ex = await paired(env)
    v.time = START + 240
    v.merge_fault = "failed"
    with pytest.raises(Exception, match="MERGE_FAILED"):
        await merge_confirmed_pairs(ex)
    assert len(v.merge_submissions) == 1
    assert ex.merged_pairs == 0 and ex.committed() == Decimal("4.85")
    assert ex.safe_to_continue


async def test_just_closed_market_uses_sdk_closed_lookup_without_skipping_identity_checks(env):
    v, ex = await paired(env)
    v.time = START + 300
    v.merge_fault = "closed_lookup"
    assert await merge_confirmed_pairs(ex)
    assert len(v.merge_submissions) == 1 and ex.merged_pairs == 5


async def test_verified_merge_recovers_capital_before_large_minimum_notional_repair(env):
    v, cfg = env
    def fill(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["YES"], "initial", 5)
            venue.books["2"]["bids"][0]["price"] = ".01"
            venue.books["2"]["asks"][0]["price"] = ".02"
        elif index == 2:
            venue.fill(ids["NO"], "minimum-topup", 100)
            venue.time = START + 240
            # The repair fits the $15 cap only after verified merge recovery.
            venue.books["1"]["bids"][0]["price"] = ".14"
            venue.books["1"]["asks"][0]["price"] = ".15"
        elif index == 3:
            venue.fill(ids["YES"], "repair", 95)
            venue.time = START + 300
    on_windows(v, fill)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "REBALANCE"]
    assert [m[2] for m in v.merge_submissions] == [5_000_000, 95_000_000]
    assert result.merged_pairs == result.recovered_collateral_usdc == 100
    assert result.confirmed_yes == result.confirmed_no == 0 and result.delta == 0
    assert result.windows[-1]["result"]["requested_quotes"][0]["size"] == 95
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


def inventory_report(ex):
    loop = SMTradingLoop(ex.cfg, ex.slug, ex.session_id)
    loop.executor = ex
    result = RunResult(ex.meta.condition_id, ex.session_id)
    result.final_close_barrier = 'SAFE_TO_CONTINUE' if ex.safe_to_continue else 'BLOCKED'
    loop._inventory(result)
    return loop, result


@pytest.mark.parametrize('prices,pnl', [((.57, .38), .25), ((.67, .54), -1.05)])
async def test_verified_merge_preserves_gross_profit_or_loss_after_inventory_flattens(env, prices, pnl):
    v, ex = await paired(env, prices=prices)
    loop, result = inventory_report(ex)
    assert result.realized_merge_pnl == 0
    assert result.unmerged_pair_pnl_estimate == pytest.approx(pnl)
    assert result.theoretical_pair_pnl == pytest.approx(pnl)
    assert result.sizing_report['pair_pnl'] == 'UNKNOWN'
    v.time = START + 240
    assert await merge_confirmed_pairs(ex)
    loop._inventory(result)
    assert result.confirmed_yes == result.confirmed_no == 0
    assert result.realized_merge_pnl == pytest.approx(pnl)
    assert result.unmerged_pair_pnl_estimate == 0
    assert result.theoretical_pair_pnl == pytest.approx(pnl)
    assert result.sizing_report['pair_pnl'] == pytest.approx(pnl)
    assert result.sizing_report['trading_pnl_before_rebate'] == 'UNKNOWN'
    assert result.sizing_report['actual_maker_rebate'] == 'UNKNOWN'
    loop._inventory(result)  # Rebuilding the report must not count a merge twice.
    assert result.theoretical_pair_pnl == pytest.approx(pnl)


async def test_merged_pair_profit_excludes_remaining_unpaired_cost(env):
    v, ex = await paired(env, no=3)
    v.time = START + 240
    assert await merge_confirmed_pairs(ex)
    _, result = inventory_report(ex)
    assert result.confirmed_yes == 2 and result.confirmed_no == 0
    assert result.realized_merge_pnl == pytest.approx(.09)
    assert result.theoretical_pair_pnl == pytest.approx(.09)
    assert result.sizing_report['pair_pnl'] == pytest.approx(.09)
    assert result.sizing_report['unintentional_residual_pnl'] == 'UNKNOWN'
    assert result.sizing_report['trading_pnl_before_rebate'] == 'UNKNOWN'


async def test_unknown_merge_cannot_create_realized_profit(env):
    v, ex = await paired(env)
    v.time = START + 240
    v.merge_fault = 'poll_timeout'
    with pytest.raises(RuntimeError, match='MERGE_UNKNOWN'):
        await merge_confirmed_pairs(ex)
    _, result = inventory_report(ex)
    assert result.realized_merge_pnl == 0
    assert result.sizing_report['pair_pnl'] == 'UNKNOWN'
    assert result.final_close_barrier == 'BLOCKED'
