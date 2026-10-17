"""L01-L12: production CLI/M1/M2/M3, external MockTransport/time only.

No live endpoints or credentials: run with rdp_sm_m0_offline.py bootstrap.
"""

import json
import logging
from threading import Event

import httpx
import pytest
import structlog
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_logging import HumanRunLog
from test_rdp_sm_entry import cli_config
from test_rdp_sm_entry import env as entry_env
from test_rdp_sm_m1 import CID, DUMMY_KEY, SLUG, START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import current_orders, on_windows


@pytest.fixture
def env(monkeypatch, tmp_path):
    root = logging.getLogger()
    handlers, level, config = root.handlers[:], root.level, structlog.get_config().copy()
    try:
        yield from entry_env.__wrapped__(monkeypatch, tmp_path)
    finally:
        root.handlers[:], root.level = handlers, level
        structlog.configure(**config)


def invoke(env, monkeypatch, tmp_path, *options):
    v, cfg = env
    config = cli_config(monkeypatch, tmp_path, v, cfg)
    return CliRunner().invoke(app, ["rdp-sm-run", "--config-dir", str(config), *options])


def test_ws_preflight_failure_renders_original_cause():
    observer = HumanRunLog()
    try:
        lines = observer.render("preflight", {
            "status": "SESSION_BLOCKED",
            "checks": [{"name": "freshness", "outcome": "BLOCKED", "detail": "WS_DISCONNECTED"}],
            "diagnostics": [{"ws_check": True, "reason": "WS_DISCONNECTED", "generation": 2,
                             "sequence": 63, "failure_count": 1,
                             "last_failure": {"reason": "WS_CROSSED_BOOK", "error_type": "ValueError"}}],
        })
        text = "\n".join(lines)
        assert "WS Last Failure: WS_CROSSED_BOOK" in text
        assert "WS Check: BLOCKED" in text and "BOOK_SIDE_MISSING" not in text
    finally:
        observer.close()


def test_obi_fill_side_and_structured_cancel_ack_render_actual_facts():
    observer = HumanRunLog()
    quote = dict(outcome='YES', token_id='up', side='BUY', price=.26, size=5)
    observer.cancel_events = [
        {'result': {'canceled': ['order'], 'not_canceled': [], 'unknown': []}},
        {'result': {'canceled': [], 'not_canceled': [['order',
            "order can't be found - already canceled or matched"]], 'unknown': []}},
    ]
    try:
        lines = observer._window(dict(
            placement_results=[dict(quote=quote, post_started=True, outcome='ACK', order_id='order')],
            confirmed_fills=[dict(status='CONFIRMED', trade_id='fill', maker_order_id='order',
                                  token_id='up', price=.26, size=.01)],
            yes_inventory=.01, no_inventory=0, delta=.01, close_barrier='SAFE_TO_CONTINUE'))
        text = '\n'.join(lines)
        assert '0.01 | YES=0.01 NO=0' in text
        assert 'Cancel Status: ACK, ALREADY_CANCELED_OR_MATCHED' in text
        assert 'NOT_ACKNOWLEDGED' not in text
    finally:
        observer.close()


