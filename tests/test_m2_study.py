"""M2 causal sequences, exploratory accounting and public-only boundaries."""

import json
import time
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest

from polymaker.m1_virtual_api import ReadOnlyTransport
from polymaker.m2_config import M2Config
from polymaker.m2_report import analyze, summarize
from polymaker.m2_resources import CPUHeadroom
from polymaker.m2_signal import M2EventBuffer, M2Signals
from polymaker.m2_store import M2Store, quantity_for_risk
from polymaker.m2_study import M2Study, output_path, write_report
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_ba import trade
from test_rdp_sm_m1_strategy import advance, hit, m1_feed


def setup_feed():
    feed, clock = m1_feed()
    cfg = M2Config()
    cfg.signal.mirror_complement_flow = False
    buffer = M2EventBuffer(wall=feed.wall)
    feed.m1 = M2Signals(feed, settings=cfg, record=buffer)
    return feed, clock, buffer


def buy_attack(feed, clock, start=.02):
    for offset in (0, .26, .51):
        advance(feed, clock, start+offset)
        feed.apply(trade(feed, side="BUY", price=str(feed.books["1"].best_ask), size="10"))
    advance(feed, clock, start+.775)


def formed():
    feed, clock, buffer = setup_feed()
    buy_attack(feed, clock)
    hit(feed, clock, .90)
    hit(feed, clock, 1.50)
    advance(feed, clock, 2.025)
    return feed, clock, buffer


def observations(buffer):
    return [r for r in buffer.rows if r["event"] == "M2_OBSERVATION"]


def test_real_buy_then_new_two_round_refill_forms_candidate_without_position_gate():
    feed, clock, buffer = formed()
    rows = observations(buffer)
    assert rows and any(r["observation_valid"] for r in rows)
    row = rows[-1]
    assert row["anchor_bid_price"] == D(".50")
    assert row["attack_ask_anchor"] == D(".52")
    assert row["anchor_displacement_ticks"] == -2
    assert row["buy_ratio_upper"] == 0
    assert row["reference_volume"] == 30
    assert all(r[0] >= row["confirmed"] for r in row["rf_rounds"])
    assert feed.m1.evaluate(feed.snapshot()).label == "NONE"


def test_refill_without_buy_attack_is_not_m2():
    feed, clock, buffer = setup_feed()
    hit(feed, clock, .02)
    hit(feed, clock, .65)
    advance(feed, clock, 1.20)
    assert feed.m1.counts["RF_CONFIRMED"]
    assert not observations(buffer)


def test_pre_attack_refill_is_cleared_and_cannot_be_reused():
    feed, clock, buffer = setup_feed()
    hit(feed, clock, .02)
    hit(feed, clock, .65)
    advance(feed, clock, 1.20)
    assert feed.m1.state("1")["rf"]
    buy_attack(feed, clock, 1.30)
    assert feed.m1.state("1")["rf"] is None
    assert not observations(buffer)
    hit(feed, clock, 2.20)
    hit(feed, clock, 2.80)
    advance(feed, clock, 3.325)
    assert observations(buffer)


def test_features_continue_after_initial_rf_and_keep_actual_buy_volume():
    feed, clock, buffer = formed()
    count = len(observations(buffer))
    advance(feed, clock, 2.10)
    feed.apply(trade(feed, side="BUY", price=".52", size="1"))
    advance(feed, clock, 2.55)
    rows = observations(buffer)
    assert len(rows) > count
    assert any(r["buy_ratio"] == D(1)/30 for r in rows)
    assert len({r["candidate_id"] for r in rows}) == 1


@pytest.mark.parametrize("reason", ["WS_DISCONNECTED", "M1_PROCESSING_GAP", "M1_TICK_CHANGED"])
def test_gaps_remove_both_campaign_and_candidate(reason):
    feed, clock, buffer = formed()
    feed.m1.invalidate(reason)
    count = len(observations(buffer))
    assert not feed.m1.candidates and not feed.m1.campaigns
    advance(feed, clock, 2.60)
    assert len(observations(buffer)) == count


