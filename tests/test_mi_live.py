"""M2A read-only mapping and NO-SEND safety tests; no network calls."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

from polymaker import mi_live
from polymaker.btc5m import slug_for_epoch
from polymaker.cli import app
from polymaker.domain import TokenMeta
from polymaker.marketdata.service import MarketDataService
from polymaker.mi_live import (
    MIReadinessError,
    NoSendDecisionSink,
    assert_clean_start,
    build_snapshot,
    evaluate_and_record,
    validate_market,
    validate_official_hosts,
    venue_quantity_step,
)
from polymaker.mi_readonly_account import AccountSnapshot, OfficialReadOnlyAccount
from polymaker.strategy.maker_inventory import (
    Action,
    MakerInventoryOrder,
    MakerInventoryState,
    OrderLifecycle,
    Outcome,
)

EPOCH = 1_800_000_000


def _meta(meta):
    return replace(
        meta,
        slug=slug_for_epoch(EPOCH),
        tokens=(TokenMeta("up-token", "Up"), TokenMeta("down-token", "Down")),
        end_date_iso=datetime.fromtimestamp(EPOCH + 300, UTC).isoformat(),
    )


def _account(cash="30"):
    return AccountSnapshot({}, (), Decimal(cash))


def _books(meta, yes=(0.64, 0.66), no=(0.34, 0.36)):
    md = MarketDataService()
    md.set_markets([(meta.condition_id, [meta.yes.token_id, meta.no.token_id])])
    md.connected = True
    md.connected_since = 1.0
    md.connection_generation = 1
    for token, (bid, ask) in ((meta.yes.token_id, yes), (meta.no.token_id, no)):
        book = md.book(token)
        assert book is not None
        book.set_tick_size(0.01)
        book.apply_snapshot([(bid, 100.0)], [(ask, 100.0)], EPOCH)
        book.snapshot_generation = 1
    return md


def _fake_market_runtime(monkeypatch, tmp_path, selected, *, start_time, valid_book, account_read_time=None):
    clock = SimpleNamespace(value=start_time)
    config = SimpleNamespace(
        wallet=SimpleNamespace(
            gamma_host="https://gamma-api.polymarket.com",
            clob_host="https://clob.polymarket.com",
            data_api_host="https://data-api.polymarket.com",
        ),
        paths=SimpleNamespace(log_dir=tmp_path),
        proxy=None,
    )

    class FakeAccountReader:
        reads = 0

        async def connect(self):
            pass

        async def snapshot(self, _meta):
            self.reads += 1
            if self.reads == 2 and account_read_time is not None:
                clock.value = account_read_time
            return _account()

    def make_market_data(*, on_connection, **_kwargs):
        md = _books(selected)
        md.connected = valid_book
        on_connection(valid_book)

        async def idle():
            await asyncio.Event().wait()

        md.run = idle
        return md

    async def resolve(_config, _slug, *, cache, strict_metadata):
        assert not cache and strict_metadata
        return selected

    real_sleep = asyncio.sleep
    real_wait_for = asyncio.wait_for

    async def advance_sleep(delay):
        clock.value += max(0.0, delay)
        await real_sleep(0)

    async def advance_wait_for(awaitable, *, timeout):
        try:
            return await real_wait_for(awaitable, timeout=0.001)
        except TimeoutError:
            clock.value += timeout
            raise

    monkeypatch.setattr(mi_live.Config, "load", lambda _path: config)
    monkeypatch.setattr(mi_live, "OfficialReadOnlyAccount", lambda _config: FakeAccountReader())
    monkeypatch.setattr(mi_live, "resolve_btc5m_market", resolve)
    monkeypatch.setattr(mi_live, "MarketDataService", make_market_data)
    monkeypatch.setattr(mi_live, "time", SimpleNamespace(time=lambda: clock.value))
    monkeypatch.setattr(mi_live.asyncio, "sleep", advance_sleep)
    monkeypatch.setattr(mi_live.asyncio, "wait_for", advance_wait_for)
    return clock


def test_identity_and_clean_start(meta):
    selected = _meta(meta)
    validate_market(selected, EPOCH)
    assert_clean_start(_account(), selected)
    assert_clean_start(AccountSnapshot({"other-token": Decimal("100")}, (), Decimal("30")), selected)
    with pytest.raises(MIReadinessError, match="position"):
        assert_clean_start(AccountSnapshot({"up-token": Decimal("0.1")}, (), Decimal("30")), selected)
    with pytest.raises(MIReadinessError, match="open order"):
        assert_clean_start(AccountSnapshot({}, ("down-token",), Decimal("30")), selected)
    with pytest.raises(MIReadinessError, match="IDENTITY"):
        validate_market(replace(selected, tokens=(TokenMeta("up-token", "Yes"), selected.no)), EPOCH)


def test_valid_books_cash_and_metadata_map_to_snapshot(meta):
    selected = _meta(meta)
    md = _books(selected)
    snapshot = build_snapshot(selected, md, _account("29.25"), EPOCH, EPOCH + 100)
    assert snapshot.data_valid
    assert snapshot.yes.best_bid == Decimal("0.64")
    assert snapshot.no.best_ask == Decimal("0.36")
    assert snapshot.cash_available_for_new_order == Decimal("29.25")
    assert snapshot.tick_size == Decimal("0.01")
    assert snapshot.venue_min_order_size == Decimal("5.0")
    assert snapshot.quantity_step == venue_quantity_step(Decimal("0.01")) == Decimal("0.01")
    assert snapshot.confirmed_yes_qty == snapshot.confirmed_no_qty == 0


def test_invalid_book_is_not_promoted_to_valid(meta):
    selected = _meta(meta)
    md = _books(selected)
    md.connected = False
    snapshot = build_snapshot(selected, md, _account(), EPOCH, EPOCH + 100)
    assert not snapshot.data_valid
    md.connected = True
    md.connection_generation = 2
    assert not build_snapshot(selected, md, _account(), EPOCH, EPOCH + 100).data_valid


@pytest.mark.parametrize(
    "yes,no,elapsed,expected,qty",
    [
        ((0.64, 0.66), (0.34, 0.36), 100, Action.PLACE_BUY_YES, "7.5"),
        ((0.34, 0.36), (0.64, 0.66), 100, Action.PLACE_BUY_NO, "7.5"),
        ((0.49, 0.51), (0.49, 0.51), 100, Action.WAIT, None),
        ((0.64, 0.66), (0.34, 0.36), 285, Action.WAIT, None),
    ],
)
def test_decision_chain_is_recorded_without_execution(meta, tmp_path, yes, no, elapsed, expected, qty):
    selected = _meta(meta)
    md = _books(selected, yes, no)
    snapshot = build_snapshot(selected, md, _account(), EPOCH, EPOCH + elapsed)
    with NoSendDecisionSink(tmp_path / "run") as sink:
        decision = evaluate_and_record(selected, snapshot, sink, EPOCH + elapsed)
    row = json.loads((tmp_path / "run" / "inventory_decisions.jsonl").read_text())
    assert decision.action is expected
    assert row["desired_action"] == expected.value
    assert (Decimal(row["decision_qty"]) if row["decision_qty"] is not None else None) == (
        Decimal(qty) if qty is not None else None
    )
    assert row["execution_status"] == "NO_SEND"
    assert row["execution"] == "SUPPRESSED_NO_SEND"
    assert row["bidY"] == str(snapshot.yes.best_bid)
    assert row["N_target"] == str(decision.n_target)
    assert row["available_cash"] == "30"
    assert row["working_order_state"] == "NO_WORKING_ORDER"
    assert (row["yes_outcome"], row["no_outcome"]) == ("Up", "Down")


def test_data_invalid_wait_is_recorded(meta, tmp_path):
    selected = _meta(meta)
    md = _books(selected)
    md.connected = False
    snapshot = build_snapshot(selected, md, _account(), EPOCH, EPOCH + 100)
    with NoSendDecisionSink(tmp_path / "run") as sink:
        decision = evaluate_and_record(selected, snapshot, sink, EPOCH + 100)
    assert decision.action is Action.WAIT
    assert decision.reason == "DATA_INVALID"


@respx.mock
async def test_official_account_reader_maps_cash_positions_and_orders_without_writes(meta):
    from py_clob_client_v2.config import get_contract_config

    selected = _meta(meta)
    spender = get_contract_config(137).exchange_v2
    calls = {"post": 0, "cancel": 0}

    class ReadFake:
        chain_id = 137

        def _ClobClient__resolve_version(self):
            return 2

        def get_open_orders(self):
            return [{"asset_id": "down-token"}]

        def get_balance_allowance(self, _params):
            return {"balance": "30000000", "allowances": {spender: "25000000"}}

        def post_order(self):
            calls["post"] += 1
            raise AssertionError("order post reached")

        def cancel(self):
            calls["cancel"] += 1
            raise AssertionError("cancel reached")

    config = SimpleNamespace(
        wallet=SimpleNamespace(chain_id=137, data_api_host="https://data-api.polymarket.com"),
        secrets=SimpleNamespace(browser_address="0xabc"),
    )
    reader = OfficialReadOnlyAccount(config)
    reader._client = ReadFake()
    respx.get("https://data-api.polymarket.com/v2/positions").mock(
        return_value=Response(200, json={
            "data": [{"token_id": "up-token", "current_size": "0", "avg_price": "0"},
                     {"token_id": "other-token", "current_size": "2", "avg_price": "0.5"}],
            "pagination": {"next_cursor": None},
        })
    )
    result = await reader.snapshot(selected)
    assert result.cash_available == Decimal("25")
    assert result.positions == {"other-token": Decimal("2")}
    assert result.open_order_tokens == ("down-token",)
    with pytest.raises(MIReadinessError, match="open order"):
        assert_clean_start(result, selected)
    assert calls == {"post": 0, "cancel": 0}


def test_desired_cancel_is_recorded_without_cancel_call(meta, tmp_path):
    selected = _meta(meta)
    md = _books(selected)
    snapshot = build_snapshot(selected, md, _account(), EPOCH, EPOCH + 100)
    state = MakerInventoryState(MakerInventoryOrder(
        Outcome.YES, Decimal("0.63"), Decimal("5"), Decimal("5"), OrderLifecycle.WORKING
    ))
    with NoSendDecisionSink(tmp_path / "run") as sink:
        decision = evaluate_and_record(selected, snapshot, sink, EPOCH + 100, state)
    row = json.loads((tmp_path / "run" / "inventory_decisions.jsonl").read_text())
    assert decision.action is Action.CANCEL_EXISTING_ORDER
    assert row["desired_action"] == "CANCEL_EXISTING_ORDER"
    assert row["working_order_state"] == "WORKING"
    assert row["execution_status"] == "NO_SEND"


@pytest.mark.parametrize("yes,no,expected", [
    ((0.64, 0.66), (0.34, 0.36), Action.PLACE_BUY_YES),
    ((0.34, 0.36), (0.64, 0.66), Action.PLACE_BUY_NO),
])
def test_desired_place_cannot_reach_execution_gateway(meta, tmp_path, monkeypatch, yes, no, expected):
    from polymaker.execution.gateway import ExecutionGateway

    post = AsyncMock(side_effect=AssertionError("real post called"))
    cancel = AsyncMock(side_effect=AssertionError("real cancel called"))
    monkeypatch.setattr(ExecutionGateway, "place", post)
    monkeypatch.setattr(ExecutionGateway, "cancel", cancel)
    selected = _meta(meta)
    snapshot = build_snapshot(selected, _books(selected, yes, no), _account(), EPOCH, EPOCH + 100)
    with NoSendDecisionSink(tmp_path / "run") as sink:
        assert evaluate_and_record(selected, snapshot, sink, EPOCH + 100).action is expected
    post.assert_not_awaited()
    cancel.assert_not_awaited()


def test_desired_cancel_cannot_reach_execution_gateway(meta, tmp_path, monkeypatch):
    from polymaker.execution.gateway import ExecutionGateway

    post = AsyncMock(side_effect=AssertionError("real post called"))
    cancel = AsyncMock(side_effect=AssertionError("real cancel called"))
    monkeypatch.setattr(ExecutionGateway, "place", post)
    monkeypatch.setattr(ExecutionGateway, "cancel", cancel)
    selected = _meta(meta)
    snapshot = build_snapshot(selected, _books(selected), _account(), EPOCH, EPOCH + 100)
    state = MakerInventoryState(MakerInventoryOrder(
        Outcome.YES, Decimal("0.63"), Decimal("5"), Decimal("5"), OrderLifecycle.WORKING
    ))
    with NoSendDecisionSink(tmp_path / "run") as sink:
        assert evaluate_and_record(selected, snapshot, sink, EPOCH + 100, state).action is Action.CANCEL_EXISTING_ORDER
    post.assert_not_awaited()
    cancel.assert_not_awaited()


async def test_runner_uses_post_account_read_time_at_cutoff(meta, tmp_path, monkeypatch):
    selected = _meta(meta)
    cutoff = EPOCH + 285
    clock = _fake_market_runtime(
        monkeypatch, tmp_path, selected,
        start_time=cutoff - 0.1,
        account_read_time=cutoff + 0.2,
        valid_book=True,
    )

    directory = await mi_live.run_inventory_no_send(
        "unused", once=True, research=True, no_send=True
    )
    rows = [json.loads(line) for line in (directory / "inventory_decisions.jsonl").read_text().splitlines()]
    report = json.loads((directory / "run.json").read_text())
    assert len(rows) == 1
    assert rows[0]["timestamp_epoch"] == pytest.approx(cutoff + 0.2)
    assert rows[0]["timestamp_epoch"] >= cutoff
    assert rows[0]["desired_action"] == Action.WAIT.value
    assert rows[0]["decision_reason"] == "FINAL_NO_BUY_WINDOW"
    assert report["status"] == "COMPLETED"
    assert report["desired_actions"] == {Action.WAIT.value: 1}
    assert report["execution_calls"] == report["cancel_calls"] == 0
    assert clock.value >= EPOCH + 300


async def test_runner_reports_no_valid_book_after_market_end(meta, tmp_path, monkeypatch):
    selected = _meta(meta)
    _fake_market_runtime(
        monkeypatch, tmp_path, selected,
        start_time=EPOCH + 0.1,
        valid_book=False,
    )

    with pytest.raises(MIReadinessError, match="NO_VALID_BOOK"):
        await mi_live.run_inventory_no_send("unused", once=True, research=True, no_send=True)
    directory = next((tmp_path / "runs").iterdir())
    rows = [json.loads(line) for line in (directory / "inventory_decisions.jsonl").read_text().splitlines()]
    report = json.loads((directory / "run.json").read_text())
    assert rows
    assert all(row["desired_action"] == Action.WAIT.value for row in rows)
    assert all(row["decision_reason"] == "DATA_INVALID" for row in rows)
    assert report["valid_evaluate_count"] == 0
    assert report["status"] == "INCOMPLETE"
    assert report["error"] == "MIReadinessError: NO_VALID_BOOK"
    assert report["execution_calls"] == report["cancel_calls"] == 0


async def test_runtime_rejects_missing_no_send_flags(monkeypatch, tmp_path: Path):
    from polymaker.mi_live import run_inventory_no_send

    loader = AsyncMock(side_effect=AssertionError("account or market must not be read"))
    monkeypatch.setattr("polymaker.mi_live.resolve_btc5m_market", loader)
    for flags in ((False, True, True), (True, False, True), (True, True, False)):
        with pytest.raises(MIReadinessError, match="REQUIRES"):
            await run_inventory_no_send(str(tmp_path), once=flags[0], research=flags[1], no_send=flags[2])
    loader.assert_not_awaited()


def test_inventory_cli_never_defaults_to_send():
    runner = CliRunner()
    for args in ([], ["--once", "--research"], ["--once", "--no-send"]):
        result = runner.invoke(app, ["inventory-live", *args])
        assert result.exit_code != 0
        assert "--once --research --no-send" in result.output


def test_mi_rejects_nonofficial_account_or_market_host():
    wallet = SimpleNamespace(
        gamma_host="https://gamma-api.polymarket.com",
        clob_host="https://clob.polymarket.com",
        data_api_host="https://data-api.polymarket.com",
    )
    validate_official_hosts(SimpleNamespace(wallet=wallet))
    wallet.data_api_host = "https://data-api.polymarket.com.example.net"
    with pytest.raises(MIReadinessError, match="OFFICIAL"):
        validate_official_hosts(SimpleNamespace(wallet=wallet))


async def test_mi_discovery_rejects_missing_venue_metadata_without_catalog_write(monkeypatch):
    from polymaker.btc5m import resolve_btc5m_market

    class FakeGamma:
        def __init__(self, _host):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def market_by_slug(self, _slug):
            return {"slug": slug_for_epoch(EPOCH), "orderMinSize": None}

    monkeypatch.setattr("polymaker.btc5m.GammaClient", FakeGamma)
    config = SimpleNamespace(wallet=SimpleNamespace(gamma_host="https://gamma-api.polymarket.com"))
    with pytest.raises(LookupError, match="venue metadata"):
        await resolve_btc5m_market(
            config, slug_for_epoch(EPOCH), cache=False, strict_metadata=True
        )