def test_l01_l04_l08_l09_l11_normal_rebalance_confirmed_summary(env, monkeypatch, tmp_path):
    v, cfg = env
    v.time = START + 15

    def fills(v, index):
        ids = current_orders(v)
        if index == 1:
            v.fill(ids["YES"], "first", 5)
        elif index == 2:
            v.fill(ids["NO"], "repair", 5)

    on_windows(v, fills, stop_after=3)
    output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 0, output.output
    r = json.loads(output.stdout)  # existing machine channel is still one JSON
    human = output.stderr
    (tmp_path / "human-cli-example.txt").write_text(human, encoding="utf-8")
    for text in ("Local Timestamp:", "BTC Up/Down 5m Market", SLUG, "Market Elapsed Seconds: 15",
                 "Trading Window: NORMAL 15 <= T < 120s | REBALANCE 15 <= T < 300s",
                 "Completion: T >= 300s | NORMAL waits for market end after T >= 120s",
                 "Remaining Trading Time: 285s",
                 "Available Capital: 100 USDC", "Preflight Status: SESSION_READY",
                 "Window 1 (closed)", "Trading State: NORMAL", "YES / UP Decision Book:",
                 "NO / DOWN Decision Book:", "Best Bid=0.49", "Bid Levels Count=1",
                 "Ask Levels Count=1", "Dynamic Tick=0.01", "Planned Quote: YES",
                 "Planned Quote: NO", "Actually Submitted Order: YES", "Order Submission Result=ACK",
                 "Maker/Post-only=YES", "Confirmed Fill Quantity (this window, newly observed): 5",
                 "Updated Inventory: YES=5 NO=0 | Updated Delta=5", "Capital Used (confirmed cost): 2.4",
                 "Cancel Status: ACK", "Reconciliation Status:", "=COMPLETE",
                 "FillWindow Duration (deadline - T0): 3s", "RDP-SM Exit Summary",
                 "Total Windows: 3 | NORMAL Windows: 2 | REBALANCE Windows: 1",
                 "Submitted Orders: 5 | Confirmed Fills: 2", "Completed Pairs: 5 | Residual Shares: 0",
                 "Maximum Capital Utilization", "Maximum Residual Exposure: NOT_AVAILABLE",
                 "Exposure Duration: NOT_AVAILABLE", "Close Barrier Result: SAFE_TO_CONTINUE"):
        assert text in human, (text, human)
    second = human.split("Window 2 (closed)")[1].split("Window 3 (closed)")[0]
    assert "Trading State: REBALANCE" in second and "Delta: 5" in second
    assert "Planned Quote: NO" in second and "Planned Quote: YES" not in second
    assert DUMMY_KEY not in output.output and cfg.secrets.browser_address not in human
    assert "HTTP Request:" not in human and "RealizedPnL" not in human
    assert r["confirmed_yes"] == r["confirmed_no"] == 0
    assert r["merged_pairs"] == 5 and "Merge Recovered: 5 USDC (verified)" in human
    rows = journal_records(v.executor.journal.path)
    assert {"sm_strategy_window", "sm_placement_intent", "sm_close_barrier", "sm_run_result"} <= {x["kind"] for x in rows}
    final = next(x["data"]["payload"] for x in rows if x["kind"] == "sm_run_result")
    assert final == r  # no human strings or redactions were written to RunResult
    strategy = next(x["data"]["payload"] for x in rows if x["kind"] == "sm_strategy_window")
    assert set(strategy) == {"window_id", "state", "delta", "elapsed_s", "quotes", "sizing"}
    assert strategy["sizing"]["configured_target_delta"] == str(cfg.rdp_sm.target_delta)


@pytest.mark.parametrize("token,side,label", [("1", "bids", "Bid"), ("2", "asks", "Ask")])
def test_l05_exact_rejected_book(env, monkeypatch, tmp_path, token, side, label):
    v, _ = env
    v.books[token][side] = []
    output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 2 and not v.posts
    human = output.stderr
    assert f"Exact Market: {SLUG} | Market ID: {CID}" in human
    assert f"Outcome: {'YES / UP' if token == '1' else 'NO / DOWN'} | Missing Side: {label}" in human
    assert f"Best {label}: MISSING" in human
    assert f"Actual Validation Failure: BOOK_SIDE_MISSING: {side} must be a non-empty list" in human
    assert ("Observed Bid Levels Count: 0" if side == "bids" else "Ask Levels Count: 0") in human
    assert f"Dynamic Tick: {float(v.ticks[token]):g}" in human