def test_uncertain_buy_volume_is_conservative_and_missing_coverage_is_invalid():
    feed, clock, buffer = formed()
    state = feed.m1.state("1")
    state["uncertain_trades"].append((clock.now-.1, "BUY", D(15)))
    state["coverage"].clear()
    candidate = feed.m1.candidates["1"]
    feed.m1.observe(candidate, state, feed.books["1"], clock.now)
    row = observations(buffer)[-1]
    assert row["buy_ratio"] == 0
    assert row["buy_ratio_upper"] == D(".5")
    assert not row["observation_valid"]


def test_pending_input_disqualifies_observation_without_hiding_features():
    feed, clock, buffer = formed()
    feed.m1_pending_received.append(clock.now-.05)
    feed.m1.observe(feed.m1.candidates["1"], feed.m1.state("1"), feed.books["1"], clock.now)
    assert not observations(buffer)[-1]["observation_valid"]


def test_buffer_overflow_fails_closed():
    buffer = M2EventBuffer(capacity=1)
    row = {"event": "M2_CANDIDATE", "available_monotonic": 1}
    assert buffer(row)
    assert not buffer(row)
    assert len(buffer.rows) == 1


def test_per_level_diagnostics_keep_counts_without_allocating_evidence_rows():
    feed, _, buffer = setup_feed()
    before = len(feed.m1.events)
    for _ in range(100):
        feed.m1.emit("EVENT_DECISION", accepted=True, kind="price_change")
    assert feed.m1.counts["EVENT_DECISION"] == 100
    assert len(feed.m1.events) == before
    assert not buffer.rows


@pytest.mark.parametrize("price", [".0001", ".01", ".19", ".20", ".50", ".90", ".99", ".9999"])
def test_full_price_range_risk_never_exceeds_five(price):
    quantity = quantity_for_risk(price, 5)
    assert quantity >= 5
    assert 0 < quantity*D(price) <= 5
    assert (quantity+D(".01"))*D(price) > 5


@pytest.mark.parametrize("price", ["0", "1", "NaN", "Infinity", "-.1"])
def test_invalid_prices_rejected(price):
    with pytest.raises(ValueError):
        quantity_for_risk(price, 5)


def test_market_minimum_does_not_expand_risk():
    with pytest.raises(ValueError, match="MINIMUM_EXCEEDS_RISK"):
        quantity_for_risk(".9", 10)


META = {"slug": "btc-updown-5m-1793710500", "condition_id": "0x"+"a"*64,
        "tokens": [{"token_id": "1", "outcome": "Up"}, {"token_id": "2", "outcome": "Down"}]}


def row(ap="ap1", **changes):
    return {"event": "M2_OBSERVATION", "candidate_id": "candidate-"+ap, "rf_id": "rf-"+ap,
            "ap_id": ap, "available_monotonic": 20., "observed_wall": 100.,
            "observation_valid": True, "visible_bid_quantity": D(100),
            "anchor_bid_price": D(".50"), "tick": D(".01"), "book_best_ask": D(".52"),
            "buy_ratio_upper": D(".4"), "outcome": "YES", **changes}


@pytest.fixture
def ledger(tmp_path):
    store = M2Store(tmp_path, M2Config(), "version1")
    store.start("run1", META)
    yield store
    store.close()


def ingest(store, rows):
    store.ingest("run1", META["slug"], rows, bounds=lambda mono: (mono, mono+1), minimum=5)


def test_each_rule_uses_first_qualifying_observation_not_future_information(ledger):
    ingest(ledger, [row(buy_ratio_upper=D(2))])
    assert [r[0] for r in ledger.db.execute("SELECT rule FROM samples")] == ["all_candidates"]
    ingest(ledger, [row(available_monotonic=21, buy_ratio_upper=D(".2"))])
    assert ledger.db.execute("SELECT count(*) FROM samples").fetchone()[0] == 5
    times = dict(ledger.db.execute("SELECT rule,elapsed FROM samples"))
    assert times["all_candidates"] == 20
    assert times["buy_ratio_le_0.25"] == 21
    ingest(ledger, [row(candidate_id="new-rf-same-ap")])
    assert ledger.db.execute("SELECT count(*) FROM samples").fetchone()[0] == 5


