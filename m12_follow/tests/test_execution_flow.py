"""Offline integration of baseline admission, transport, native guards and signed mock HTTP."""
import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from decimal import Decimal, ROUND_HALF_UP
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
from polymaker.m4_live_task import FAK_NO_MATCH
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
                                  "sqlite_limit_late_no_v2", "sqlite_limit_gtd_retry", "sqlite_limit_native_expiry",
                                  "sqlite_limit_at_cap", "sqlite_limit_cancel",
                                  "sqlite_limit_T240", "sqlite_limit_burst", "sqlite_limit_restart",
                                  "sqlite_limit_cancel_timeout", "sqlite_limit_small", "sqlite_limit_low", "sqlite_limit_floor",
                                  "sqlite_limit_ceiling", "sqlite_limit_near_ceiling", "sqlite_limit_near_ceiling_late",
                                  "sqlite_limit_cap_drop", "sqlite_limit_cap_drop_native_v2", "sqlite_limit_cap_drop_native_late_v2",
                                  "sqlite_limit_cap_stale", "sqlite_limit_cap_inside", "sqlite_partial_anchor_drop",
                                  "m21_sqlite_limit_v2", "m21_sqlite_limit_late_v2",
                                  "m21_sqlite_limit_cancel", "m21_sqlite_limit_cap_drop_native_v2",
                                  "m21_sqlite_limit_low",
                                  *["sqlite_limit_gtd_tail_" + mode for mode in (
                                      "matched", "null", "empty", "blank", "404", "below", "boundary", "not_expired",
                                      "still_open", "orders_incomplete", "trades_incomplete", "query_failed",
                                      "nonfinal", "quantity_conflict", "owner_conflict", "taker_fee", "two_sides",
                                  )],
                                  *["sqlite_limit_gtd_chase_" + mode for mode in (
                                      "equal", "higher_v2", "cancel_fill", "cancel_timeout", "cancel_budget", "ask_back", "ask_stale",
                                      "cutoff", "unknown", "restart_ready", "restart_unknown", "native_ask_back_v2",
                                      "restart_cancel", "cancel_ack_lags", "partial_v2", "stale_before_sign", "scheduler_v2",
                                      "concurrent_v2", "finer_tick", "no_cushion_v2", "example_v2",
                                      "native_within_limit_v2", "native_above_limit_v2",
                                  )]])