@pytest.mark.parametrize("failure", ["clean_market", "account_identity", "capital", "books"])
def test_l06_preflight_actual_reason(env, monkeypatch, tmp_path, failure):
    v, cfg = env
    if failure == "clean_market":
        v.positions = [{"token_id": "1", "current_size": 1, "avg_price": 0.5}]
    elif failure == "account_identity":
        cfg.wallet.signature_type = 0
        cfg.secrets.browser_address = "0x" + "bb" * 20
    elif failure == "capital":
        v.capital["balance"] = "0"
    else:
        # Discovery succeeds; the first M1 book read rejects its own snapshot.
        response = v.response
        calls = [0]

        def mutate(request):
            if request.url.path == "/book" and request.url.params["token_id"] == "1":
                calls[0] += 1
                if calls[0] == 2:
                    v.books["1"]["asks"] = []
            return response(request)

        from test_rdp_sm_entry import boundary
        client = boundary(monkeypatch, v, lambda request: mutate(request))
    try:
        output = invoke(env, monkeypatch, tmp_path)
        assert output.exit_code == 2 and not v.posts
        human = output.stderr
        assert "M1_SESSION_NOT_READY" in human and f"Failed Preflight Check: {failure}" in human
        assert "Failure Reason:" in human and "Trading Was Attempted: NO" in human
        expected = {"account_identity": "EOA_FUNDER_IDENTITY_MISMATCH", "books": "BOOK_SIDE_MISSING",
                    "clean_market": "zero target inventory", "capital": "basic venue minimum"}[failure]
        assert expected in human
        if failure == "books":
            assert "Missing Side: Ask" in human and "Best Ask: MISSING" in human
    finally:
        if failure == "books":
            client.close()


@pytest.mark.parametrize("status", ["MATCHED", "MINED"])
def test_l04_pending_never_displayed_as_confirmed(env, monkeypatch, tmp_path, status):
    v, _ = env
    on_windows(v, lambda v, _: v.fill(current_orders(v)["YES"], "pending", 5, status))
    output = invoke(env, monkeypatch, tmp_path)
    r = json.loads(output.stdout)
    assert output.exit_code == 2 and r["confirmed_fills"] == r["confirmed_yes"] == 0
    assert "Confirmed Fill Quantity (this window, newly observed): 0" in output.stderr
    assert "Close Barrier Result: BLOCKED" in output.stderr


def test_l07_unknown_batch_blocks_and_reports_both_submitted_orders(env, monkeypatch, tmp_path):
    v, _ = env
    v.replies = [httpx.ReadTimeout("offline unknown placement")]
    output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 2 and len(v.posts) == 2 and len(v.post_requests) == 1
    assert "Planned Quote: NO" in output.stderr
    assert "Actually Submitted Order: NO" in output.stderr
    assert "Order Submission: YES + NO together (one batch request)" in output.stderr
    assert "Order Submission Result=UNKNOWN" in output.stderr and "UNKNOWN order result | BLOCKED" in output.stderr
    assert "Capital Committed (M2): 4.795" in output.stderr


def test_l09_secrets_are_redacted_and_raw_responses_not_rendered(env, monkeypatch, tmp_path):
    v, cfg = env
    cfg.secrets.builder_secret = "dummy-builder-secret"
    on_windows(v, lambda *_: None, stop_after=1)
    response = v.response

    def add_fields(request):
        result = response(request)
        if request.method == "POST":
            raw = result.json()
            for item in raw if isinstance(raw, list) else [raw]:
                item["api_secret"] = "dummy-api-secret"
            return httpx.Response(200, json=raw, request=request)
        return result

    from test_rdp_sm_entry import boundary
    with boundary(monkeypatch, v, lambda request: add_fields(request)):
        output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 0, output.output
    assert "dummy-api-secret" not in output.output  # no credential in either channel
    placements = json.loads(output.stdout)["windows"][0]["result"]["placement_results"]
    assert all(item["api_secret"] == "[REDACTED]" for p in placements for item in p["raw_response"])
    log = HumanRunLog(secrets=(DUMMY_KEY, cfg.secrets.builder_secret))
    try:
        assert log._safe(f"{DUMMY_KEY} {cfg.secrets.builder_secret} {cfg.secrets.browser_address}") == (
            f"[REDACTED] [REDACTED] {cfg.secrets.browser_address[:8]}…{cfg.secrets.browser_address[-6:]}"
        )
    finally:
        log.close()