def test_budget_caps_trades_but_preserves_every_research_sample(ledger):
    ledger.settings = M2Config(market_budget_usdc=D(5))
    ingest(ledger, [row("ap0")]+[row(f"ap{i}", outcome="NO") for i in range(1, 6)])
    rows = list(ledger.db.execute("SELECT * FROM samples WHERE rule='all_candidates' ORDER BY id"))
    assert len(rows) == 6
    assert sum(r["status"] == "ASSUMED_FILL" for r in rows) == 1
    assert all(r["reason"] == "MARKET_BUDGET_EXHAUSTED" for r in rows[1:])
    ledger.settle(META["slug"], {"slug": META["slug"], "winner": "YES"})
    rows = list(ledger.db.execute("SELECT * FROM samples WHERE rule='all_candidates' ORDER BY id"))
    assert D(rows[0]["sample_pnl"]) == 5
    assert all(D(r["sample_pnl"]) == -5 for r in rows[1:])
    assert all(r["trade_pnl"] is None for r in rows[1:])


def test_one_fill_per_direction_per_market_rule_persists_after_reopen(ledger):
    ingest(ledger, [row("yes1", buy_ratio_upper=D(2)), row("yes2", buy_ratio_upper=D('.2')),
                   row("no1", outcome="NO", buy_ratio_upper=D('.2'))])
    directory, cfg = ledger.directory, ledger.settings
    ledger.close()
    ledger.__init__(directory, cfg, "version1")
    ingest(ledger, [row("yes3", buy_ratio_upper=D('.2')), row("no2", outcome="NO", buy_ratio_upper=D('.2'))])
    for rule in cfg.rules:
        fills = list(ledger.db.execute("SELECT outcome,ap_id FROM samples WHERE rule=? AND status='ASSUMED_FILL' ORDER BY id", (rule,)))
        assert [(r['outcome'], r['ap_id']) for r in fills] == [
            ('YES', 'yes1' if rule == 'all_candidates' else 'yes2'), ('NO', 'no1')]
    skipped = list(ledger.db.execute("SELECT * FROM samples WHERE status='SKIPPED'"))
    assert skipped and all(r['reason'] == 'DIRECTION_ENTRY_LIMIT_REACHED' for r in skipped)
    ledger.settle(META['slug'], {'slug': META['slug'], 'winner': 'YES'})
    report = analyze(directory, include_incomplete=True)['rules']['all_candidates']['overall']
    assert report['settled_assumed_fills'] == 2
    assert D(report['settled_pnl']) == 0
    assert report['direction_limit_skipped_samples'] == 3
    assert report['mean_outcome_minus_price'] == 0


def test_rejected_minimum_does_not_consume_direction_slot(ledger):
    ledger.ingest('run1', META['slug'], [row('too-small', anchor_bid_price=D('.9'), book_best_ask=D('.91'))],
                  bounds=lambda mono: (mono, mono+1), minimum=10)
    ingest(ledger, [row('fits')])
    assert ledger.db.execute("SELECT ap_id FROM samples WHERE rule='all_candidates' AND status='ASSUMED_FILL'").fetchone()[0] == 'fits'


def test_direction_limit_resets_in_next_market(ledger):
    ingest(ledger, [row('first')])
    meta = {**META, 'slug': 'btc-updown-5m-1793710800'}
    ledger.start('run2', meta)
    ledger.ingest('run2', meta['slug'], [row('next')], bounds=lambda mono: (mono, mono+1), minimum=5)
    assert ledger.db.execute("SELECT count(*) FROM samples WHERE rule='all_candidates' AND status='ASSUMED_FILL'").fetchone()[0] == 2


@pytest.mark.parametrize("changes", [
    {"observation_valid": False}, {"visible_bid_quantity": D(0)},
    {"available_monotonic": 14}, {"available_monotonic": 240},
    {"anchor_bid_price": D(".505")}, {"book_best_ask": D(".49")},
    {"clock_fresh": False},
])
def test_invalid_or_out_of_window_observations_never_trade(ledger, changes):
    ingest(ledger, [row(**changes)])
    assert ledger.db.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    assert ledger.db.execute("SELECT count(*) FROM samples").fetchone()[0] == 0


