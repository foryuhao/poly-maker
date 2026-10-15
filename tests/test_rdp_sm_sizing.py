"""RDP-SIZE-001: pure sizing and actual M1/M3/M2/SDK with mock official data."""

from dataclasses import replace
from decimal import ROUND_CEILING, Decimal

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPSMConfig
from polymaker.rdp_sm import NORMAL_END_S, _market, inventory_trading_state
from polymaker.rdp_sm_execution import RiskJournal, maker_buy_size
from polymaker.rdp_sm_loop import QuoteUnavailable, make_quotes
from polymaker.rdp_sm_sizing import resolve_favorite_target, validate_market_sizing
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import ADDRESS, CID, SLUG, START, Venue
from test_rdp_sm_m3_loop import current_orders, on_windows, runner
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_trading_phases import advance

D = Decimal


def book_pair(p=".75", *, tick=".001", stamp=START + 15):
    p, tick = D(p), D(tick)
    return {token: {"token_id": token, "market_id": CID, "best_bid": str(mid - tick),
                    "best_ask": str(mid + tick), "tick": str(tick), "timestamp": stamp}
            for token, mid in (("1", p), ("2", 1 - p))}


def decision(delta=0, p=".75", *, cfg=None, books=None, minimum=5):
    meta, _ = _market(Venue().raw, SLUG)
    meta = replace(meta, min_order_size=minimum)
    evidence = {}
    state, quotes = make_quotes(meta, books or book_pair(p), max(D(str(delta)), 0),
        max(-D(str(delta)), 0), "w", cfg or RDPSMConfig(target_delta=1),
        now=START + 15, evidence=evidence)
    return state, {q.outcome: D(str(q.size)) for q in quotes}, evidence, quotes


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def configure(env, target=1, p=".75", **kw):
    v, cfg = env
    cfg.rdp_sm = RDPSMConfig(target_delta=target, **kw)
    for token, b in book_pair(p).items():
        v.books[token]["bids"][0]["price"] = b["best_bid"]
        v.books[token]["asks"][0]["price"] = b["best_ask"]
        v.books[token]["tick_size"] = b["tick"]
        v.ticks[token] = b["tick"]
    return v, cfg


def test_defaults():
    cfg = RDPSMConfig()
    assert (cfg.base_size, cfg.target_delta, cfg.favorite_threshold, cfg.max_order_size) == (
        D(7), D(0), D(".70"), D(15))


@pytest.mark.parametrize("bad", [
    {"base_size": 0}, {"base_size": -1}, {"base_size": "NaN"}, {"base_size": "5.001"},
    {"target_delta": -1}, {"target_delta": 2}, {"target_delta": ".5"}, {"target_delta": "Infinity"},
    {"favorite_threshold": ".5"}, {"favorite_threshold": 1}, {"favorite_threshold": "NaN"},
    {"max_order_size": 6}, {"max_order_size": "15.001"}, {"max_order_size": "Infinity"},
])
def test_invalid_config_rejected(bad):
    with pytest.raises(ValidationError):
        RDPSMConfig(**bad)


def test_official_minimum_rejects_config_without_clamping():
    cfg = RDPSMConfig(base_size=7)
    with pytest.raises(ValueError, match="BASE_BELOW_MARKET_MINIMUM"):
        validate_market_sizing(cfg, D(8))
    assert cfg.base_size == 7


def test_config_load_a_b_and_legacy(tmp_path):
    for base, target in ((5, 0), (7, 0), (7, 1)):
        (tmp_path / "config.toml").write_text(
            f'[rdp_sm]\nbase_size="{base}"\ntarget_delta="{target}"\n'
            'favorite_threshold="0.70"\nmax_order_size="15"\n')
        cfg = Config.load(tmp_path, load_env=False).rdp_sm
        assert cfg.base_size == base and cfg.target_delta == target
        assert cfg.favorite_threshold == D(".70")


@pytest.mark.parametrize("p,target", [(".69", 0), (".70", 0), (".71", 1),
    (".29", -1), (".30", 0), (".50", 0)])