async def test_l10_slow_terminal_does_not_block_real_executor(env, monkeypatch, tmp_path):
    from polymaker.logging import configure

    configure(console=True)  # real INFO component logs participate in this test
    v, cfg = env
    v.time = START + 15
    on_windows(v, lambda *_: None, stop_after=1)
    baseline = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    baseline_calls, baseline_posts = list(v.calls), list(v.post_times)
    baseline_window = baseline.windows[0]["result"]
    # Reset only the external API/time fixture, then run the same production
    # components with logging on and a different empty temporary Journal path.
    for rows in (v.rows, v.calls, v.post_times, v.posts, v.cancels):
        rows.clear()
    v.executor, v.mono, v.time = None, 0, START + 15
    cfg.paths.journal_dir = str(tmp_path / "with-logging")
    on_windows(v, lambda *_: None, stop_after=1)
    entered, release = Event(), Event()
    # Stall while holding the real StreamHandler lock. All component console
    # records must use the same writer; otherwise FillTruth/Tracker would block.
    handler = logging.getLogger().handlers[0]
    original_emit = handler.emit

    def slow_terminal(record):
        entered.set()
        assert release.wait(10)
        original_emit(record)

    # Stall only the terminal boundary; the production renderer still runs.
    monkeypatch.setattr(handler, "emit", slow_terminal)
    log = HumanRunLog(console_handlers=logging.getLogger().handlers[:])
    log.emit("discovery", {"slug": SLUG, "elapsed_s": 15})
    assert entered.wait(2)
    try:
        result = await run_current_market(cfg, observer=log, sleep=v.sleep, monotonic=lambda: v.mono)
        assert result.execution_status == "COMPLETED" and v.executor.closed
        assert v.post_times == [15, 15]
        assert v.post_times == baseline_posts and v.calls == baseline_calls
        w = result.windows[0]["result"]
        assert all(w[field] == baseline_window[field] for field in (
            "t0_monotonic", "fill_deadline_monotonic", "close_monotonic", "close_barrier",
            "yes_inventory", "no_inventory", "capital_committed"))
        assert w["fill_deadline_monotonic"] - w["t0_monotonic"] == 3
        assert w["close_monotonic"] - w["t0_monotonic"] == 3
        cancel = [r["data"]["payload"] for r in journal_records(v.executor.journal.path) if r["kind"] == "sm_cancel_ack"]
        assert cancel and all(c["duration_s"] == 0 for c in cancel)
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    finally:
        release.set()
        assert log.close()


@pytest.mark.parametrize("option", ["--json-only", "--http-info"])
def test_http_diagnostics_and_json_only_switch(env, monkeypatch, tmp_path, option):
    v, _ = env
    on_windows(v, lambda *_: None, stop_after=1)
    output = invoke(env, monkeypatch, tmp_path, option)
    assert output.exit_code == 0 and json.loads(output.stdout)["total_windows"] == 1
    if option == "--http-info":
        assert "HTTP Request:" in output.stderr
    else:
        assert "Window 1 (closed)" not in output.stderr and "RDP-SM Exit Summary" not in output.stderr


def test_l05_m2_missing_book_blocks_whole_batch_and_exposes_fresh_failure(env, monkeypatch, tmp_path):
    v, _ = env
    response, reads = v.response, [0]

    def mutate(request):
        if (request.url.path == "/book" and request.url.params["token_id"] == "1"
                and v.executor is not None):
            reads[0] += 1
            if reads[0] == 2:  # M3 decision, then M2 final books for BOTH legs before POST.
                v.books["2"]["asks"] = []
        return response(request)

    from test_rdp_sm_entry import boundary
    with boundary(monkeypatch, v, lambda request: mutate(request)):
        output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 2 and not v.posts and not v.post_requests, output.output
    assert "Outcome: NO / DOWN | Missing Side: Ask" in output.stderr
    assert "Best Ask: MISSING" in output.stderr
    assert "Actually Submitted Order: YES" not in output.stderr
    assert "Actually Submitted Order: NO" not in output.stderr
    assert not v.cancels


def test_l02_decision_books_use_each_windows_snapshot(env, monkeypatch, tmp_path):
    v, _ = env

    def change(v, index):
        if index == 1:
            v.ticks["1"] = ".001"
            v.books["1"]["tick_size"] = ".001"
            v.books["1"]["bids"][0]["price"] = ".50"

    on_windows(v, change, stop_after=2)
    output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 0, output.output
    first, second = output.stderr.split("Window 1 (closed)")[1].split("Window 2 (closed)")
    assert "YES / UP Decision Book: Best Bid=0.49" in first
    assert "YES / UP Decision Book: Best Bid=0.5" in second
    assert "Planned Quote: YES | Side=BUY Price=0.499" in second
    assert "Dynamic Tick=0.001" in second


