"""Controlled live wrapper tests use fakes; no authenticated order path is run."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from polymaker.btc5m import slug_for_epoch
from polymaker.domain import OpenOrder, OrderState, Quote, Side
from polymaker.execution.gateway import ExecutionGateway
from polymaker.favorite_live import (
    CONTROLLED_CONFIG,
    LiveResult,
    PreflightError,
    PreflightSnapshot,
    _classify_final,
    _wallet_identity,
    attribution_report,
    collateral_readiness,
    effective_config,
    run_favorite_first_live,
    validate_meta,
    validate_preflight,
)
from polymaker.strategy.favorite_first import (
    StrategyMemory,
    StrategySnapshot,
    StrategyState,
    evaluate,
)
from tests.test_engine import _engine_with_market
from tests.test_favorite_first import _paper_engine, book

READY = PreflightSnapshot(True, True, 20, 20, 0, 0, 0, True, True, True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("credential_ready", False),
        ("wallet_ready", False),
        ("balance_usd", 11.99),
        ("allowance_usd", None),
        ("yes_position", 1),
        ("no_position", 1),
        ("owned_open_orders", 1),
        ("market_ws_ready", False),
        ("user_ws_ready", False),
        ("fill_truth_ready", False),
    ],
)
def test_preflight_gate_fails_closed(field, value):
    with pytest.raises(PreflightError):
        validate_preflight(replace(READY, **{field: value}))
    validate_preflight(READY)


def test_collateral_and_minimum_are_explicit(meta):
    assert collateral_readiness(
        {"balance": "12000000", "allowances": {"exchange": "12000000"}}, ("exchange",)
    ) == (12, 12)
    assert collateral_readiness({"balance": "12000000"}) == (12, None)
    selected = replace(
        meta, slug=slug_for_epoch(900), end_date_iso=datetime.fromtimestamp(1200, UTC).isoformat()
    )
    with pytest.raises(PreflightError, match="minimum"):
        validate_meta(replace(selected, min_order_size=6), selected.slug, 900, 5)


def test_allowance_is_for_actual_spender_only():
    required = ("0xRequired",)
    raw = {"balance": "12000000", "allowances": {
        "0xOther": "1000000000", "0xRequired": "0"}}
    assert collateral_readiness(raw, required) == (12, 0)
    with pytest.raises(PreflightError, match="allowance"):
        validate_preflight(replace(READY, allowance_usd=collateral_readiness(raw, required)[1]))
    raw["allowances"]["0xRequired"] = "12000000"
    assert collateral_readiness(raw, required) == (12, 12)
    validate_preflight(replace(READY, allowance_usd=12))
    del raw["allowances"]["0xRequired"]
    assert collateral_readiness(raw, required) == (12, None)
    assert collateral_readiness({"balance": "0", "allowances": {"0xRequired": "12000000"}},
                                required) == (0, 12)


async def test_required_spender_follows_client_order_version_and_market_risk(meta, tmp_path):
    from py_clob_client_v2.config import get_contract_config

    from polymaker.config import Config, PathsConfig

    cfg = Config(paths=PathsConfig(db=str(tmp_path / "state.db"),
                                   journal_dir=str(tmp_path / "j"), log_dir=str(tmp_path / "l")))
    gw = ExecutionGateway(cfg, paper=True)

    class Client:
        chain_id = 137

        def _ClobClient__resolve_version(self):
            return 2

    gw._client = Client()
    contracts = get_contract_config(137)
    try:
        assert await gw.required_collateral_spenders(replace(meta, neg_risk=False)) == (
            contracts.exchange_v2,)
        assert await gw.required_collateral_spenders(replace(meta, neg_risk=True)) == (
            contracts.neg_risk_exchange_v2,)
    finally:
        gw.close()


async def test_favorite_final_submit_requires_current_user_ws(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        eng.paper = False  # Exercise the controlled live guard with no network call.
        eng._user_started = True
        eng.user = SimpleNamespace(connected=True, disconnected_since=0)
        q = Quote(meta.yes.token_id, Side.BUY, 0.62, 5)
        assert eng._favorite_submit_ok(cid, [q], meta)
        first = await eng.gateway.place([q], meta,
                                        submit_guard=lambda: eng._favorite_submit_ok(cid, [q], meta))
        assert len(first) == 1
        eng.user.connected = False  # preflight had passed; grace has not elapsed
        assert not eng._favorite_submit_ok(cid, [q], meta)
        # A reprice may cancel the old order, but its final replacement guard fails.
        assert await eng.gateway.cancel([first[0].order_id])
        replacement = Quote(meta.yes.token_id, Side.BUY, 0.61, 5)
        assert not eng._favorite_submit_ok(cid, [replacement], meta)
        assert await eng.gateway.place([replacement], meta, submit_guard=lambda:
                                       eng._favorite_submit_ok(cid, [replacement], meta)) == []
        eng.user.connected = True
        assert eng._favorite_submit_ok(cid, [replacement], meta)
        assert len(await eng.gateway.place([replacement], meta, submit_guard=lambda:
                                          eng._favorite_submit_ok(cid, [replacement], meta))) == 1
        eng._fill_recovery_pending.add(cid)
        assert not eng._can_submit_market(eng._market_connectivity_generation, cid)
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


@pytest.mark.parametrize("state,halt,expected", [
    (StrategyState.HALTED, None, LiveResult.HALTED_DURING_MARKET),
    (StrategyState.COMPLETE, "RISK_HALT:daily_loss", LiveResult.HALTED_DURING_MARKET),
    (StrategyState.COMPLETE, "MARKET_METADATA_HALT", LiveResult.HALTED_DURING_MARKET),
    (StrategyState.COMPLETE, None, LiveResult.COMPLETED),
])
def test_final_classification_uses_engine_halt(state, halt, expected):
    cid = "selected"
    engine = SimpleNamespace(
        _favorite={cid: (0, CONTROLLED_CONFIG, StrategyMemory(state=state))},
        favorite_halt_reason=lambda _cid: halt,
    )
    final = dict(owned_open_orders=0, fill_truth_complete=True, position_reconciled=True)
    assert _classify_final(engine, cid, final, {"paired_locked_pnl": -0.10}) is expected
    assert _classify_final(engine, cid, {**final, "owned_open_orders": 1}, {}) \
        is LiveResult.INCOMPLETE_RECONCILIATION


def test_wallet_signer_and_funder_mismatch_fails_closed():
    from eth_account import Account

    key = "0x" + "1" * 64
    signer = Account.from_key(key).address
    cfg = SimpleNamespace(
        secrets=SimpleNamespace(pk=key, browser_address=signer),
        wallet=SimpleNamespace(signature_type=0),
    )
    engine = SimpleNamespace(gateway=SimpleNamespace(address=signer, funder=signer, creds=object()))
    assert _wallet_identity(cfg, engine)
    engine.gateway.address = "0x" + "2" * 40
    assert not _wallet_identity(cfg, engine)
    engine.gateway.address = signer
    cfg.secrets.browser_address = "0x" + "3" * 40
    assert not _wallet_identity(cfg, engine)


def test_obsolete_probation_offset_is_optional_and_never_quotes_during_window():
    assert CONTROLLED_CONFIG.probation_passive_offset_ticks == 0
    effective = effective_config(CONTROLLED_CONFIG)
    assert "probation_passive_offset_ticks" not in effective
    assert effective["first_leg_risk_window_s"] == 20
    assert effective["max_pair_loss_usd"] == 0.50
    assert effective["runner_passive_offset_ticks"] == 2
    assert effective["normal_reprice_min_s"] == 0.75
    assert effective["urgent_reprice_min_s"] == 0.30
    s = StrategySnapshot(
        41,
        0,
        book(0.62, 0.63),
        book(0.37, 0.38),
        5,
        0,
        0.62,
        0,
        "yes",
        "no",
        0.01,
        first_fill_ts=40,
        first_leg_side="YES",
        venue_min_order_size=5,
    )
    d = evaluate(s, StrategyMemory(), CONTROLLED_CONFIG)
    assert d.memory.state is StrategyState.FIRST_LEG_RISK_WINDOW and d.quote is None


async def test_disarmed_engine_quoter_cannot_place_before_preflight(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    cid = meta.condition_id
    try:
        eng.attach_favorite_first(cid, 900, CONTROLLED_CONFIG)
        eng._favorite_bootstrap = (CONTROLLED_CONFIG, 900)
        eng._favorite_armed = False
        await eng._recompute(cid)
        assert not eng.state.orders and eng._favorite[cid][2] == StrategyMemory()
        assert not eng._favorite_submit_ok(cid, [], meta)
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_favorite_startup_cleans_selected_orders_only(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    selected = OpenOrder("selected", meta.yes.token_id, Side.BUY, 0.62, 5, OrderState.LIVE)
    foreign = OpenOrder("foreign", "other-market-token", Side.BUY, 0.2, 5, OrderState.LIVE)

    class Gateway:
        def __init__(self):
            self.active = [selected, foreign]
            self.cancel_all_called = False

        async def open_orders(self):
            return list(self.active)

        async def cancel(self, ids):
            self.active = [o for o in self.active if o.order_id not in ids]
            return True

        async def cancel_all(self):
            self.cancel_all_called = True

        async def positions(self):
            return {}

    gateway = Gateway()
    eng.gateway.close()
    eng.gateway = gateway
    eng._favorite_bootstrap = (CONTROLLED_CONFIG, 900)
    eng.state.upsert_order(selected)
    try:
        await eng._startup_reconcile()
        assert gateway.active == [foreign] and not gateway.cancel_all_called
        assert not eng.state.orders_for(meta.yes.token_id)
    finally:
        eng.state.close()
        eng.catalog.close()


class Clock:
    def __init__(self):
        self.value = 600.0

    def now(self):
        return self.value

    async def sleep(self, seconds):
        self.value += seconds


def setup_session(meta, monkeypatch, tmp_path):
    epoch = 900
    selected = replace(
        meta,
        slug=slug_for_epoch(epoch),
        end_date_iso=datetime.fromtimestamp(epoch + 300, UTC).isoformat(),
    )
    cfg = SimpleNamespace(
        secrets=SimpleNamespace(pk="present", browser_address="0x" + "1" * 40),
        wallet=SimpleNamespace(signature_type=2),
        engine=SimpleNamespace(heartbeat=True, journal=True),
        paths=SimpleNamespace(journal_dir=str(tmp_path)),
        markets=[],
    )
    created = []

    class FakeEngine:
        def __init__(self, cfg, *, paper, favorite_first, market_start_ts):
            assert not paper and market_start_ts == epoch
            created.append(self)
            self.cfg = cfg
            self.metas = {selected.condition_id: selected}
            self.gateway = SimpleNamespace(address="0x" + "1" * 40, funder="0x" + "1" * 40)
            self._favorite = {
                selected.condition_id: (
                    epoch,
                    favorite_first,
                    StrategyMemory(state=StrategyState.COMPLETE),
                )
            }
            self.armed = False
            self.stopped = False
            self.final_calls = 0

        async def start(self, *, arm_favorite):
            assert not arm_favorite

        def arm_favorite_first(self, cid):
            assert cid == selected.condition_id
            self.armed = True

        def disarm_favorite_first(self, cid):
            assert cid == selected.condition_id
            self.armed = False

        def favorite_halt_reason(self, cid):
            assert cid == selected.condition_id
            return None

        async def finalize_favorite_market(self, cid):
            self.final_calls += 1
            return dict(
                owned_open_orders=0,
                fill_truth_complete=True,
                position_reconciled=True,
                yes_qty=0,
                no_qty=0,
                economics=dict(first_qty=0, pair_qty=0, first_side=None, first_vwap=0, pair_vwap=0),
            )

        async def shutdown(self):
            self.stopped = True

    async def preflight(*_args, **_kwargs):
        return READY

    monkeypatch.setattr("polymaker.favorite_live._preflight", preflight)
    monkeypatch.setattr("polymaker.favorite_live.attribution_report", lambda *_args: {"safe": True})
    return selected, cfg, FakeEngine, created


async def test_preflight_only_exits_without_arming(meta, monkeypatch, tmp_path):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    clock = Clock()
    seen = []

    async def resolver(_cfg, slug):
        seen.append(slug)
        return selected

    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=True,
        now=clock.now,
        sleep=clock.sleep,
        config_loader=lambda _: cfg,
        resolver=resolver,
        engine_factory=factory,
    )
    assert result is LiveResult.PREFLIGHT_PASS
    assert seen == [selected.slug] and len(created) == 1
    assert created[0].stopped and not created[0].armed


@pytest.mark.parametrize(
    "field,value",
    [
        ("credential_ready", False),
        ("wallet_ready", False),
        ("balance_usd", 0),
        ("allowance_usd", 0),
        ("yes_position", 1),
        ("no_position", 1),
        ("owned_open_orders", 1),
        ("market_ws_ready", False),
        ("user_ws_ready", False),
        ("fill_truth_ready", False),
    ],
)
async def test_live_wrapper_never_arms_when_preflight_fails(
    meta, monkeypatch, tmp_path, field, value
):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    clock = Clock()

    async def preflight(*_args, **_kwargs):
        validate_preflight(replace(READY, **{field: value}))

    async def resolver(_cfg, _slug):
        return selected

    monkeypatch.setattr("polymaker.favorite_live._preflight", preflight)
    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=False,
        now=clock.now,
        sleep=clock.sleep,
        config_loader=lambda _: cfg,
        resolver=resolver,
        engine_factory=factory,
    )
    assert result is LiveResult.ABORTED_BEFORE_TRADING
    assert len(created) == 1 and not created[0].armed and created[0].stopped


async def test_one_market_arms_once_and_never_discovers_next(meta, monkeypatch, tmp_path):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    clock = Clock()
    seen = []

    async def resolver(_cfg, slug):
        seen.append(slug)
        assert slug == selected.slug
        return selected

    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=False,
        now=clock.now,
        sleep=clock.sleep,
        config_loader=lambda _: cfg,
        resolver=resolver,
        engine_factory=factory,
    )
    assert result is LiveResult.COMPLETED
    assert len(created) == 1 and not created[0].armed and created[0].stopped
    assert seen == [selected.slug, selected.slug]
    assert clock.value == 1200


async def test_committed_market_identity_change_aborts_without_arming(meta, monkeypatch, tmp_path):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    clock = Clock()
    calls = 0

    async def resolver(_cfg, _slug):
        nonlocal calls
        calls += 1
        return selected if calls == 1 else replace(selected, condition_id="other")

    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=False,
        now=clock.now,
        sleep=clock.sleep,
        config_loader=lambda _: cfg,
        resolver=resolver,
        engine_factory=factory,
    )
    assert result is LiveResult.ABORTED_BEFORE_TRADING
    assert calls == 2 and len(created) == 1 and not created[0].armed


async def test_terminal_residual_is_successful_one_market_outcome(meta, monkeypatch, tmp_path):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    clock = Clock()

    async def resolver(_cfg, _slug):
        return selected

    monkeypatch.setattr(
        "polymaker.favorite_live.attribution_report",
        lambda *_args: {"decision": "UNPAIRABLE_RESIDUAL"},
    )
    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=False,
        now=clock.now,
        sleep=clock.sleep,
        config_loader=lambda _: cfg,
        resolver=resolver,
        engine_factory=factory,
    )
    assert result is LiveResult.UNPAIRABLE_RESIDUAL
    assert len(created) == 1 and not created[0].armed


async def test_t270_residual_is_reported_without_engineering_failure(meta, monkeypatch, tmp_path):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    clock = Clock()

    async def resolver(_cfg, _slug):
        return selected

    original_factory = factory

    def halted_factory(*args, **kwargs):
        engine = original_factory(*args, **kwargs)
        engine._favorite[selected.condition_id] = (
            900,
            CONTROLLED_CONFIG,
            StrategyMemory(state=StrategyState.HALTED, residual_reason="T270_RESIDUAL_INVENTORY"),
        )
        return engine

    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=False,
        now=clock.now,
        sleep=clock.sleep,
        config_loader=lambda _: cfg,
        resolver=resolver,
        engine_factory=halted_factory,
    )
    assert result is LiveResult.COMPLETED and len(created) == 1


async def test_armed_lifecycle_error_still_runs_scoped_finalization(meta, monkeypatch, tmp_path):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    clock = Clock()

    async def resolver(_cfg, _slug):
        return selected

    async def interrupted_sleep(_seconds):
        raise RuntimeError("simulated market loop failure")

    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=False,
        now=clock.now,
        sleep=interrupted_sleep,
        config_loader=lambda _: cfg,
        resolver=resolver,
        engine_factory=factory,
    )
    assert result is LiveResult.INCOMPLETE_RECONCILIATION
    assert len(created) == 1 and not created[0].armed and created[0].stopped
    assert created[0].final_calls == 1


@pytest.mark.parametrize("remaining", [0, 1])
async def test_task_cancel_runs_verified_finalization_before_shutdown(
    meta, monkeypatch, tmp_path, remaining
):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    events = []

    async def lifecycle_sleep(_seconds):
        entered.set()
        await release.wait()

    async def finalize(self, cid):
        assert cid == selected.condition_id
        assert not self.armed
        self.final_calls += 1
        events.extend(("cancel_selected", "verify_open_orders", "reconcile_fills",
                       "verify_positions"))
        cleanup_started.set()
        await cleanup_release.wait()
        return dict(
            owned_open_orders=remaining,
            fill_truth_complete=True,
            position_reconciled=True,
            yes_qty=0, no_qty=0,
            economics=dict(first_qty=0, pair_qty=0, first_side=None, first_vwap=0,
                           pair_vwap=0),
        )

    original_shutdown = factory.shutdown

    async def shutdown(self):
        events.append("shutdown")
        await original_shutdown(self)

    factory.finalize_favorite_market = finalize
    factory.shutdown = shutdown

    async def resolver(_cfg, _slug):
        return selected

    output = io.StringIO()
    task = asyncio.create_task(run_favorite_first_live(
        "ignored", Console(file=output), preflight_only=False,
        now=Clock().now, sleep=lifecycle_sleep,
        config_loader=lambda _: cfg, resolver=resolver, engine_factory=factory,
    ))
    await entered.wait()
    task.cancel()
    await cleanup_started.wait()
    assert not task.done()  # original cancellation did not abort cleanup
    cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].final_calls == 1
    assert created[0].stopped
    assert ("INCOMPLETE_RECONCILIATION" if remaining else "COMPLETED") in output.getvalue()
    assert events == ["cancel_selected", "verify_open_orders", "reconcile_fills",
                      "verify_positions", "shutdown"]


async def test_engine_finalization_failed_cancel_keeps_authoritative_open_order(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    resting = OpenOrder("owned", meta.yes.token_id, Side.BUY, 0.62, 5, OrderState.LIVE)
    events = []

    class Gateway:
        async def cancel_asset(self, token):
            events.append(("cancel", token))
            return False

        async def open_orders(self):
            events.append(("open_orders", None))
            return [resting]

        async def positions(self):
            events.append(("positions", None))
            return {}

    original_gateway = eng.gateway
    eng.gateway = Gateway()
    eng.state.upsert_order(resting)
    try:
        final = await eng.finalize_favorite_market(meta.condition_id)
        assert not eng._favorite_armed
        assert final["owned_open_orders"] == 1
        assert final["position_reconciled"]
        assert events == [("cancel", meta.yes.token_id), ("cancel", meta.no.token_id),
                          ("open_orders", None), ("positions", None)]
        assert _classify_final(eng, meta.condition_id, final, {}) \
            is LiveResult.INCOMPLETE_RECONCILIATION
    finally:
        original_gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_engine_records_risk_halt_independent_of_strategy_memory(tmp_path, meta):
    from polymaker.risk.manager import RiskDecision

    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    eng.risk.evaluate = lambda *_args, **_kwargs: RiskDecision(True, False, 0, "daily_loss")
    try:
        await eng._recompute(cid)
        assert eng.favorite_halt_reason(cid) == "RISK_HALT:daily_loss"
        assert eng._favorite[cid][2].state is not StrategyState.HALTED
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_engine_records_metadata_halt_after_metadata_recovers(tmp_path, meta, monkeypatch):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id

    class Gamma:
        def __init__(self, _host):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def markets_by_condition(self, _cids):
            return {cid: {"closed": True}}

    monkeypatch.setattr("polymaker.engine.GammaClient", Gamma)
    try:
        await eng.refresh_market_metadata()
        assert eng.favorite_halt_reason(cid) == "MARKET_METADATA_HALT"
        eng._halted.clear()  # even a subsequent healthy metadata refresh cannot erase history
        assert eng.favorite_halt_reason(cid) == "MARKET_METADATA_HALT"
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_finalization_is_once_when_cancel_arrives_during_cleanup(
    meta, monkeypatch, tmp_path
):
    selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def finalize(self, _cid):
        assert not self.armed
        self.final_calls += 1
        started.set()
        await release.wait()
        return dict(owned_open_orders=0, fill_truth_complete=True,
                    position_reconciled=True, yes_qty=0, no_qty=0,
                    economics=dict(first_qty=0, pair_qty=0, first_side=None,
                                   first_vwap=0, pair_vwap=0))

    factory.finalize_favorite_market = finalize

    async def resolver(_cfg, _slug):
        return selected

    clock = Clock()
    task = asyncio.create_task(run_favorite_first_live(
        "ignored", Console(quiet=True), preflight_only=False,
        now=clock.now, sleep=clock.sleep,
        config_loader=lambda _: cfg, resolver=resolver, engine_factory=factory,
    ))
    await started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].final_calls == 1 and created[0].stopped


@pytest.mark.parametrize("missing", ["pk", "browser_address"])
async def test_missing_credential_aborts_before_engine(meta, monkeypatch, tmp_path, missing):
    _selected, cfg, factory, created = setup_session(meta, monkeypatch, tmp_path)
    setattr(cfg.secrets, missing, "")
    result = await run_favorite_first_live(
        "ignored",
        Console(quiet=True),
        preflight_only=True,
        config_loader=lambda _: cfg,
        engine_factory=factory,
    )
    assert result is LiveResult.ABORTED_BEFORE_TRADING and not created


def test_attribution_report_separates_locked_from_unsettled(tmp_path, meta):
    epoch = 900
    selected = replace(meta, slug=slug_for_epoch(epoch))
    journal = Path(tmp_path) / "live.jsonl"
    rows = [
        (
            936,
            {
                "strategy_state": "ENTRY_QUOTING",
                "favorite_side": "YES",
                "favorite_probability": 0.63,
                "desired_price": 0.62,
            },
        ),
        (
            960,
            {
                "strategy_state": "PAIR_STOP",
                "total_lockable_pnl": -0.05,
                "pair_quote_mode": "AGGRESSIVE",
                "desired_price": 0.40,
            },
        ),
    ]
    journal.write_text(
        "".join(
            json.dumps(
                {
                    "ts": ts,
                    "kind": "favorite_first_decision",
                    "data": {"cid": selected.condition_id, **data},
                }
            )
            + "\n"
            for ts, data in rows
        )
    )
    first = [
        SimpleNamespace(ts=940, size=2, price=0.62),
        SimpleNamespace(ts=943, size=3, price=0.62),
    ]
    pair = [SimpleNamespace(ts=965, size=2, price=0.40)]

    class Fills:
        def economic_fills_for_orders(self, ids):
            return first if "first" in ids else pair

    engine = SimpleNamespace(
        cfg=SimpleNamespace(paths=SimpleNamespace(journal_dir=str(tmp_path))),
        state=SimpleNamespace(
            owned_order=lambda oid: {
                "created_ts": 936 if oid == "first" else 960,
                "submitted_qty": 5,
                "submitted_price": 0.62 if oid == "first" else 0.40,
            }
        ),
        user_proc=Fills(),
        _favorite_first_orders={selected.condition_id: {"first"}},
        _favorite_pair_orders={selected.condition_id: {"pair"}},
        _favorite={
            selected.condition_id: (
                epoch,
                CONTROLLED_CONFIG,
                StrategyMemory(state=StrategyState.PAIR_STOP, first_leg_final_qty=5),
            )
        },
    )
    final = dict(
        yes_qty=5,
        no_qty=2,
        owned_open_orders=0,
        fill_truth_complete=True,
        position_reconciled=True,
        economics=dict(first_qty=5, pair_qty=2, first_vwap=0.62, pair_vwap=0.40),
    )
    report = attribution_report(selected, epoch, CONTROLLED_CONFIG, engine, final, 600)
    assert report["paired_locked_pnl"] == pytest.approx(-0.04)
    assert report["residual_qty"] == 3
    assert report["total_settled_pnl"] is None
    assert report["first_quote_reprices"] == 0
    assert report["decision"] == "PAIR_STOP"