@pytest.mark.parametrize("tick", [".01", ".001", ".0001"])
def test_favorite_strict_threshold_and_dynamic_tick(p, target, tick):
    _, _, e, _ = decision(p=p, books=book_pair(p, tick=tick))
    assert D(e["signed_target_delta"]) == target
    assert e["favorite_data_validity"] == "VALID"


@pytest.mark.parametrize("fault", ["missing", "stale", "future", "wrong_token", "asynchronous",
    "inconsistent", "crossed", "nan", "tick_mismatch", "tick_unknown"])
def test_favorite_invalid_never_reuses_direction(fault):
    meta, _ = _market(Venue().raw, SLUG)
    books = book_pair()
    if fault == "missing":
        del books["2"]
    elif fault == "stale":
        books["2"]["timestamp"] -= 11
    elif fault == "future":
        books["2"]["timestamp"] += 1
    elif fault == "wrong_token":
        books["1"]["token_id"] = "99"
    elif fault == "asynchronous":
        books["2"]["timestamp"] -= 2
    elif fault == "inconsistent":
        books["2"]["best_bid"], books["2"]["best_ask"] = ".399", ".401"
    elif fault == "crossed":
        books["2"]["best_ask"] = ".2"
    elif fault == "nan":
        books["1"]["best_bid"] = "NaN"
    elif fault == "tick_mismatch":
        books["1"]["tick"] = ".01"
    else:
        books["1"]["tick"] = ".03"
    e = resolve_favorite_target(meta, books, RDPSMConfig(target_delta=1), now=START + 15)
    assert e["signed_target_delta"] == "0" and e["favorite_side"] == "NONE"
    assert e["favorite_data_validity"] == "INVALID"


@pytest.mark.parametrize("base", [5, 7])
@pytest.mark.parametrize("p", [".25", ".5", ".75"])
@pytest.mark.parametrize("delta", ["0", "2", "-3", "4.49999", "-4.49999"])
def test_target_zero_exact_normal_skew_parity(base, p, delta):
    state, sizes, e, _ = decision(delta, p, cfg=RDPSMConfig(base_size=base, target_delta=0))
    d = D(delta)
    assert state == "NORMAL"
    assert sizes == {s: q.quantize(D(".01"), rounding=ROUND_CEILING) for s, q in
                     {"YES": D(base) + max(-d, 0), "NO": D(base) + max(d, 0)}.items()}
    assert e["favorite_data_validity"] == "DISABLED" and e["signed_target_delta"] == "0"


@pytest.mark.parametrize("delta,p,expected", [(0, ".75", (7, 6)), (0, ".25", (6, 7)),
    (0, ".55", (7, 7)), (2, ".75", (7, 8)), (-2, ".25", (8, 7)),
    (2, ".25", (6, 9)), (-2, ".75", (9, 6))])
def test_normal_target_respects_actual_inventory(delta, p, expected):
    state, sizes, e, _ = decision(delta, p)
    assert state == "NORMAL" and (sizes["YES"], sizes["NO"]) == expected
    assert D(delta) + sizes["YES"] - sizes["NO"] == D(e["signed_target_delta"])


@pytest.mark.parametrize("delta,p,side,size", [(7, ".75", "NO", 6), (7, ".55", "NO", 7),
    (7, ".25", "NO", 8), (-7, ".75", "YES", 8), (-7, ".25", "YES", 6),
    (5, ".75", "NO", 5)])
def test_rebalance_examples_and_minimum_fallback(delta, p, side, size):
    state, sizes, e, quotes = decision(delta, p)
    assert state == "REBALANCE" and sizes == {side: D(size)}
    assert quotes[0].price == float(book_pair(p)[quotes[0].token_id]["best_bid"])
    if delta == 5:
        assert e["ideal_repair_size"] == "4"
        assert e["repair_sizing_fallback"] != ["NONE"]