def test_http_warning_and_error_are_preserved(env, monkeypatch, tmp_path):
    v, _ = env
    response, logged = v.response, [False]

    def warnings(request):
        if not logged[0]:
            logging.getLogger("httpx").warning("offline HTTP diagnostic WARNING")
            logging.getLogger("httpx").error("offline HTTP diagnostic ERROR")
            logging.getLogger("httpx").warning({"event": "offline credential diagnostic",
                                               "api_secret": "dummy-secret-from-warning",
                                               "private_key": DUMMY_KEY, "wallet": v.funder})
            try:
                raise ValueError(f"private_key={DUMMY_KEY} api_secret=dummy-secret-from-exception")
            except ValueError:
                logging.getLogger("httpx").exception("offline HTTP exception remains visible")
            logged[0] = True
        return response(request)

    from test_rdp_sm_entry import boundary
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, lambda request: warnings(request)):
        output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 0
    assert "offline HTTP diagnostic WARNING" in output.stderr
    assert "offline HTTP diagnostic ERROR" in output.stderr
    assert "HTTP Request:" not in output.stderr
    assert "dummy-secret-from-warning" not in output.output
    assert DUMMY_KEY not in output.output and v.funder not in output.stderr
    assert "offline HTTP exception remains visible" in output.stderr
    assert "ValueError:" in output.stderr and "dummy-secret-from-exception" not in output.output


def test_l06_executor_open_repreflight_exposes_its_actual_failure(env, monkeypatch, tmp_path):
    v, _ = env
    response, calls = v.response, [0]

    def mutate(request):
        if request.url.path == "/balance-allowance":
            calls[0] += 1
            if calls[0] == 2:  # M2.open's independent M1 check, after M3 READY
                v.capital["balance"] = "0"
        return response(request)

    from test_rdp_sm_entry import boundary
    with boundary(monkeypatch, v, lambda request: mutate(request)):
        output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 2 and not v.posts
    assert "Preflight Status: SESSION_READY" in output.stderr
    assert "Preflight Status: SESSION_REJECTED" in output.stderr
    assert "M1_SESSION_NOT_READY" in output.stderr
    assert "Failed Preflight Check: capital" in output.stderr
    assert "Trading Was Attempted: NO" in output.stderr


def test_l05_unobserved_levels_are_not_zero_prices(env, monkeypatch, tmp_path):
    v, _ = env
    v.books["1"]["bids"] = None
    output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 2 and not v.posts
    assert "Observed Bid Levels Count: NOT_AVAILABLE" in output.stderr
    assert "Best Bid: NOT_AVAILABLE" in output.stderr
    assert "Best Bid: 0" not in output.stderr


def test_l08_late_normal_start_waits_until_market_end_with_observed_inventory(env, monkeypatch, tmp_path):
    v, _ = env
    v.time = START + 240
    output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 0 and not v.posts and not v.cancels
    assert "WAIT_FOR_MARKET_END_NORMAL" in output.stderr
    assert "MARKET_CUTOFF" in output.stderr
    assert "Current Inventory: YES=0 NO=0" in output.stderr
    assert "YES Inventory: 0 | NO Inventory: 0" in output.stderr
    assert json.loads(output.stdout)["stop_elapsed_s"] == 300
    assert "Total Windows: 0" in output.stderr and "Maximum Capital Utilization" in output.stderr


def test_network_pause_and_first_order_close_are_visible_in_human_log():
    observer = HumanRunLog()
    lines = observer.render('events', [
        ('sm_network_transition', {'previous': 'NORMAL', 'state': 'PAUSED',
                                   'reason': 'NETWORK_REPEATED_SLOW', 'pause_duration_sec': 2}),
        ('sm_network_slow_request', {'method': 'DELETE', 'duration_ms': 573}),
        ('sm_obi_close_requested', {'window_id': 'w1', 'reason': 'ORDER_DEADLINE'}),
    ])
    text = '\n'.join(lines)
    assert 'NORMAL -> PAUSED' in text and 'NETWORK_REPEATED_SLOW' in text
    assert '573' in text and 'WARNING' in text and 'ORDER_DEADLINE' in text