def test_pending_resolution_and_restart_preserve_history(ledger):
    ingest(ledger, [row()])
    assert ledger.db.execute("SELECT trade_pnl FROM samples LIMIT 1").fetchone()[0] is None
    ledger.recover()
    assert ledger.has_market(META["slug"])
    assert ledger.pending()[0]["slug"] == META["slug"]
    assert ledger.db.execute("SELECT state FROM runs").fetchone()[0] == "INTERRUPTED"
    ledger.settle(META["slug"], {"slug": META["slug"], "winner": "NO"})
    assert D(ledger.db.execute("SELECT trade_pnl FROM samples LIMIT 1").fetchone()[0]) == -5
    with pytest.raises(ValueError, match="CONFLICT"):
        ledger.settle(META["slug"], {"slug": META["slug"], "winner": "YES"})


def test_event_time_bounds_are_not_replaced_by_a_later_clock_read(ledger):
    ingest(ledger, [row(elapsed_lower=14, elapsed_upper=15)])
    assert ledger.db.execute("SELECT count(*) FROM samples").fetchone()[0] == 0


def test_reports_separate_versions_and_identify_assumption(ledger):
    ingest(ledger, [row()])
    ledger.version = "version2"
    ingest(ledger, [row()])
    report = ledger.report({"state": "TEST"})
    assert len({r["version"] for r in report["rules"]}) == 2
    assert report["real_posts"] == 0
    assert report["phase"] == "EXPLORATION_NOT_HOLDOUT_VALIDATION"
    write_report(ledger.directory, report)
    assert json.loads((ledger.directory/"latest.json").read_text())["candidate_count"] == 0
    assert "假设足额成交" in (ledger.directory/"report.md").read_text()


def test_output_cannot_point_to_m1_or_wallet_directories():
    with pytest.raises(ValueError, match="OUTPUT_MUST"):
        output_path("research_outputs/m1_virtual")
    with pytest.raises(ValueError, match="OUTPUT_MUST"):
        output_path("config")


@pytest.mark.parametrize("method,url", [
    ("POST", "https://clob.polymarket.com/order"),
    ("DELETE", "https://clob.polymarket.com/order"),
    ("GET", "https://data-api.polymarket.com/positions"),
])
async def test_public_transport_blocks_account_and_exchange_writes(method, url):
    called = []
    async with httpx.AsyncClient(transport=ReadOnlyTransport(httpx.MockTransport(
        lambda r: called.append(r)))) as client:
        with pytest.raises(RuntimeError, match="NETWORK_WRITE_OR_HOST_DENIED"):
            await client.request(method, url)
    assert called == []


async def test_settlement_service_uses_exact_official_identity(tmp_path):
    class API:
        async def resolution(self, expected):
            assert expected["token_ids"] == {"YES": "1", "NO": "2"}
            return {"slug": expected["slug"], "winner": "YES"}

        async def close(self):
            pass

    study = M2Study(M2Config(), tmp_path, api=API())
    try:
        study.store.start("run1", META)
        study.store.finish("run1", META["slug"], full_window=True, complete=True, counts={})
        await study.resolutions()
        await study.resolve_task
        await study.resolutions()
        assert study.store.db.execute("SELECT winner FROM markets").fetchone()[0] == "YES"
    finally:
        await study.close_reports()
        study.store.close()


def test_readonly_breakdowns_default_to_complete_markets_and_latest_version(ledger):
    ingest(ledger, [row()])
    ledger.settle(META["slug"], {"slug": META["slug"], "winner": "YES"})
    assert analyze(ledger.directory)["rules"] == {}
    ledger.finish("run1", META["slug"], full_window=True, complete=True, counts={})
    report = analyze(ledger.directory)
    group = report["rules"]["all_candidates"]
    assert D(group["overall"]["settled_pnl"]) == 5
    assert group["overall"]["mean_outcome_minus_price"] == .5
    assert group["overall"]["market_cluster_normal_95_interval"] is None
    assert group["price"]["[0.5,0.6)"]["settled_markets"] == 1
    assert group["time"]["T15-60"]["settled_samples"] == 1
    assert group["anchor_relation"]["unknown"]["settled_samples"] == 1


