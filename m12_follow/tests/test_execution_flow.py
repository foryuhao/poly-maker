"""Offline integration of baseline admission, transport, native guards and signed mock HTTP."""
import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from py_clob_client_v2.http_helpers import helpers

from m4_helpers import close, feed_fixture
from test_m4_live_budget import fixture as mock_live_market
from test_m4_live_task import evidence, serve

from m12_follow import sqlite_source, transport
from m12_follow import trial
from m12_follow.adapter import FollowQuoteFeed, FollowSocket
from m12_follow.publisher import IntentPublication
from m12_follow.sqlite_source import SQLiteIntents
from polymaker.config import RDPObiConfig
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_live_budget import BudgetLedger, follow_fak_amount
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_run import Executor
from polymaker.m4_types import M421_LEDGER
from polymaker.market_protocol import V2MarketMeta
from polymaker.rdp_sm_writer import AsyncRiskWriter
from polymaker.taker_runner import StrategyReads, strategy_settings

FRAMEWORK = Path(__file__).resolve().parents[1] / "framework"
VERSION = "m12-live-support-weak-ask-overlap-v1/raw-path-v1/p-9c8515b130c7"


@pytest.mark.asyncio
async def test_follow_quote_feed_closes_after_market_releases_books(tmp_path):
    source, _, _ = feed_fixture()
    reader = SQLiteIntents(tmp_path, source.meta, 5)
    settings = strategy_settings(FRAMEWORK, M421_LEDGER, M4LiveSettings.load(FRAMEWORK), signal_profile="M42112")
    feed = FollowQuoteFeed(source.meta, RDPObiConfig(), taker_settings=settings, reader=reader, run_id="offline")
    await feed.close()
    assert feed.research.reasons["1"] == "WS_STOPPED"
    feed.release_signal_memory()
    await feed.close()
    assert not feed.research.books and reader.fd is None


@pytest.mark.parametrize("remaining,price,at_cap,expected", [
    ("5", ".87", False, ("5.00", "5.7472")),
    ("5", ".89", False, ("5.00", "5.6180")),
    ("2.537", ".87", False, ("2.53", "2.9081")),
    ("5", ".87", True, ("4.35", "5.0000")),
    ("5", ".50", True, ("5.00", "10.0000")),
])
def test_follow_fak_cash_and_cap(remaining, price, at_cap, expected):
    cash, shares = follow_fak_amount(Decimal(remaining), Decimal(price), ask_at_cap=at_cap)
    assert (cash, shares) == tuple(map(Decimal, expected))
    assert cash <= Decimal(remaining) and cash / shares <= Decimal(price)


@pytest.mark.parametrize("limit,reported,shares,principal", [
    (".89", ".8900000141691695", "2.117273", "1.884373"),
    (".73", ".7300000386970629", "5.685186", "4.150186"),
    (".73", ".7300002", "5.685186", None),
    (".73", ".74", ".000001", None),
])
def test_follow_maker_fill_price_precision(limit, reported, shares, principal):
    from polymaker.m4_live_numeric import maker_fill_event
    from polymaker.userstream.parse import parse_trade

    trade = parse_trade({
        "id": "confirmed-maker-fill", "market": "market", "status": "CONFIRMED",
        "trader_side": "MAKER", "taker_order_id": "foreign-taker", "asset_id": "opposite",
        "side": "BUY", "maker_orders": [{
            "order_id": "owned-order", "maker_address": "funder", "asset_id": "token",
            "side": "BUY", "price": reported, "matched_amount": shares,
        }],
    })
    spec = {"market_id": "market", "token_id": "token", "price": Decimal(limit),
            "quantity": Decimal("7"), "fees": {"rate": ".05", "exponent": 1}}
    if principal is None:
        with pytest.raises(ValueError, match="M12_MAKER_LEG_CONFLICT"):
            maker_fill_event(trade, spec, "owned-order", "funder")
        return
    fill, tolerance, fee_ceiling = maker_fill_event(trade, spec, "owned-order", "funder")
    assert fill.status.value == "CONFIRMED" and fill.size == float(shares)
    assert fill.price == float(limit) and fill.principal_usdc == principal
    assert Decimal(fill.fee_usdc) == tolerance == fee_ceiling == 0