@pytest.mark.parametrize("delta", ["5.003207", "-7.001", "15", "-15"])
def test_target_zero_rebalance_parity_inside_configured_limit(delta):
    _, sizes, _, quotes = decision(delta, cfg=RDPSMConfig(target_delta=0, max_order_size=30))
    q = quotes[0]
    assert sizes[q.outcome] == maker_buy_size(D(str(q.price)), abs(D(delta)), D(5))


def test_normal_minimum_and_maximum_constraints():
    _, sizes, e, _ = decision(0, cfg=RDPSMConfig(base_size=5, target_delta=1))
    assert sizes == {"YES": 5, "NO": 5} and "MINIMUM_ORDER_FALLBACK" in e["sizing_constraint_reason"]
    _, sizes, e, _ = decision(4, cfg=RDPSMConfig(base_size=15, target_delta=1))
    assert max(sizes.values()) == 15 and "MAX_ORDER_SIZE" in e["sizing_constraint_reason"]
    assert sizes == {"YES": 12, "NO": 15}
    _, sizes, _, _ = decision(7, minimum=6)
    assert sizes == {"NO": 6}


def test_minimum_notional_exceeding_maximum_skips():
    with pytest.raises(QuoteUnavailable, match="MINIMUM_ORDER_EXCEEDS_MAXIMUM"):
        decision(7, p=".98")


@pytest.mark.parametrize("delta,state", [("4.499", "NORMAL"), ("4.5", "REBALANCE"),
    ("5", "REBALANCE"), ("-4.5", "REBALANCE")])
def test_current_authorized_state_and_timing_are_unchanged(delta, state):
    assert NORMAL_END_S == 120
    assert inventory_trading_state(D(delta)) == state
    assert decision(delta)[0] == state


@pytest.mark.parametrize("p,expected", [(".75", 6), (".55", 7), (".25", 8)])
async def test_actual_late_repair_uses_configured_target_and_full_confirmation(env, p, expected):
    v, _ = configure(env, p=".75" if p == ".25" else p)
    def action(v, index):
        if index == 1:
            v.fill(current_orders(v)["YES"], "first-seven", 7)
            for token, b in book_pair(p).items():
                v.books[token]["bids"][0]["price"] = b["best_bid"]
                v.books[token]["asks"][0]["price"] = b["best_ask"]
            advance(v, 200)
        elif index == 2:
            v.fill(current_orders(v)["NO"], "repair", expected)
    on_windows(v, action, stop_after=2)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE"]
    w = result.windows[1]
    q = w["result"]["requested_quotes"][0]
    assert q["outcome"] == "NO" and q["size"] == expected
    assert q["price"] == float(book_pair(p)["2"]["best_bid"])
    assert result.delta == 7 - expected
    assert result.sizing_report["intentional_residual_shares"] == (0 if p == ".55" else 1)
    assert result.sizing_report["maker_filled_shares"] == 7 + expected
    assert D(w["sizing"]["confirmed_no_fill"]) == expected
    assert result.sizing_report["trading_pnl_before_rebate"] == "UNKNOWN"
    assert all(p["postOnly"] for batch in v.post_requests for p in batch)


async def test_partial_repair_uses_only_confirmed_three_then_returns_normal(env):
    v, _ = configure(env)
    def action(v, index):
        if index == 1:
            v.fill(current_orders(v)["YES"], "seven", 7)
        elif index == 2:
            v.fill(current_orders(v)["NO"], "partial-three", 3)
    on_windows(v, action, stop_after=3)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    assert result.delta == 4 and result.confirmed_no == 3
    assert D(result.windows[1]["sizing"]["actual_repair_size"]) == 6
    assert D(result.windows[1]["sizing"]["confirmed_no_fill"]) == 3
    assert result.sizing_report["intentional_residual_shares"] == 0
    assert result.sizing_report["unintentional_residual_shares"] == 4