def test_uncertainty_clusters_by_market_not_signal_count():
    rows = [{"slug": "same-market", "sample_pnl": "1", "status": "ASSUMED_FILL",
             "trade_pnl": "1", "quantity": "10", "cost": "5", "outcome": "YES",
             "winner": "YES", "price": ".5"} for _ in range(100)]
    report = summarize(rows)
    assert report["settled_samples"] == 100
    assert report["settled_markets"] == 1
    assert report["market_cluster_normal_95_interval"] is None


def test_skipped_research_samples_cannot_inflate_strategy_precision():
    fill = {"slug": "filled-market", "sample_pnl": "5", "status": "ASSUMED_FILL",
            "trade_pnl": "5", "quantity": "10", "cost": "5", "outcome": "YES",
            "winner": "YES", "price": ".5"}
    skipped = [{**fill, "slug": f"skipped-{i}", "status": "SKIPPED", "trade_pnl": None,
                "reason": "DIRECTION_ENTRY_LIMIT_REACHED", "sample_pnl": "-5", "winner": "NO"}
               for i in range(30)]
    report = summarize([fill]+skipped)
    assert report['settled_samples'] == 31
    assert report['settled_research_markets'] == 31
    assert report['settled_markets'] == 1
    assert report['mean_outcome_minus_price'] == .5
    assert report['market_cluster_normal_95_interval'] is None
    assert D(report['settled_pnl']) == 5
    assert D(report['all_sample_pnl']) == -145


