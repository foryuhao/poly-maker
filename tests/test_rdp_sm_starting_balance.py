"""Operator-funded startup balance; current-session M2 obligations remain enforced."""


import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RiskJournal, SMWindowExecutor
from test_rdp_sm_entry import history_boundary
from test_rdp_sm_historical_expiry import traded_records
from test_rdp_sm_historical_risk import historical_trades, safe_records, write_records
from test_rdp_sm_m1 import ADDRESS
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m2_remediation import external_order
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("balance,expected", [("6000000", 6), ("33464169", 20), ("100000000", 20)])
async def test_starting_budget_uses_official_balance_not_old_matched_cost(env, monkeypatch, balance, expected):
    v, cfg = env
    v.capital["balance"] = balance
    v.trades = historical_trades(v)
    path = write_records(cfg.paths.journal_dir, safe_records(retained=True))
    before = path.read_bytes()
    budgets = []
    real_snapshot = SMWindowExecutor._snapshot
    async def observe_snapshot(executor, **kwargs):
        snapshot = await real_snapshot(executor, **kwargs)
        budgets.append(snapshot[3]["basic_spendable_budget_usd"])
        return snapshot
    monkeypatch.setattr(SMWindowExecutor, "_snapshot", observe_snapshot)
    on_windows(v, lambda *_: None, stop_after=1)
    with history_boundary(monkeypatch, v)[0]:
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.confirmed_fills == 0
    session = [r["data"]["payload"] for r in journal_records(v.executor.journal.path) if r["kind"] == "sm_session"][-1]
    assert session["starting_official_balance_usdc"] == int(balance) / 1e6
    assert session["starting_spendable_usdc"] == int(balance) / 1e6
    assert budgets and all(b == expected for b in budgets)
    assert session["capital_basis"] == "OFFICIAL_BALANCE_AT_SESSION_START"
    assert session["historical_pending_cost_reserved_usd"] == 0
    assert session["historical_pending_cost_observed_usd"] == 0
    assert all(t["status"] == "MATCHED" for t in v.trades)
    assert path.read_bytes().startswith(before)
    future = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    assert future.sequence == 0
    future.close()


@pytest.mark.parametrize("omit", [False, True])
async def test_preexisting_foreign_order_is_operator_managed_even_if_replica_omits_it(env, omit):
    v, cfg = env
    v.capital["balance"] = "6000000"
    v.rows["external"] = external_order()
    on_windows(v, lambda *_: None, stop_after=1)
    if omit:
        async def replica_omits(_):
            v.open_rows_override = []
        v.on_sleep = replica_omits
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and v.rows["external"]["status"] == "LIVE"
    assert all("external" not in ids for ids in v.cancels)
    session = next(r["data"]["payload"] for r in journal_records(v.executor.journal.path)
                   if r["kind"] == "sm_session")
    assert session["starting_spendable_usdc"] == 6
    assert "external" in session["preflight_observed_order_ids"]
    future = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    future.close()


async def test_new_external_order_after_startup_still_reserves_collateral(env):
    v, cfg = env
    v.capital["balance"] = "6000000"
    def introduce(venue, _):
        venue.rows["external"] = external_order()
    on_windows(v, introduce)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert len(v.posts) == 2 and result.total_windows > 2
    assert result.stop_elapsed_s == 300
    assert all(w["result"]["placement_results"] == [] for w in result.windows[1:])
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_current_session_unknown_keeps_exposure_and_blocks_next_window(env):
    v, cfg = env
    v.capital["balance"] = "6000000"
    v.replies = [{"success": True, "orderID": "o-1"}, {"success": True}]
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert len(v.post_requests) == 1 and result.total_windows == 1
    assert result.final_close_barrier == "BLOCKED"
    assert [p["outcome"] for p in result.windows[0]["result"]["placement_results"]] == ["ACK", "UNKNOWN"]
    unknown = result.windows[0]["result"]["placement_results"][1]["quote"]
    assert result.windows[0]["result"]["capital_committed"] == pytest.approx(unknown["price"] * unknown["size"])
    assert result.confirmed_fills == 0


async def test_allowance_still_limits_the_pair_even_when_balance_is_sufficient(env):
    v, cfg = env
    v.capital["balance"] = "40000000"
    v.capital["allowances"] = {k: "3000000" for k in v.capital["allowances"]}
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT" and not v.posts


async def test_starting_balance_does_not_depend_on_prior_unknown_journal(env):
    v, cfg = env
    path = write_records(cfg.paths.journal_dir, traded_records(outcome="UNKNOWN"))
    before = path.read_bytes()
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED" and len(v.posts) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("capital_basis", "IGNORED"),
                                        ("historical_pending_cost_reserved_usd", 1),
                                        ("historical_pending_cost_observed_usd", 0)])
def test_prior_funding_metadata_is_not_new_session_input(tmp_path, field, value):
    rows = safe_records(retained=True)
    session = rows[0]["data"]["payload"]
    session.update(capital_basis="OFFICIAL_BALANCE_AT_SESSION_START",
                   historical_pending_cost_reserved_usd=0,
                   historical_pending_cost_observed_usd=10.15)
    session[field] = value
    path = write_records(tmp_path, rows)
    before = path.read_bytes()
    journal = RiskJournal(tmp_path, ADDRESS)
    assert journal.sequence == 0
    journal.close()
    assert path.read_bytes() == before