@pytest.mark.parametrize("kind", ["complete", "partial", "one_side"])
async def test_intentional_requires_decision_and_complete_fills(env, kind):
    v, _ = configure(env)
    def action(v, index):
        ids = current_orders(v)
        if kind == "complete":
            v.fill(ids["YES"], "y", 7)
            v.fill(ids["NO"], "n", 6)
        elif kind == "partial":
            v.fill(ids["YES"], "y", 4)
            v.fill(ids["NO"], "n", 3)
        else:
            v.fill(ids["YES"], "y", 7)
    on_windows(v, action, stop_after=1)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    report = result.sizing_report
    assert report["intentional_residual_shares"] == (1 if kind == "complete" else 0)
    assert report["unintentional_residual_shares"] == (0 if kind == "complete" else abs(result.delta))
    assert bool(report["intentional_residual_evidence"]) == (kind == "complete")


async def test_later_same_side_partial_never_enlarges_intentional_certificate(env):
    v, _ = configure(env)
    def action(v, index):
        ids = current_orders(v)
        if index == 1:
            v.fill(ids["YES"], "y", 7)
            v.fill(ids["NO"], "n", 6)
        else:
            v.fill(ids["YES"], "partial", 1)
    on_windows(v, action, stop_after=2)
    result = await runner(env).run(offline=True)
    assert result.delta == 2 and result.sizing_report["intentional_residual_shares"] == 1
    assert result.sizing_report["unintentional_residual_shares"] == 1


async def test_base_seven_does_not_bypass_capital_gate(env):
    v, _ = configure(env, target=0, p=".50")
    v.capital["balance"] = "6000000"
    on_windows(v, lambda *_: None, stop_after=1)
    result = await runner(env).run(offline=True)
    assert not v.post_requests and result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_new_zero_attempt_report_is_independent_of_next_session(env):
    v, cfg = configure(env, target=0, p=".5")
    v.capital["balance"] = "6000000"
    on_windows(v, lambda *_: None, stop_after=1)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "BLOCKED" and not v.post_requests
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    assert journal.sequence == 0
    journal.close()


async def test_favorite_change_at_final_admission_updates_sizes_only(env, monkeypatch):
    v, _ = configure(env)
    on_windows(v, lambda *_: None, stop_after=1)
    reads = {"1": 0, "2": 0}
    def mutate(request):
        if request.url.path == "/book" and v.executor is not None:
            token = request.url.params["token_id"]
            reads[token] += 1
            if reads[token] >= 2:
                b = book_pair(".25")[token]
                v.books[token]["bids"][0]["price"] = b["best_bid"]
                v.books[token]["asks"][0]["price"] = b["best_ask"]
        return None
    with boundary(monkeypatch, v, mutate):
        result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert {q["outcome"]: q["size"] for q in result.windows[0]["result"]["requested_quotes"]} == {"YES": 6, "NO": 7}
    assert result.windows[0]["sizing"]["favorite_side"] == "NO"
    assert len(v.post_requests) == 1


async def test_market_minimum_invalid_configuration_rejects_before_session(env):
    v, _ = configure(env, target=0)
    v.raw["orderMinSize"] = "8"
    result = await runner(env).run(offline=True)
    assert result.execution_status == "BLOCKED"
    assert result.stop_reason == "SIZING_BASE_BELOW_MARKET_MINIMUM"
    assert not v.post_requests and v.executor is None


async def test_forged_refresh_quantity_cannot_bypass_shared_sizing(env):
    from test_rdp_sm_m2_execution import open_executor, quotes

    v, cfg = configure(env, target=0)
    _, executor = await open_executor(env)
    async with executor.lock:
        await executor._snapshot()
    def forged(meta, books, wid):
        return [replace(q, size=8) for q in quotes(wid, size=7)]
    result = await executor.execute_window("w1", quotes(size=7),
        quote_revision=executor.revision, quote_factory=forged)
    assert result.block_reason == "STRATEGY_QUOTE_SIZE_CHANGED" and not v.post_requests