def test_sqlite_wakes_for_baseline_wal_writes_only(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1.0)
    monkeypatch.setattr(sqlite_source, "time", SimpleNamespace(monotonic=lambda: clock.now))
    meta = SimpleNamespace(slug="offline-market", condition_id="offline-condition")
    reader = SQLiteIntents(tmp_path, meta, 3)
    reader.path.parent.mkdir(parents=True)
    try:
        with closing(sqlite3.connect(reader.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("CREATE TABLE audit(seq INTEGER PRIMARY KEY,input_seq,kind,payload);"
                             "CREATE TABLE metadata(name PRIMARY KEY,payload);")
            db.executemany("INSERT INTO metadata VALUES(?,?)", (
                ("identity", json.dumps(dict(database_id="baseline", market=meta.slug, condition_id=meta.condition_id))),
                ("run_owner", json.dumps(dict(path=str(tmp_path), mode="SIMULATION_ONLY_PUBLIC_DATA"))),
            ))
            db.commit()
            initial = reader.read()
            assert initial is not None
            assert reader.read() is initial
            (reader.path.parent / "diagnostic.json").write_text("{}")
            assert reader.read() is initial

            intent = dict(market=meta.slug, strategy="M12", profile="M12", side="BUY",
                          order_type="ASSUMED_FIXED_ANCHOR", signal_version=VERSION, opportunity="first")
            db.execute("INSERT INTO audit VALUES (1,1,'INTENT',?)",
                       (json.dumps({"scenario": "sop/anchor", "intent": intent}),))
            db.commit()
            changed = reader.read()
            assert changed is not initial
            assert reader.latest["opportunity"] == "first"
            assert reader.read() is changed
            clock.now += 0.021
            rechecked = reader.read()
            assert rechecked is not changed
            assert reader.read() is rechecked
    finally:
        reader.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["ack", "ended_before_send", "outside_band", "unknown", "zero_then_end",
                                  "partial_active", "partial_ended", "end_after_signing", "restart_unknown", "T240", "ack_v2", "partial_active_v2",
                                  "trial_end_after_signing", "trial_archive_unknown", "sqlite_late",
                                  "sqlite_cap", "sqlite_superseded", "sqlite_T240", "sqlite_unknown", "sqlite_partial",
                                  "sqlite_limit", "sqlite_limit_v2", "sqlite_limit_late", "sqlite_limit_late_v2",
                                  "sqlite_limit_at_cap", "sqlite_limit_cancel",
                                  "sqlite_limit_T240", "sqlite_limit_edge", "sqlite_limit_burst", "sqlite_limit_restart",
                                  "sqlite_limit_cancel_timeout", "sqlite_limit_small", "sqlite_limit_low", "sqlite_limit_floor",
                                  "sqlite_limit_anchor_drop", "sqlite_limit_anchor_drop_native_v2",
                                  "sqlite_limit_anchor_stale", "sqlite_limit_anchor_inside", "sqlite_partial_anchor_drop"])
async def test_follow_native_fak(tmp_path, monkeypatch, case):
    assert Path(__import__("polymaker").__file__).resolve().is_relative_to(FRAMEWORK)
    primary, lead, parent, clock, _, mock_transport = await mock_live_market(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock.now))
    cfg = strategy_settings(FRAMEWORK, M421_LEDGER, M4LiveSettings.load(FRAMEWORK), signal_profile="M42112")
    if case.endswith("_v2"):
        case = case.removesuffix("_v2")
        lead.meta = parent.meta = V2MarketMeta(**{**asdict(lead.meta), "tokens": lead.meta.tokens})
        lead.gateway._client.session_market_protocol = "v2"
        lead.gateway._client.session_market_id = lead.meta.condition_id
    meta = parent.meta
    source = dict(run_id="offline-baseline", market=meta.slug, condition_id=meta.condition_id)
    producer = transport.Producer(tmp_path / "channel", **source)
    reader = transport.Reader(tmp_path / "channel", **source)
    sqlite_mode = case.startswith("sqlite_")
    if sqlite_mode:
        reader = SQLiteIntents(tmp_path, meta, 5 if case.startswith("sqlite_limit") else 3)
        reader.follow_limit = case.startswith("sqlite_limit")
        reader.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(reader.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("CREATE TABLE audit(seq INTEGER PRIMARY KEY,input_seq,kind,payload);"
                             "CREATE TABLE metadata(name PRIMARY KEY,payload);")
            db.executemany("INSERT INTO metadata VALUES(?,?)", (
                ("identity", json.dumps(dict(database_id="baseline", market=meta.slug, condition_id=meta.condition_id))),
                ("run_owner", json.dumps(dict(path=str(tmp_path), mode="SIMULATION_ONLY_PUBLIC_DATA"))),
            ))

    def append_intent(intent):
        with closing(sqlite3.connect(reader.path)) as db, db:
            db.execute("INSERT INTO audit(input_seq,kind,payload) VALUES(1,'INTENT',?)",
                       (json.dumps({"scenario": "sop/anchor", "intent": intent}),))

    if case.startswith("trial_"):
        # The fixture market is the second, and last, authorized market.
        epoch = int(meta.slug.rsplit("-", 1)[1])
        window = trial.TwoMarketWindow(epoch - 300)
        trial_clock = SimpleNamespace(now=epoch + 20)
        monkeypatch.setattr(trial, "time", SimpleNamespace(time=lambda: trial_clock.now))
        reader = window.scoped_reader(reader, meta.slug)
    socket = FollowSocket(meta, RDPObiConfig(), m421_settings=cfg,
                          follow=SimpleNamespace(reader=lambda _: reader, run_id=source["run_id"],
                                                 market_entry_block=lambda _: None),
                          monotonic=parent.monotonic, wall=parent.wall)
    feed = socket.secondary[M421_LEDGER]
    socket.connect()
    socket.verify_ticks({t.token_id: Decimal("0.01") for t in meta.tokens})
    if case == "sqlite_late":
        socket.subscription_token_ids = ("1",)
    ask = {"outside_band": ".90", "sqlite_cap": ".74"}.get(case, ".71")
    if case.startswith("sqlite_limit"):
        ask = ".82"  # No liquidity within +5 ticks. A resting bid must still be submitted.
    if case == "sqlite_limit_at_cap":
        ask = ".87"
    rows = [{"event_type": "book", "market": meta.condition_id, "asset_id": t.token_id,
             "timestamp": str(feed.wall() * 1000), "bids": [{"price": ".70", "size": "100"}],
             "asks": [{"price": ask, "size": "100"}]} for t in meta.tokens]
    socket._apply(json.dumps(rows), received_mono=clock.now, received_wall=feed.wall())

    def push_ask(ask, token="1"):
        row = {"event_type": "book", "market": meta.condition_id, "asset_id": token,
               "timestamp": str(feed.wall() * 1000),
               "bids": [{"price": str(Decimal(ask) - Decimal(".01")), "size": "100"}],
               "asks": [{"price": ask, "size": "100"}]}
        socket._apply(json.dumps([row]), received_mono=clock.now, received_wall=feed.wall())

    identity = {"strategy": M421_LEDGER, "mode": "LIVE", "funder": lead.gateway.funder,
                "market_id": meta.condition_id, "settings_hash": cfg.fingerprint}

    def open_ledger():
        path = tmp_path / "m421-ledgers" / f"{meta.slug}.sqlite3"
        return BudgetLedger(path, identity, settings=cfg, fees=primary.ledger.fees)

    ledger = open_ledger()
    sid = "m4_live_" + "a" * 32
    ledger.initialize_session(sid, cfg.model_dump(mode="json"), asdict(meta))
    writer = AsyncRiskWriter(M4RiskJournal(tmp_path / "follow-journal", lead.gateway.funder, session_id=sid))
    reads = StrategyReads(lead.reads, feed, ledger, cfg)
    executor = Executor(None, lead.slug, sid, reads, writer, ledger=ledger, control=lead.control,
                        live=True, monotonic=parent.monotonic)
    executor.meta, executor.official_time, executor.time_anchor = meta, lead.official_time, lead.time_anchor
    executor.stop_event = asyncio.Event()
    market = BudgetMarket(executor, cfg, executor.stop_event, account_owner=primary, display=lambda _: None)
    events = []
    fact = dict(id="m12:pair:example", token="1", anchor_price=".70", confirmed=clock.now-.02,
                version=VERSION, root_id="m12:component:example")
    engine = SimpleNamespace(clock=SimpleNamespace(mono=clock.now), meta=meta, seq=1, ended=False,
        needs_reconnect=False, validity={"status": "TRACKING"}, signal_history={fact["id"]: fact},
        sop_runtime=SimpleNamespace(core=SimpleNamespace(signals={fact["id"]: fact}),
            legal=lambda token: True, connected=True, good={"1": True}),
        store=SimpleNamespace(emit=lambda *args: events.append(args), commit=lambda: None))
    tap = IntentPublication(engine, producer)
    calls = []

    def respond(request):
        assert (request.method, request.url.path) == ("POST", "/orders")
        calls.extend(json.loads(request.content))
        oid = reads.gateway._client._operation_value("attempt").expected_order_id
        if case in {"unknown", "restart_unknown", "trial_archive_unknown", "sqlite_unknown"}:
            raise httpx.ReadTimeout("mock lost acknowledgement", request=request)
        reply = {"success": True, "orderID": oid}
        if case == "zero_then_end":
            reply.update(errorMsg="no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.",
                         status="", makingAmount="", takingAmount="", tradeIDs=[], transactionsHashes=[])
        return httpx.Response(200, json=[reply])

    def advance_source():
        engine.seq += 1
        tap.after_input({})

    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            monkeypatch.setattr(helpers, "_http_client", http)
            engine.store.emit("SOP_EVIDENCE", {"event": "SIGNAL_CONFIRMED", "payload": fact})
            advance_source()
            market.collect()
            assert not ledger.rows()
            intent = dict(opportunity=fact["id"], strategy="M12", profile="M12", signal_version=VERSION,
                          order_type="ASSUMED_FIXED_ANCHOR", market=meta.slug, side="BUY", token="1",
                          outcome="YES", price=".70", quantity="7.1", available=clock.now, deadline=clock.now+1)
            if case in {"sqlite_limit_low", "sqlite_limit_floor"}:
                intent["price"] = ".13" if case == "sqlite_limit_low" else ".45"
            if case in {"sqlite_limit_late", "sqlite_limit_at_cap"}:
                intent["price"] = ".82"
            if sqlite_mode:
                if case == "sqlite_limit_burst":
                    append_intent({**intent, "opportunity": "earlier-in-same-read"})
                append_intent({**intent, "available": clock.now - 45, "deadline": clock.now - 42})
            else:
                engine.store.emit("INTENT", {"scenario": "sop/anchor", "intent": intent})
                advance_source()
            if case in {"sqlite_limit_low", "sqlite_limit_floor"}:
                feed.follow_market = market
                feed.connected = False
                feed.m1.refresh(force=True)
                assert feed.wanted_token() is None
            market.collect()
            if case == "sqlite_limit_low":
                row, = ledger.rows()
                assert (row["state"], row["reason"]) == ("SKIPPED", "M12_FROZEN_CAP_BELOW_PRICE_FLOOR")
                assert not market.pending and not calls and not ledger.attempts()
                assert not market.intent_needs_quotes(row["id"]) and feed.wanted_token() is None
                filtered, = ledger.metric()["by_follow_intent"]
                assert filtered["posts_started"] == 0 and filtered["principal_completion_ratio"] == "0"
                # Simulate an unsent task left RESERVED by the preceding binary.
                with ledger.db:
                    ledger.db.execute("UPDATE opportunities SET state='RESERVED',reason=NULL WHERE id=?", (row["id"],))
                market.restore()
                assert not market.pending and ledger.row(row["id"])["state"] == "SKIPPED"
                append_intent({**intent, "opportunity": "later", "token": "2", "outcome": "NO", "price": ".65"})
                market.collect()
                assert feed.wanted_token() == "2" and len(market.pending) == 1
                return
            if case == "sqlite_limit_floor":
                assert market.pending and feed.wanted_token() == "1"
                feed.connected = True
            [opportunity] = market.pending.values()
            market.collect()
            assert len(ledger.rows()) == 1
            if case == "sqlite_limit_anchor_inside":
                push_ask(".61")
                push_ask(".50", token="2")
            if case == "sqlite_limit_anchor_stale":
                push_ask(".60")
                clock.now += .201
                await market.submit(opportunity)
                assert not calls and not ledger.attempts()
                assert market.guard_failure(opportunity.episode_id) == "M12_WAIT_FRESH_BEST_ASK_BEFORE_POST"
                assert not ledger.get("m12_anchor_invalidation:" + opportunity.episode_id)
                push_ask(".61")
            if case in {"sqlite_limit_anchor_drop", "sqlite_limit_anchor_drop_native"}:
                key = opportunity.episode_id
                if case == "sqlite_limit_anchor_drop_native":
                    def drop_at_native_guard(attempt):
                        push_ask(".60")
                        return market.native_guard(attempt)
                    monkeypatch.setattr(reads.gateway._client, "m4_native_guard", drop_at_native_guard)
                else:
                    push_ask(".60")
                await market.submit(opportunity)
                assert not calls
                saved = ledger.get("m12_anchor_invalidation:" + key)
                assert saved["token_id"] == "1" and saved["drop"] == "0.10"
                row = ledger.row(key)
                assert (row["state"], row["reason"]) == ("SKIPPED", "M12_BEST_ASK_BELOW_SIGNAL_ANCHOR")
                assert not row["order_id"] and Decimal(row["reserved"]) == 0
                if case == "sqlite_limit_anchor_drop_native":
                    attempt, = ledger.attempts()
                    assert attempt["state"] == "UNSENT"
                    proof = json.loads(attempt["proof"])
                    assert proof["native_post_sent"] is False and proof["reason"] == row["reason"]
                else:
                    assert not ledger.attempts()
                ledger.validate_recovery()
                await writer.flush()
                ledger.close()
                ledger = open_ledger()
                executor.ledger = reads.gateway.m4_ledger = reads.gateway._client.m4_ledger = ledger
                market = BudgetMarket(executor, cfg, executor.stop_event, account_owner=primary, display=lambda _: None)
                push_ask(".71")
                market.restore()
                await market.submit(opportunity)
                assert not calls and ledger.get("m12_anchor_invalidation:" + key) == saved
                assert not market.intent_needs_quotes(key)
                return
            if case == "ended_before_send":
                engine.sop_runtime.core.signals.clear()
                advance_source()
            if case in {"T240", "sqlite_T240"}:
                executor.official_time = int(meta.slug.rsplit("-", 1)[1]) + 240
            if case in {"end_after_signing", "trial_end_after_signing", "sqlite_superseded"}:
                def end_at_native_guard(attempt):
                    if case == "trial_end_after_signing":
                        trial_clock.now = window.end
                    elif case == "sqlite_superseded":
                        append_intent({**intent, "opportunity": "newer", "token": "2", "outcome": "NO"})
                    else:
                        engine.sop_runtime.core.signals.clear()
                        advance_source()
                    return market.native_guard(attempt)
                monkeypatch.setattr(reads.gateway._client, "m4_native_guard", end_at_native_guard)
            if case == "sqlite_limit_small":
                # The actual per-market GTD share minimum may exceed available budget.
                from dataclasses import replace
                executor.meta = replace(executor.meta, min_order_size=10)
            if case == "sqlite_limit_T240":
                executor.official_time = int(meta.slug.rsplit("-", 1)[1]) + 240
                await market.submit(opportunity)
                assert not calls
                return
            if case == "sqlite_limit_edge":
                def change_time(attempt):
                    executor.official_time = int(meta.slug.rsplit("-", 1)[1]) + 120
                    return market.native_guard(attempt)
                monkeypatch.setattr(reads.gateway._client, "m4_native_guard", change_time)
                await market.submit(opportunity)
                assert not calls and ledger.attempts()[0]["state"] == "UNSENT"
            if case in {"sqlite_limit_late", "sqlite_limit_at_cap"}:
                executor.official_time = int(meta.slug.rsplit("-", 1)[1]) + 140
            await market.submit(opportunity)
            if case.startswith("sqlite_limit"):
                assert len(calls) == 1, (market.guard_failure(opportunity.episode_id), ledger.attempts(), ledger.rows())
                sent, = calls
                plan = json.loads(ledger.attempts()[-1]["plan"])
                is_fak = case in {"sqlite_limit_late", "sqlite_limit_at_cap", "sqlite_limit_edge", "sqlite_limit_small"}
                assert sent["orderType"] == ("FAK" if is_fak else "GTD")
                assert sent["postOnly"] is False
                limit = Decimal({"sqlite_limit_floor": ".50", "sqlite_limit_late": ".87",
                                 "sqlite_limit_at_cap": ".87"}.get(case, ".75"))
                assert Decimal(plan["limit_price"]) == limit
                assert int(sent["order"]["expiration"]) == plan["expiration"]
                assert plan["expiration"] == (0 if is_fak else int(meta.slug.rsplit("-", 1)[1]) + 300)
                assert 0 < Decimal(sent["order"]["makerAmount"]) / Decimal(sent["order"]["takerAmount"]) <= limit
                if case == "sqlite_limit_late":
                    assert int(sent["order"]["makerAmount"]) == 5_000_000
                    assert int(sent["order"]["takerAmount"]) == 5_747_200
                    assert Decimal(plan["planned_cost_usdc"]) > Decimal(plan["principal_usdc"]) == 5
                if case == "sqlite_limit_at_cap":
                    assert plan["pricing"]["ask_at_cap"] is True
                    assert int(sent["order"]["makerAmount"]) == 4_350_000
                    assert int(sent["order"]["takerAmount"]) == 5_000_000
                ledger.validate_recovery()
                if case == "sqlite_limit_burst":
                    skipped, latest = ledger.metric()["by_follow_intent"]
                    assert skipped["state"] == "SUPERSEDED_BEFORE_SUBMISSION"
                    assert skipped["posts_started"] == 0 and skipped["principal_completion_ratio"] == "0"
                    assert latest["posts_started"] == 1 and latest["confirmed_all_in_usdc"] == "0"
                if case == "sqlite_limit_restart":
                    await writer.flush()
                    ledger.close()
                    ledger = open_ledger()
                    executor.ledger = reads.gateway.m4_ledger = reads.gateway._client.m4_ledger = ledger
                    market = BudgetMarket(executor, cfg, executor.stop_event, account_owner=primary, display=lambda _: None)
                    market.restore()
                    ledger.validate_recovery()
                    assert ledger.unresolved(opportunity.episode_id)
                    await market.submit(opportunity)
                    assert len(calls) == 1
                if case in {"sqlite_limit_cancel", "sqlite_limit_cancel_timeout"}:
                    await verify_replace(market, executor, ledger, opportunity, intent, append_intent,
                                         http, calls, monkeypatch, timeout=case.endswith("timeout"))
                return
            if case in {"end_after_signing", "trial_end_after_signing", "sqlite_superseded"}:
                assert ledger.attempts()[0]["state"] == "UNSENT"
            if case in {"outside_band", "ended_before_send", "end_after_signing", "trial_end_after_signing",
                        "T240", "sqlite_T240", "sqlite_cap", "sqlite_superseded"}:
                assert not calls
                if case == "sqlite_superseded":
                    market.pending.clear()
                    market.collect()
                    latest, = market.pending.values()
                    assert latest.outcome == "NO"
                if case == "sqlite_cap":
                    assert market.guard_failure(opportunity.episode_id) == "M12_ABOVE_ANCHOR_CAP"
            else:
                assert len(calls) == 1
                assert calls[0]["orderType"] == "FAK"
                assert calls[0]["order"]["tokenId"] == "1"
                assert 0 < int(calls[0]["order"]["makerAmount"]) <= 5_000_000
                if case in {"unknown", "restart_unknown", "sqlite_unknown"}:
                    assert ledger.unresolved(opportunity.episode_id)
                if case == "sqlite_unknown":
                    append_intent({**intent, "opportunity": "newer", "token": "2", "outcome": "NO"})
                    market.collect()
                    newest, = (op for op in market.pending.values() if op.outcome == "NO")
                    await market.submit(newest)
                    assert len(calls) == 1
                    assert market.guard_failure(newest.episode_id) == "M12_PREVIOUS_INTENT_AWAITING_ORDER_PROOF"
                if case == "trial_archive_unknown":
                    trial_clock.now = window.end
                    oid = ledger.attempts()[0]["order_id"]
                    order, trade = evidence(market, executor, oid, "2", price=".70", status="CONFIRMED")
                    query, _ = serve(monkeypatch, http, order, trade)

                    class ArchiveReads:
                        def __init__(self, _):
                            self.gateway = reads.gateway

                        async def connect_account(self):
                            pass

                        def bind_ledger(self, owned):
                            assert self.gateway._client.m4_read_only
                            self.gateway.m4_ledger = self.gateway._client.m4_ledger = owned

                        def close(self):
                            pass

                    try:
                        result = await asyncio.wait_for(window.finish(tmp_path, lead.gateway.funder.lower(),
                            ArchiveReads, None, asyncio.Event(), lead.control), timeout=8)
                        assert result["status"] == "TWO_MARKET_WINDOW_CLOSED"
                        assert ledger.attempt(oid)["state"] == "TERMINAL"
                        assert ledger.principal_used(opportunity.episode_id) == Decimal("1.40")
                        result_row = result["market_results"][meta.slug]
                        assert result_row["intent_count"] == result_row["post_count"] == 1
                        assert result_row["confirmed_fill_count"] == result_row["confirmed_order_count"] == 1
                        assert Decimal(result_row["confirmed_shares"]) == Decimal("2")
                        assert Decimal(result_row["confirmed_cost_with_fees_usdc"]) == Decimal(ledger.attempt(oid)["spent"])
                        assert result_row["unconfirmed_order_count"] == 0
                        assert len(calls) == 1
                    finally:
                        query.close()
                if case == "restart_unknown":
                    ledger.validate_recovery()
                    await writer.flush()
                    ledger.close()
                    ledger = open_ledger()
                    executor.ledger = reads.gateway.m4_ledger = reads.gateway._client.m4_ledger = ledger
                    executor.known.clear()
                    market = BudgetMarket(executor, cfg, executor.stop_event, account_owner=primary,
                                          display=lambda _: None)
                    # Reopen the durable ledger and discard the subscriber's in-memory claims.
                    new_reader = transport.Reader(tmp_path / "channel", **source)
                    feed.m1 = type(feed.m1)(feed, cfg, new_reader, source["run_id"])
                    market.restore()
                    market.collect()
                    assert set(market.pending) == {opportunity.episode_id}
                    assert len(ledger.rows()) == len(ledger.attempts()) == len(calls) == 1
                if case in {"partial_active", "partial_ended", "sqlite_partial", "sqlite_partial_anchor_drop"}:
                    oid = ledger.attempts()[0]["order_id"]
                    order, trade = evidence(market, executor, oid, "2", price=".70", status="MINED")
                    query, _ = serve(monkeypatch, http, order, trade)
                    try:
                        await market.reconcile(oid)
                        assert ledger.attempt(oid)["state"] == "SETTLING"
                        await market.submit(opportunity)
                        assert len(calls) == 1
                        if case == "partial_ended":
                            engine.sop_runtime.core.signals.clear()
                            advance_source()
                        trade["status"] = "CONFIRMED"
                        await market.reconcile(oid)
                        if case == "sqlite_partial_anchor_drop":
                            push_ask(".60")
                        await market.submit(opportunity)
                        assert ledger.principal_used(opportunity.episode_id) == Decimal("1.40")
                        if case in {"partial_active", "sqlite_partial"}:
                            assert len(calls) == 2
                            assert int(calls[1]["order"]["makerAmount"]) == 3_600_000
                        else:
                            assert len(calls) == 1
                        if case == "sqlite_partial_anchor_drop":
                            row = ledger.row(opportunity.episode_id)
                            assert (row["state"], row["reason"]) == ("CUTOFF", "M12_BEST_ASK_BELOW_SIGNAL_ANCHOR")
                            ledger.validate_recovery()
                    finally:
                        query.close()
                if case == "zero_then_end":
                    engine.sop_runtime.core.signals.clear()
                    advance_source()
                    await market.submit(opportunity)
                    assert len(calls) == 1
    finally:
        tap.close()
        await close(market, executor)
        await close(primary, lead)
        mock_transport.close()


async def verify_replace(market, executor, ledger, op, intent, append, original, posts, monkeypatch, *, timeout):
    oid = ledger.attempts()[0]["order_id"]
    order, take = evidence(market, executor, oid, "2", price=".70")
    order.update(order_type="GTD", status="LIVE")
    cancellations = []
    trades = {take["id"]: take}

    def wire(request):
        if request.method == "POST":
            return original._transport.handle_request(request)
        if request.method == "DELETE":
            ids = json.loads(request.content)
            assert ids == [oid]
            cancellations.append(ids)
            if timeout:
                raise httpx.ReadTimeout("unknown cancellation outcome", request=request)
            return httpx.Response(200, json={"canceled": ids})
        if request.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        assert request.url.path == "/data/trades" and "asset_id" not in request.url.params
        return httpx.Response(200, json={"data": [trades[request.url.params["id"]]], "next_cursor": "LTE="})

    with httpx.Client(transport=httpx.MockTransport(wire)) as http:
        monkeypatch.setattr(helpers, "_http_client", http)
        await market.read_order(oid)
        assert ledger.unresolved(op.episode_id) and not cancellations
        partial, = ledger.metric()["by_follow_intent"]
        assert Decimal(partial["principal_completion_ratio"]) == Decimal(".28")
        assert Decimal(partial["confirmed_fees_usdc"]) > 0
        append({**intent, "opportunity": "next", "token": "2", "outcome": "NO"})
        market.collect()
        newest, = (v for v in market.pending.values() if v.outcome == "NO")
        await market.read_order(oid)
        assert len(cancellations) == 1
        await market.submit(newest)
        assert len(posts) == 1
        maker = {**take, "id": "rest-fill", "taker_order_id": "external", "trader_side": "MAKER", "asset_id": "2", "side": "BUY",
                 "maker_orders": [{"order_id": oid, "asset_id": "1", "side": "BUY", "price": ".75000001",
                                   "matched_amount": "1", "maker_address": executor.gateway.funder}]}
        order.update(status="CANCELED", size_matched="3", associate_trades=[take["id"], maker["id"]])
        trades[maker["id"]] = maker
        maker["status"] = "MINED"
        await market.read_order(oid)
        assert ledger.attempt(oid)["state"] == "SETTLING"
        await market.submit(newest)
        assert len(posts) == 1
        maker["status"] = "CONFIRMED"
        await market.read_order(oid)
        assert ledger.attempt(oid)["state"] == "TERMINAL", ledger.get("blocked:" + oid)
        assert ledger.principal_used(op.episode_id) == Decimal("2.15")
        original_intent, _ = ledger.metric()["by_follow_intent"]
        assert Decimal(original_intent["principal_completion_ratio"]) == Decimal(".43")
        assert Decimal(original_intent["confirmed_shares"]) == 3
        assert Decimal(original_intent["baseline_share_completion_ratio"]) == Decimal(3) / Decimal("7.1")
        assert ledger.metric()["intents_with_confirmed_fills"] == 1
        assert original_intent["confirmed_fees_usdc"] == partial["confirmed_fees_usdc"]
        assert Decimal(original_intent["confirmed_all_in_usdc"]) == Decimal(ledger.attempt(oid)["spent"])
        await market.submit(newest)
        assert len(posts) == 2 and posts[1]["order"]["tokenId"] == "2"
        ledger.validate_recovery()


def test_follow_cap_uses_frozen_tick_and_current_legal_grid():
    from polymaker.m4_live_pricing import follow_limit_price
    from polymaker.m4_types import CLOSED_SIGNED_LIMIT_BAND, POST_SIGNED_LIMIT_BAND

    root = {"anchor_price": ".87", "tick_at_plan": ".01", "pricing_origin": {"overpay_ticks": 5},
            "post_price_band": {"policy": POST_SIGNED_LIMIT_BAND, "minimum": ".50", "maximum": ".90"}}
    assert follow_limit_price(root, Decimal(".01")) == Decimal(".89")
    assert follow_limit_price(root, Decimal(".001")) == Decimal(".899")
    root["post_price_band"]["policy"] = CLOSED_SIGNED_LIMIT_BAND
    assert follow_limit_price(root, Decimal(".01")) == Decimal(".90")
    root["anchor_price"] = ".72"
    assert follow_limit_price(root, Decimal(".1")) == Decimal(".7")