async def test_follow_native_orders(tmp_path, monkeypatch, case):
    assert Path(__import__("polymaker").__file__).resolve().is_relative_to(FRAMEWORK)
    primary, lead, parent, clock, _, mock_transport = await mock_live_market(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock.now))
    strategy = "M21" if case.startswith("m21_") else "M12"
    case = case.removeprefix("m21_")
    cfg = strategy_settings(FRAMEWORK, M421_LEDGER, M4LiveSettings.load(FRAMEWORK),
                            signal_profile="M42112", follow_strategy=strategy)
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
        reader = SQLiteIntents(tmp_path, meta, 5 if case.startswith("sqlite_limit") else 3, strategy=strategy)
        if case == "sqlite_limit_gtd_chase_no_cushion":
            reader.chase_fak_extra_ticks = 0
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
    if case in {"sqlite_limit_near_ceiling", "sqlite_limit_near_ceiling_late"}:
        ask = ".85"
    if case == "sqlite_limit_at_cap":
        ask = ".87"
    if case in {"sqlite_limit_late_no", "sqlite_limit_gtd_chase_example"}:
        ask = ".67"
    rows = [{"event_type": "book", "market": meta.condition_id, "asset_id": t.token_id,
             "timestamp": str(feed.wall() * 1000),
             "bids": [{"price": ".66" if ask == ".67" else ".70", "size": "100"}],
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
    fees = primary.ledger.fees
    if case.startswith("sqlite_limit_gtd_tail_"):
        fees = {**fees, "rate": "0.05"}

    def open_ledger():
        path = tmp_path / "m421-ledgers" / f"{meta.slug}.sqlite3"
        return BudgetLedger(path, identity, settings=cfg, fees=fees)

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

    async def restart_follow_market():
        nonlocal ledger, market
        await writer.flush()
        ledger.close()
        ledger = open_ledger()
        executor.ledger = reads.gateway.m4_ledger = reads.gateway._client.m4_ledger = ledger
        executor.known.clear()
        market = BudgetMarket(executor, cfg, executor.stop_event, account_owner=primary, display=lambda _: None)
        market.restore()
        return market

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
            intent = dict(opportunity=fact["id"], strategy=strategy, profile=strategy,
                          signal_version=sqlite_source.BASELINE_VERSIONS[strategy],
                          order_type="ASSUMED_FIXED_ANCHOR", market=meta.slug, side="BUY", token="1",
                          outcome="YES", price=".70", quantity="7.1", available=clock.now, deadline=clock.now+1)
            intent["price"] = {
                "sqlite_limit_low": ".46", "sqlite_limit_floor": ".50", "sqlite_limit_ceiling": ".90",
                "sqlite_limit_near_ceiling": ".89", "sqlite_limit_near_ceiling_late": ".89",
                "sqlite_limit_late": ".82", "sqlite_limit_at_cap": ".82",
            }.get(case, intent["price"])
            if case.startswith("sqlite_limit_gtd_tail_"):
                intent["price"] = ".64"
            if case in {"sqlite_limit_late_no", "sqlite_limit_gtd_chase_example"}:
                intent["price"] = ".58"
            if case == "sqlite_limit_late_no":
                intent.update(outcome="NO", token="2")
            if sqlite_mode:
                if case == "sqlite_limit_burst":
                    append_intent({**intent, "opportunity": "earlier-in-same-read"})
                append_intent({**intent, "available": clock.now - 45, "deadline": clock.now - 42})
            else:
                engine.store.emit("INTENT", {"scenario": "sop/anchor", "intent": intent})
                advance_source()
            if case in {"sqlite_limit_low", "sqlite_limit_floor", "sqlite_limit_ceiling"}:
                feed.follow_market = market
                feed.connected = False
                if case != "sqlite_limit_floor":
                    feed.tick_verified.pop("1")
                feed.m1.refresh(force=True)
                assert feed.wanted_token() is None
            market.collect()
            if case in {"sqlite_limit_low", "sqlite_limit_ceiling"}:
                row, = ledger.rows()
                assert (row["state"], row["reason"]) == ("SKIPPED", "M12_INTENT_ANCHOR_OUTSIDE_PRICE_BAND")
                assert not market.pending and not calls and not ledger.attempts()
                assert not ledger.facts("taker_plan")
                assert not market.intent_needs_quotes(row["id"]) and feed.wanted_token() is None
                filtered, = ledger.metric()["by_follow_intent"]
                assert filtered["posts_started"] == 0 and filtered["principal_completion_ratio"] == "0"
                ledger.validate_recovery()
                market.restore()
                assert not market.pending and ledger.row(row["id"]) == row
                append_intent({**intent, "opportunity": "later", "token": "2", "outcome": "NO", "price": ".65"})
                market.collect()
                assert feed.wanted_token() == "2" and len(market.pending) == 1
                return
            if case == "sqlite_limit_floor":
                assert market.pending and feed.wanted_token() == "1"
                feed.connected = True
                push_ask(".49")
            [opportunity] = market.pending.values()
            assert feed.m1.provenance(opportunity.episode_id)[1] == strategy
            if sqlite_mode:
                assert ledger.root_plan(opportunity.episode_id)["detector_source"] == strategy
            market.collect()
            assert len(ledger.rows()) == 1
            if case == "sqlite_limit_cap_inside":
                push_ask(".66")  # The frozen cap of .75 is .09 above this ask.
                push_ask(".50", token="2")
            if case == "sqlite_limit_cap_stale":
                push_ask(".65")
                clock.now += .201
                await market.submit(opportunity)
                assert not calls and not ledger.attempts()
                assert market.guard_failure(opportunity.episode_id) == "M12_WAIT_FRESH_BEST_ASK_BEFORE_POST"
                assert not ledger.get("m12_anchor_invalidation:" + opportunity.episode_id)
                push_ask(".66")
            if case.startswith("sqlite_limit_cap_drop"):
                key = opportunity.episode_id
                if case.endswith("_late"):
                    executor.official_time = int(meta.slug.rsplit("-", 1)[1]) + 140
                if "_native" in case:
                    def drop_at_native_guard(attempt):
                        push_ask(".65")
                        return market.native_guard(attempt)
                    monkeypatch.setattr(reads.gateway._client, "m4_native_guard", drop_at_native_guard)
                else:
                    push_ask(".65")
                await market.submit(opportunity)
                assert not calls
                saved = ledger.get("m12_anchor_invalidation:" + key)
                assert saved["token_id"] == "1" and saved["drop"] == "0.10"
                assert (saved["anchor_price"], saved["frozen_cap"], saved["best_ask"]) == ("0.70", "0.75", "0.65")
                row = ledger.row(key)
                assert (row["state"], row["reason"]) == ("SKIPPED", "M12_BEST_ASK_BELOW_FROZEN_CAP")
                assert not row["order_id"] and Decimal(row["reserved"]) == 0
                if "_native" in case:
                    attempt, = ledger.attempts()
                    assert attempt["state"] == "UNSENT"
                    assert json.loads(attempt["plan"])["order_type"] == "GTD"
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
                executor.meta = replace(executor.meta, min_order_size=10)
            if case == "sqlite_limit_T240":
                executor.official_time = int(meta.slug.rsplit("-", 1)[1]) + 240
                await market.submit(opportunity)
                assert not calls
                return
            epoch = int(meta.slug.rsplit("-", 1)[1])
            if case == "sqlite_limit_native_expiry":
                def change_time(attempt):
                    executor.official_time = epoch + 151
                    return market.native_guard(attempt)

                with monkeypatch.context() as patch:
                    patch.setattr(reads.gateway._client, "m4_native_guard", change_time)
                    await market.submit(opportunity)
                blocked, = ledger.attempts()
                assert not calls and blocked["state"] == "UNSENT"
                assert json.loads(blocked["proof"])["reason"] == "M12_GTD_EXPIRATION_TOO_SOON"
                assert not ledger.gtd_chase(opportunity.episode_id)
            if case in {"sqlite_limit_late", "sqlite_limit_at_cap", "sqlite_limit_near_ceiling_late"}:
                executor.official_time = epoch + 140
            if case == "sqlite_limit_late_no":
                executor.official_time = epoch + 151
            await market.submit(opportunity)
            if case == "sqlite_limit_small":
                assert not calls and not ledger.attempts()
                assert ledger.row(opportunity.episode_id)["reason"] == "M12_GTD_BELOW_MIN_SHARES"
                return
            if case == "sqlite_limit_gtd_retry":
                await verify_gtd_retry(market, opportunity, http, calls, monkeypatch)
                return
            if case.startswith("sqlite_limit_gtd_chase_"):
                mode = case.removeprefix("sqlite_limit_gtd_chase_")
                await verify_gtd_chase(market, opportunity, mode, clock, push_ask, http, calls, monkeypatch,
                                       restart_follow_market)
                return
            if case.startswith("sqlite_limit_gtd_tail_"):
                mode = case.removeprefix("sqlite_limit_gtd_tail_")
                assert len(calls) == 1 and calls[0]["orderType"] == "GTD"
                first, = ledger.attempts()
                first_plan = json.loads(first["plan"])
                assert (first_plan["limit_price"], first_plan["quantity"]) == ("0.69", "7.24")
                first_trades = await verify_gtd_tail(market, first, mode, http, monkeypatch)
                if mode == "two_sides":
                    append_intent({**intent, "opportunity": "later-no", "outcome": "NO", "token": "2"})
                    market.collect()
                    no_intent, = (op for op in market.pending.values() if op.outcome == "NO")
                    await market.submit(no_intent)
                    assert len(calls) == 2 and calls[-1]["orderType"] == "GTD"
                    second = ledger.attempts()[-1]
                    assert second["order_id"] != first["order_id"]
                    await verify_gtd_tail(market, second, "matched", http, monkeypatch, other_trades=first_trades)
                    assert ledger.row(first["task_id"])["outcome"] == "YES"
                    assert ledger.row(second["task_id"])["outcome"] == "NO"
                    assert len(ledger.db.execute("SELECT * FROM taker_trade_evidence").fetchall()) == 4
                return
            if case.startswith("sqlite_limit"):
                assert len(calls) == 1, (market.guard_failure(opportunity.episode_id), ledger.attempts(), ledger.rows())
                sent, = calls
                plan = json.loads(ledger.attempts()[-1]["plan"])
                assert sent["orderType"] == "GTD"
                assert sent["postOnly"] is False
                limit = Decimal({"sqlite_limit_floor": ".55", "sqlite_limit_late": ".87", "sqlite_limit_at_cap": ".87",
                                 "sqlite_limit_late_no": ".63", "sqlite_limit_near_ceiling": ".94",
                                 "sqlite_limit_near_ceiling_late": ".94"}.get(case, ".75"))
                assert Decimal(plan["limit_price"]) == limit
                assert int(sent["order"]["expiration"]) == plan["expiration"]
                assert plan["expiration"] >= epoch + 300
                assert plan["expiration"] >= executor.official_time + 180
                assert 0 < Decimal(sent["order"]["makerAmount"]) / Decimal(sent["order"]["takerAmount"]) <= limit
                if case == "sqlite_limit_late":
                    assert int(sent["order"]["makerAmount"]) == 4_993_800
                    assert int(sent["order"]["takerAmount"]) == 5_740_000
                if case == "sqlite_limit_late_no":
                    assert sent["order"]["tokenId"] == "2"
                    assert epoch + 240 - executor.official_time == 89
                    assert Decimal(sent["order"]["makerAmount"]) / Decimal(sent["order"]["takerAmount"]) == limit
                if case == "sqlite_limit_at_cap":
                    assert plan["pricing"]["ask_at_cap"] is True
                    assert int(sent["order"]["makerAmount"]) == 4_993_800
                    assert int(sent["order"]["takerAmount"]) == 5_740_000
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
                assert calls[0]["orderType"] == "GTD"
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
                    order.update(order_type="GTD", status="CANCELED")
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
                    order.update(order_type="GTD", status="CANCELED")
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
                            assert calls[1]["orderType"] == "GTD"
                            assert 0 < int(calls[1]["order"]["makerAmount"]) <= 3_600_000
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


async def verify_gtd_retry(market, opportunity, original, posts, monkeypatch):
    ledger, executor, key = market.ledger, market.e, opportunity.episode_id
    first, = ledger.attempts(key)
    assert [post["orderType"] for post in posts] == ["GTD"]
    orders, cancellations = {}, []

    def live_order(order_id):
        order, _ = evidence(market, executor, order_id, "0")
        orders[order_id] = {**order, "order_type": "GTD", "status": "LIVE"}
        return orders[order_id]

    def wire(request):
        if request.method == "POST":
            return original._transport.handle_request(request)
        if request.method == "DELETE":
            order_id, = json.loads(request.content)
            cancellations.append(order_id)
            orders[order_id]["status"] = "CANCELED"
            return httpx.Response(200, json={"canceled": [order_id], "not_canceled": {}})
        assert request.url.path.startswith("/data/order/")
        return httpx.Response(200, json=orders[request.url.path.rsplit("/", 1)[1]])

    with httpx.Client(transport=httpx.MockTransport(wire)) as query, monkeypatch.context() as patch:
        patch.setattr(helpers, "_http_client", query)
        order = live_order(first["order_id"])
        await market.read_order(first["order_id"])
        await market.submit(opportunity)
        assert len(posts) == 1
        order["status"] = "CANCELED"
        await market.read_order(first["order_id"])
        assert ledger.attempt(first["order_id"])["state"] == "TERMINAL"
        await market.submit(opportunity)
        assert [post["orderType"] for post in posts] == ["GTD", "GTD"]
        assert ledger.gtd_chase(key)["old_order_id"] == first["order_id"]
        second_id = ledger.attempts(key)[-1]["order_id"]
        live_order(second_id)
        executor.official_time = int(executor.meta.slug.rsplit("-", 1)[1]) + 240
        await market.read_order(second_id)
        assert cancellations == [second_id]
        assert ledger.attempt(second_id)["state"] == "TERMINAL"
        await market.submit(opportunity)
        assert len(posts) == 2
        ledger.validate_recovery()


async def verify_gtd_chase(market, opportunity, mode, clock, push_ask, original, posts, monkeypatch,
                           restart_follow_market):
    ledger, e, key = market.ledger, market.e, opportunity.episode_id
    old, = ledger.attempts(key)
    oid = old["order_id"]
    state = ledger.gtd_chase(key)
    assert old["state"] == "ACK" and posts[0]["orderType"] == "GTD"
    assert state["phase"] == "WATCH" and state["old_order_id"] == oid
    stamp = state["first_post"]["monotonic"]
    order, _ = evidence(market, e, oid, "0")
    order.update(order_type="GTD", status="LIVE")
    cancellations, trades = [], {}
    chase_posts = []
    chase_order = None
    owner = market.account_owner
    gateway = e.gateway
    trigger = ".68" if mode == "example" else ".80"
    ask = ".69" if mode == "example" else ".81"
    frozen_cap = Decimal(".63" if mode == "example" else ".75")
    extra_ticks = 0 if mode == "no_cushion" else 5
    chase_limit = Decimal(ask) + extra_ticks * Decimal(".01")

    def wire(request):
        nonlocal chase_order
        if request.method == "POST":
            planned = gateway._client._operation_value("attempt").expected_order_id
            plan = json.loads(gateway._client.m4_ledger.attempt(planned)["plan"])
            if plan["pricing"]["stage"] != "GTD_CHASE_FAK":
                return original._transport.handle_request(request)
            row, = json.loads(request.content)
            posts.append(row)
            chase_posts.append(planned)
            if mode == "restart_unknown":
                raise httpx.ReadTimeout("chase response lost", request=request)
            if mode == "partial":
                chase_order, trade = evidence(market, e, planned, "1")
                trades[trade["id"]] = trade
                return httpx.Response(200, json=[{"success": True, "orderID": planned}])
            return httpx.Response(200, json=[{"success": False, "errorMsg": FAK_NO_MATCH}])
        if request.method == "DELETE":
            assert json.loads(request.content) == [oid]
            cancellations.append(oid)
            if mode != "cancel_ack_lags":
                order["status"] = "CANCELED"
            if mode == "cancel_timeout":
                raise httpx.ReadTimeout("cancel response lost", request=request)
            if mode == "cancel_fill":
                _, trade = evidence(market, e, oid, "1")
                trades[trade["id"]] = trade
                order.update(size_matched="1", associate_trades=[trade["id"]])
            if mode == "cancel_budget":
                owner.account["official_balance_usd"] = ".99"
            if mode == "ask_back":
                push_ask(".80")
            if mode == "finer_tick":
                push_ask(".79")
                with market.feed.lock:
                    market.feed.books["1"] = replace(market.feed.books["1"], tick=Decimal(".001"))
                    market.feed.tick_verified["1"] = Decimal(".001")
            if mode == "ask_stale":
                clock.now += .25
            if mode == "cutoff":
                e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + 240
            return httpx.Response(200, json={"canceled": [oid], "not_canceled": {}})
        if request.url.path == f"/data/order/{oid}":
            if mode == "unknown" and clock.now >= stamp + 5:
                return httpx.Response(503, json={"error": "order read unavailable"})
            return httpx.Response(200, json=order)
        if request.url.path.startswith("/data/order/"):
            if chase_order and request.url.path == f"/data/order/{chase_order['id']}":
                return httpx.Response(200, json=chase_order)
            return httpx.Response(404, json={"error": "unknown FAK"})
        assert request.url.path == "/data/trades"
        return httpx.Response(200, json={"data": [trades[request.url.params["id"]]], "next_cursor": "LTE="})

    async def restore():
        nonlocal ledger, market
        market = await restart_follow_market()
        ledger = market.ledger

    try:
        with httpx.Client(transport=httpx.MockTransport(wire)) as query, monkeypatch.context() as patch:
            patch.setattr(helpers, "_http_client", query)
            clock.now = stamp + 4.999
            push_ask(ask)
            await market.read_order(oid)
            assert not cancellations and ledger.gtd_chase(key)["phase"] == "WATCH"
            clock.now = stamp + 5
            owner.account_at = clock.now
            push_ask(trigger if mode == "equal" else ask)
            if mode == "restart_cancel":
                assert market.change_chase(key, "WATCH", "CANCELING")
                await restore()
                order["status"] = "CANCELED"
                await market.read_order(oid)
                assert ledger.attempt(oid)["state"] == "TERMINAL"
                assert ledger.gtd_chase(key)["reason"] == "M12_GTD_CHASE_CANCEL_INTERRUPTED"
                assert not cancellations and not chase_posts and len(posts) == 1
                return
            if mode == "scheduler":
                market.read_after[oid] = clock.now + 30
                await market.advance()
                await market.read_jobs[oid]
            else:
                await market.read_order(oid)
            state = ledger.gtd_chase(key)
            assert Decimal(state["trigger_check"]["threshold"]) == Decimal(trigger)
            assert Decimal(state["trigger_check"]["best_ask"]) == Decimal(trigger if mode == "equal" else ask)
            if mode in {"equal", "unknown"}:
                assert not cancellations and state["phase"] == "STOPPED" and len(posts) == 1
                return
            assert cancellations == [oid]
            if mode == "cancel_ack_lags":
                assert state["phase"] == "WAIT_CLOSE" and ledger.attempt(oid)["state"] == "ACK"
                await market.submit(opportunity)
                assert len(posts) == 1
                order["status"] = "CANCELED"
                await market.read_order(oid)
                state = ledger.gtd_chase(key)
            if mode in {"cancel_fill", "cancel_timeout", "cancel_budget", "ask_back", "ask_stale", "cutoff", "finer_tick"}:
                if mode == "cancel_timeout":
                    assert ledger.attempt(oid)["state"] != "TERMINAL"
                    await market.read_order(oid)
                    assert ledger.attempt(oid)["state"] == "TERMINAL"
                if mode == "cancel_fill":
                    assert Decimal(ledger.attempt(oid)["filled"]) == 1
                if mode == "cancel_budget":
                    assert ledger.attempt(oid)["state"] == "TERMINAL"
                    assert state["reason"] == "M12_GTD_CHASE_NO_LEGAL_AMOUNT"
                if mode == "finer_tick":
                    assert state["final_check"]["threshold"] == "0.80"
                    assert state["final_check"]["tick"] == "0.001"
                    assert state["reason"] == "M12_GTD_CHASE_AT_OR_BELOW_THRESHOLD"
                assert ledger.gtd_chase(key)["phase"] == "STOPPED" and len(posts) == 1
                return
            assert state["phase"] == "READY" and state["zero_cancel_verified"] is True
            assert ledger.attempt(oid)["state"] == "TERMINAL"
            if mode == "restart_ready":
                await restore()
            native_ask = {"native_ask_back": trigger, "native_within_limit": ".85", "native_above_limit": ".87"}.get(mode)
            if native_ask is not None:
                def native_guard(attempt):
                    push_ask(native_ask)
                    return market.native_guard(attempt)
                patch.setattr(gateway._client, "m4_native_guard", native_guard)
            if mode == "stale_before_sign":
                clock.now += .25
            if mode == "scheduler":
                await market.advance()
                await market.post_jobs[key]
            elif mode == "concurrent":
                await asyncio.gather(market.submit(opportunity), market.submit(opportunity))
            else:
                await market.submit(opportunity)
            saved = ledger.gtd_chase(key)
            if mode == "stale_before_sign":
                assert not chase_posts and len(posts) == 1 and saved["phase"] == "STOPPED"
                assert saved["reason"] == "M12_WAIT_FRESH_BEST_ASK_BEFORE_POST"
                return
            if mode in {"native_ask_back", "native_above_limit"}:
                assert not chase_posts and len(posts) == 1 and saved["phase"] == "STOPPED", market.guard_failure(key)
                assert ledger.attempt(saved["new_order_id"])["state"] == "UNSENT"
                if mode == "native_above_limit":
                    assert saved["reason"] == "M12_GTD_CHASE_ASK_ABOVE_SIGNED_LIMIT"
                return
            if mode == "concurrent":
                assert [post["orderType"] for post in posts] == ["GTD", "FAK", "GTD"]
                assert chase_posts == [saved["new_order_id"]]
                assert Decimal(json.loads(ledger.attempts(key)[-1]["plan"])["limit_price"]) <= frozen_cap
                ledger.validate_recovery()
                return
            assert len(posts) == 2 and chase_posts == [saved["new_order_id"]], (market.guard_failure(key), saved)
            assert posts[1]["orderType"] == "FAK" and posts[1]["order"]["side"] == "BUY"
            assert posts[1]["order"]["tokenId"] == posts[0]["order"]["tokenId"] == "1"
            assert Decimal(posts[1]["order"]["makerAmount"]) / Decimal(posts[1]["order"]["takerAmount"]) == chase_limit
            pricing = json.loads(ledger.attempt(saved["new_order_id"])["plan"])["pricing"]
            assert pricing["chase_fak_extra_ticks"] == extra_ticks and Decimal(pricing["best_ask"]) == Decimal(ask)
            if mode == "restart_unknown":
                assert saved["phase"] == "SENT"
                await restore()
                await market.submit(opportunity)
                assert len(posts) == 2 and chase_posts == [saved["new_order_id"]]
                assert ledger.attempt(saved["new_order_id"])["state"] == "UNKNOWN"
            else:
                if mode == "partial":
                    await market.read_order(saved["new_order_id"])
                    saved = ledger.gtd_chase(key)
                    assert Decimal(saved["filled"]) == 1
                    assert ledger.principal_used(key) == chase_limit
                else:
                    assert Decimal(saved["filled"]) == 0
                assert saved["phase"] == "DONE"
                await market.submit(opportunity)
                newest = json.loads(ledger.attempts(key)[-1]["plan"])
                assert [post["orderType"] for post in posts] == ["GTD", "FAK", "GTD"]
                assert Decimal(newest["limit_price"]) <= frozen_cap
                assert Decimal(newest["principal_usdc"]) <= Decimal(5) - ledger.principal_used(key)
                assert chase_posts == [saved["new_order_id"]]
            ledger.validate_recovery()
    finally:
        if ledger is not gateway._client.m4_ledger:
            raise AssertionError("fixture ledger diverged")
        if ledger is not e.ledger:
            raise AssertionError("fixture executor ledger diverged")


async def verify_gtd_tail(market, attempt, mode, original, monkeypatch, *, other_trades=()):
    ledger, e, oid = market.ledger, market.e, attempt["order_id"]
    plan, quote = json.loads(attempt["plan"]), json.loads(attempt["quote"])
    assert plan["order_type"] == "GTD"
    tail = Decimal({"below": "0.099999", "boundary": "0.100000"}.get(mode, "0.009354"))
    total = Decimal(plan["quantity"]) - tail
    order, _ = evidence(market, e, oid, total)
    order.update(order_type="GTD", expiration=str(plan["expiration"]))
    trades = []
    for number, quantity in enumerate((Decimal(3), total - 3), 1):
        trades.append({
            "id": f"gtd-trade-{attempt['seq']}-{number}", "market": order["market"],
            "trader_side": "MAKER", "taker_order_id": "counterparty-order",
            "asset_id": "2" if quote["token_id"] == "1" else "1", "side": "BUY",
            "status": "CONFIRMED", "price": str(1 - Decimal(plan["limit_price"])), "size": str(quantity),
            "maker_orders": [{"order_id": oid, "asset_id": quote["token_id"], "side": "BUY",
                              "maker_address": e.gateway.funder,
                              "price": plan["limit_price"], "matched_amount": str(quantity)}],
        })
    order["associate_trades"] = [row["id"] for row in trades]
    fee = Decimal(0)
    if mode == "taker_fee":
        _, take = evidence(market, e, oid, "3")
        take.update(id=trades[0]["id"], maker_address=e.gateway.funder)
        trades[0] = take
        price = Decimal(plan["limit_price"])
        fee = (3 * Decimal(str(plan["fees"]["rate"])) * (price * (1 - price))
               ** plan["fees"]["exponent"]).quantize(Decimal(".00001"), rounding=ROUND_HALF_UP)
        assert fee > 0
    exchange_time = plan["expiration"] + 1
    detail = "matched"
    reads = []

    def page(data, cursor="LTE="):
        return httpx.Response(200, json={"data": data, "next_cursor": cursor, "count": len(data)})

    def wire(request):
        if request.method == "POST":
            return original._transport.handle_request(request)
        path = request.url.path
        reads.append(path)
        if path == "/time":
            return httpx.Response(200, json=exchange_time)
        if path == f"/data/order/{oid}":
            if detail == "404":
                return httpx.Response(404, json={"error": "not found"})
            if detail == "null":
                return httpx.Response(200, content=b"null", headers={"Content-Type": "application/json"})
            if detail == "blank":
                return httpx.Response(200, content=b"")
            return httpx.Response(200, json={} if detail == "empty" else order)
        assert request.url.params["market"] == order["market"]
        assert request.headers["POLY_API_KEY"]
        if path == "/data/orders":
            assert set(request.url.params) == {"market", "next_cursor"}
            if request.url.params["next_cursor"] == "MA==":
                return page([], "NEXT")
            if mode == "orders_incomplete":
                return page([], "NEXT")
            return page([order] if mode == "still_open" else [])
        assert path == "/data/trades"
        trade_id = request.url.params.get("id")
        if trade_id:
            return page([trade for trade in trades if trade["id"] == trade_id])
        assert set(request.url.params) == {"market", "next_cursor"}
        if mode == "query_failed":
            return httpx.Response(503, json={"error": "unavailable"})
        if request.url.params["next_cursor"] == "MA==":
            return page([*other_trades, trades[0]], "NEXT")
        if mode == "trades_incomplete":
            return page([], None)
        last = trades[1]
        if mode == "nonfinal":
            last = {**last, "status": "MINED"}
        if mode == "quantity_conflict":
            last = {**last, "maker_orders": [dict(last["maker_orders"][0], matched_amount="1")]}
        if mode == "owner_conflict":
            last = {**last, "maker_orders": [dict(last["maker_orders"][0], maker_address="0x" + "1" * 40)]}
        return page([trades[0], last])  # A repeated ID across pages appears once in the proof.

    with httpx.Client(transport=httpx.MockTransport(wire)) as query, monkeypatch.context() as patch:
        patch.setattr(helpers, "_http_client", query)
        missing = {"null", "empty", "blank", "404"}
        if mode in missing or mode == "not_expired":
            exchange_time = plan["expiration"]
            await market.reconcile(oid)
            assert ledger.attempt(oid)["state"] == "UNKNOWN"
            assert "/data/orders" not in reads
            exchange_time += mode != "not_expired"
            if mode in missing:
                detail = mode
        reserve = Decimal(ledger.row(attempt["task_id"])["reserved"])
        assert reserve == Decimal(plan["commitment_usdc"]) > 0
        if quote["token_id"] == "1":
            assert reserve == Decimal("5.495161")
        if mode != "two_sides":
            ledger.set("buy_closed", True)
        await market.reconcile(oid)
        saved = ledger.attempt(oid)
        row = ledger.row(attempt["task_id"])
        if mode not in missing | {"matched", "below", "two_sides", "taker_fee"}:
            assert saved["state"] == "UNKNOWN" and Decimal(row["reserved"]) == reserve
            return trades
        principal = sum((Decimal(t["maker_orders"][0]["matched_amount"]) * Decimal(plan["limit_price"]))
                        .quantize(Decimal(".000001"), rounding=ROUND_HALF_UP) for t in trades)
        if mode in missing | {"matched", "taker_fee"} and quote["token_id"] == "1":
            assert (total, principal) == (Decimal("7.230646"), Decimal("4.989146"))
        assert saved["state"] == "TERMINAL" and Decimal(saved["filled"]) == total
        assert Decimal(row["reserved"]) == 0 and Decimal(saved["spent"]) == principal + fee
        assert ledger.confirmed_principal(saved) == principal
        assert row["state"] == ("DUST" if mode == "two_sides" else "CUTOFF")
        proof = json.loads(saved["proof"])
        assert proof["reason"] == "M12_GTD_EXPIRED_TAIL_RECONCILED"
        assert Decimal(proof["gtd_tail"]["remainder"]) == tail
        assert proof["gtd_tail"]["open_order_absent"] is True
        assert (proof["gtd_tail"]["open_order_pages"], proof["gtd_tail"]["trade_pages"]) == (2, 2)
        assert {item["trade_id"] for item in proof["trades"]} == set(order["associate_trades"])
        assert sum(Decimal(item["fee_usdc"]) for item in proof["trades"]) == fee
        if mode in missing:
            assert proof["order"]["detail_http_status"] == (404 if mode == "404" else 200)
        before = len(reads)
        await market.reconcile(oid)
        assert len(reads) == before and ledger.attempt(oid) == saved and ledger.row(attempt["task_id"]) == row
        ledger.validate_recovery()
    return trades


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
    from polymaker.m4_live_pricing import follow_limit_price, order_price_allowed
    from polymaker.m4_types import CLOSED_SIGNED_LIMIT_BAND, POST_SIGNED_LIMIT_BAND

    root = {"anchor_price": ".87", "tick_at_plan": ".01", "pricing_origin": {"overpay_ticks": 5},
            "post_price_band": {"policy": POST_SIGNED_LIMIT_BAND, "minimum": ".50", "maximum": ".90"}}
    assert follow_limit_price(root, Decimal(".01")) == Decimal(".89")
    assert follow_limit_price(root, Decimal(".001")) == Decimal(".899")
    assert not order_price_allowed(".92", root)
    root["post_price_band"]["policy"] = CLOSED_SIGNED_LIMIT_BAND
    assert follow_limit_price(root, Decimal(".01")) == Decimal(".90")
    root["post_price_band"]["policy"] = POST_SIGNED_LIMIT_BAND
    root["pricing_origin"]["follow_anchor_band"] = True
    assert follow_limit_price(root, Decimal(".01")) == Decimal(".92")
    assert follow_limit_price(root, Decimal(".001")) == Decimal(".92")
    assert order_price_allowed(".92", root) and not order_price_allowed(".921", root)
    root["anchor_price"] = ".46"
    assert not order_price_allowed(".51", root)
    root["anchor_price"] = ".72"
    assert follow_limit_price(root, Decimal(".1")) == Decimal(".7")