@pytest.mark.parametrize("busy,guard_enabled,m1_guard_enabled,live_pressure,grace", (
    (0, True, True, 0, 5), (95, True, True, 0, 5), (95, False, True, 0, 0),
    (100, False, False, 100, 0),
))
async def test_market_loop_persists_features_without_wallet_or_m1_execution(
        tmp_path, monkeypatch, busy, guard_enabled, m1_guard_enabled, live_pressure, grace):
    def forbidden(*args, **kwargs):
        raise AssertionError("wallet configuration or live execution must not be used")

    monkeypatch.setattr("polymaker.config.Config.load", forbidden)
    monkeypatch.setattr("polymaker.m1_live.M1Market.__init__", forbidden)
    base, _ = m1_feed()
    epoch = int(time.time()//300)*300
    meta = replace(base.meta, slug=f"btc-updown-5m-{epoch}")

    class API:
        async def ticks(self, tokens):
            return {token: D(".01") for token in tokens}

        async def close(self):
            pass

    class Feed(SessionFeed):
        async def run(self):
            self.connect()
            evidence = row()
            for key in ("event", "available_monotonic", "observed_wall"):
                evidence.pop(key)
            self.m1.emit("M2_CANDIDATE", **evidence)
            self.m1.emit("M2_OBSERVATION", **evidence)
            await self.stop_event.wait()

    class Probe:
        live_pressure_pct = live_pressure

        def __call__(self):
            return busy

    study = M2Study(M2Config(min_free_disk_mib=256, host_cpu_guard_enabled=guard_enabled,
                             m1_cpu_guard_enabled=m1_guard_enabled,
                             cpu_busy_grace_sec=grace), tmp_path, api=API(), feed_factory=Feed,
                    resource_probe=Probe())
    now = time.monotonic()
    study.clock = (epoch+20, now, now)
    try:
        await study.market(meta, stop_at=now+.15)
        await study.close_reports()
        assert study.store.db.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
        assert study.store.db.execute("SELECT count(*) FROM samples").fetchone()[0] == 4
        assert study.store.db.execute("SELECT state FROM markets").fetchone()[0] == "INTERRUPTED"
        counts = json.loads(study.store.db.execute("SELECT counts FROM runs").fetchone()[0])
        assert not counts["resource_stop"]  # Brief CPU spikes do not discard a market.
        assert json.loads((tmp_path/"latest.json").read_text())["real_posts"] == 0
    finally:
        study.store.close()


def test_one_outcome_gap_does_not_erase_other_outcome_attack_anchor():
    feed, clock, _ = setup_feed()
    advance(feed, clock, .02)
    feed.apply(trade(feed, side="BUY", price=".52", size="10"))
    ap_id = feed.m1.state("1")["attacks"]["BUY"].id
    feed.m1.invalidate("M1_MIRROR_BOOK_UNALIGNED", "2")
    assert feed.m1.attack_anchors[ap_id]["ask"] == D(".52")
    for at in (.28, .53):
        advance(feed, clock, at)
        feed.apply(trade(feed, side="BUY", price=".52", size="10"))
    advance(feed, clock, .80)
    assert feed.m1.campaigns["1"]["attack_ask_anchor"] == D(".52")


async def test_resource_pressure_waits_without_opening_any_market_feed(tmp_path):
    class API:
        async def clock(self):
            raise AssertionError("waiting for CPU must not start market discovery")

        async def close(self):
            pass

    study = M2Study(M2Config(min_free_disk_mib=256), tmp_path, api=API(), resource_probe=lambda: 95)

    async def finish_wait(seconds):
        study.stop.set()

    study.wait = finish_wait
    await study.run(max_seconds=1)
    report = json.loads((tmp_path/"latest.json").read_text())
    assert report["runtime"]["state"] == "WAIT_RESOURCES"
    assert report["market_count"] == 0


def test_cpu_pressure_unknown_fails_closed(monkeypatch):
    from polymaker.m2_study import host_cpu_pressure

    monkeypatch.setattr("polymaker.m2_study.Path.read_text", lambda self: "some avg10=invalid")
    assert host_cpu_pressure() == 100


async def test_pressure_during_market_ends_only_m2_as_incomplete(tmp_path):
    base, _ = m1_feed()
    epoch = int(time.time()//300)*300
    meta = replace(base.meta, slug=f"btc-updown-5m-{epoch}")

    class API:
        async def ticks(self, tokens):
            return {token: D(".01") for token in tokens}

    study = M2Study(M2Config(min_free_disk_mib=256, cpu_busy_grace_sec=0), tmp_path,
                    api=API(), resource_probe=lambda: 95)
    now = time.monotonic()
    study.clock = (epoch+20, now, now)
    try:
        await study.market(meta, stop_at=now+1)
        row = study.store.db.execute("SELECT state,full_window FROM markets").fetchone()
        assert row["state"] == "INTERRUPTED" and not row["full_window"]
        counts = json.loads(study.store.db.execute("SELECT counts FROM runs").fetchone()[0])
        assert counts["resource_stop"]
        assert counts["invalid_reasons"]["M2_HOST_CPU_BUSY"] == 1
    finally:
        await study.close_reports()
        study.store.close()


def test_cpu_headroom_measures_idle_instead_of_global_scheduler_pressure():
    counters = iter(((100, 0, 0, 100, 0, 0, 0, 0), (160, 0, 0, 240, 0, 0, 0, 0)))
    clock = iter((0, 10))
    probe = CPUHeadroom(reader=lambda: next(counters), clock=lambda: next(clock),
                        protected_reader=lambda: 3)
    assert probe() == 100  # Need a real interval before admitting research.
    assert probe() == 30
    subject = SimpleNamespace(settings=M2Config(), resource_probe=probe)
    assert M2Study.resource_reason(subject, probe.busy_pct) is None


def test_m1_scheduler_pressure_blocks_research_even_with_idle_cpu():
    subject = SimpleNamespace(settings=M2Config(),
                              resource_probe=SimpleNamespace(live_pressure_pct=25))
    assert M2Study.resource_reason(subject, 50) == "M2_M1_CPU_PRESSURE"


@pytest.mark.parametrize("busy", (95, 100))
def test_disabled_host_guard_keeps_m1_specific_protection(busy):
    subject = SimpleNamespace(settings=M2Config(host_cpu_guard_enabled=False),
                              resource_probe=SimpleNamespace(live_pressure_pct=5))
    assert M2Study.resource_reason(subject, busy) is None
    subject.resource_probe.live_pressure_pct = 25
    assert M2Study.resource_reason(subject, busy) == "M2_M1_CPU_PRESSURE"


@pytest.mark.parametrize("m1_guard_enabled,live_pressure", ((True, 5), (False, 100)))
async def test_disabled_host_guard_allows_discovery_during_high_host_cpu(
        tmp_path, m1_guard_enabled, live_pressure):
    called = []

    class API:
        async def clock(self):
            called.append("clock")
            study.stop.set()
            now = time.monotonic()
            return int(time.time()//300)*300+250, now, now

        async def close(self):
            pass

    class Probe:
        live_pressure_pct = live_pressure

        def __call__(self):
            return 100

    study = M2Study(M2Config(min_free_disk_mib=256, host_cpu_guard_enabled=False,
                             m1_cpu_guard_enabled=m1_guard_enabled), tmp_path,
                    api=API(), resource_probe=Probe())
    await study.run(max_seconds=1)
    assert called == ["clock"]
    assert json.loads((tmp_path/"latest.json").read_text())["runtime"]["state"] == "WAIT_NEXT_MARKET"


def test_host_guard_setting_separates_research_versions():
    enabled, disabled = M2Config(), M2Config(host_cpu_guard_enabled=False)
    assert enabled.fingerprint != disabled.fingerprint
    assert enabled.signal == disabled.signal
    assert enabled.rules == disabled.rules


def test_disabling_both_cpu_guards_preserves_signal_and_risk_settings():
    before = M2Config(host_cpu_guard_enabled=False)
    after = before.model_copy(update={"m1_cpu_guard_enabled": False})
    assert before.fingerprint != after.fingerprint
    assert before.model_dump(exclude={"m1_cpu_guard_enabled"}) == after.model_dump(
        exclude={"m1_cpu_guard_enabled"})


@pytest.mark.parametrize("live_pressure", (None, 0, 25, 100))
def test_disabled_cpu_guards_never_pause_for_cpu_metrics(live_pressure):
    subject = SimpleNamespace(settings=M2Config(host_cpu_guard_enabled=False,
                                                m1_cpu_guard_enabled=False),
                              resource_probe=SimpleNamespace(live_pressure_pct=live_pressure))
    assert M2Study.resource_reason(subject, 100) is None


def test_cli_can_disable_host_guard_without_changing_m1_guard(monkeypatch):
    from polymaker.m2_study import main

    captured = []

    async def capture(settings, *args, **kwargs):
        captured.append(settings)

    monkeypatch.setattr("polymaker.m2_study.run", capture)
    monkeypatch.setattr("sys.argv", ["m2_study", "--disable-host-cpu-guard"])
    main()
    assert not captured[0].host_cpu_guard_enabled
    assert captured[0].m1_cpu_guard_enabled
    assert captured[0].max_m1_cpu_pressure_pct == 20


def test_cli_disables_both_cpu_guards(monkeypatch):
    from polymaker.m2_study import main

    captured = []

    async def capture(settings, *args, **kwargs):
        captured.append(settings)

    monkeypatch.setattr("polymaker.m2_study.run", capture)
    monkeypatch.setattr("sys.argv", ["m2_study", "--disable-cpu-guards"])
    main()
    assert not captured[0].host_cpu_guard_enabled
    assert not captured[0].m1_cpu_guard_enabled


async def test_runner_keeps_service_cpu_priority(monkeypatch, tmp_path):
    from polymaker.m2_study import run

    called = []

    def forbidden(*args):
        raise AssertionError("M2 must not lower its own CPU priority")

    class Study:
        def __init__(self, settings, directory, **kwargs):
            assert directory == tmp_path

        async def run(self, **kwargs):
            called.append(kwargs)

    monkeypatch.setattr("polymaker.m2_study.output_path", lambda value: tmp_path)
    monkeypatch.setattr("os.nice", forbidden)
    monkeypatch.setattr("polymaker.m2_study.M2Study", Study)
    await run(M2Config(host_cpu_guard_enabled=False, m1_cpu_guard_enabled=False),
              tmp_path, once=True)
    assert called == [{"once": True, "max_seconds": None, "max_markets": None}]


def test_failed_headroom_read_cannot_start_market():
    def fail():
        raise OSError("unavailable")

    probe = CPUHeadroom(reader=fail)
    assert probe() == 100 and probe.live_pressure_pct == 100


def test_steal_time_is_not_counted_as_available_cpu():
    counters = iter(((0,)*8, (0, 0, 0, 10, 0, 0, 0, 90)))
    clock = iter((0, 10))
    probe = CPUHeadroom(reader=lambda: next(counters), clock=lambda: next(clock),
                        protected_reader=lambda: 0)
    probe()
    assert probe() == 90