async def test_executor_rejects_order_above_configured_maximum(env):
    from test_rdp_sm_m2_execution import open_executor, quotes

    v, _ = configure(env, target=0)
    _, executor = await open_executor(env)
    result = await executor.execute_window("w1", quotes(size=16))
    assert result.block_reason == "QUOTE_SIZE_INVALID" and not v.post_requests


@pytest.mark.parametrize("p", [".25", ".75"])
async def test_production_entry_config_a_ignores_favorite(env, p):
    from polymaker.rdp_sm_entry import run_current_market

    v, cfg = configure(env, target=0, p=p)
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert {q["outcome"]: q["size"] for q in result.windows[0]["result"]["requested_quotes"]} == {"YES": 7, "NO": 7}
    assert result.windows[0]["sizing"]["favorite_data_validity"] == "DISABLED"


def test_threshold_is_configurable_and_inactive_when_target_zero():
    assert decision(p=".71", cfg=RDPSMConfig(target_delta=1, favorite_threshold=".8"))[2]["signed_target_delta"] == "0"
    assert decision(p=".71", cfg=RDPSMConfig(target_delta=1, favorite_threshold=".6"))[2]["signed_target_delta"] == "1"
    assert decision(p=".71", cfg=RDPSMConfig(target_delta=0, favorite_threshold=".6"))[1] == {"YES": 7, "NO": 7}


def test_favorite_wrong_market_and_exact_crosscheck_boundary():
    meta, _ = _market(Venue().raw, SLUG)
    books = book_pair()
    books["2"]["market_id"] = "wrong-market"
    assert resolve_favorite_target(meta, books, RDPSMConfig(target_delta=1), now=START + 15)["signed_target_delta"] == "0"
    for offset, valid in ((".02", True), (".0201", False)):
        books = book_pair()
        for key in ("best_bid", "best_ask"):
            books["2"][key] = str(D(books["2"][key]) + D(offset))
        result = resolve_favorite_target(meta, books, RDPSMConfig(target_delta=1), now=START + 15)
        assert (result["favorite_data_validity"] == "VALID") == valid


@pytest.mark.parametrize("qty,expected", [(".5", ".5"), ("1", "0"), ("2", "0")])
async def test_opposing_confirmed_fills_consume_intentional_first(env, qty, expected):
    v, _ = configure(env)
    def action(v, index):
        ids = current_orders(v)
        if index == 1:
            v.fill(ids["YES"], "y", 7)
            v.fill(ids["NO"], "n", 6)
        else:
            v.fill(ids["NO"], "opposing", float(qty))
    on_windows(v, action, stop_after=2)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert D(str(result.sizing_report["intentional_residual_shares"])) == D(expected)
    assert D(str(result.sizing_report["unintentional_residual_shares"])) == abs(D(1) - D(qty)) - D(expected)


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("p", [".25", ".5", ".75"])
def test_normal_maximum_chooses_closest_target_by_reducing_risk(side, p):
    _, sizes, e, _ = decision(4 * side, p=p,
        cfg=RDPSMConfig(base_size=15, target_delta=1))
    assert 4 * side + sizes["YES"] - sizes["NO"] == D(e["signed_target_delta"])
    assert min(sizes.values()) >= 5 and max(sizes.values()) <= 15


@pytest.mark.parametrize("fault", ["unknown_field", "claimed_fill", "claimed_intentional", "false_barrier"])
def test_prior_sizing_report_cannot_gate_new_session(tmp_path, fault):
    from test_rdp_sm_historical_risk import safe_records, write_records

    rows = safe_records()
    report = rows[-1]["data"]["payload"]["sizing_report"]
    if fault == "unknown_field":
        report["surprise"] = 0
    elif fault == "claimed_fill":
        report["maker_fill_count"] = 1
    elif fault == "claimed_intentional":
        report["intentional_residual_shares"] = 1
    else:
        report["safe_exit_status"] = "BLOCKED"
    write_records(tmp_path, rows)
    journal = RiskJournal(tmp_path, ADDRESS)
    assert journal.sequence == 0
    journal.close()
