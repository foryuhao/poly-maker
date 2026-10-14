"""Dynamic tick recovery through production M1/M2/M3 and real SDK admission."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from polymaker.rdp_sm import _book, _Issue, _market
from polymaker.rdp_sm_execution import RiskJournal
from polymaker.rdp_sm_recovery import retry_read
from test_rdp_sm_cli_logging import env as logging_env
from test_rdp_sm_cli_logging import invoke
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import ADDRESS, SLUG, START, Venue
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_recovery import Observer, run


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from logging_env.__wrapped__(monkeypatch, tmp_path)


def tick_fault(v, *, stage, recover):
    reads, failures, posts = [], [], []

    def fills(venue, index):
        venue.fill(current_orders(venue)["NO" if index == 1 else "YES"], f"fill-{index}", 5)
        if index == 1:
            venue.time = START + 210
            venue.books["1"]["bids"][0]["price"] = ".98"
            venue.books["1"]["asks"][0]["price"] = ".99"

    on_windows(v, fills, stop_after=2)

    def fault(request):
        if request.method == "POST":
            posts.append(v.time - START)
        if (request.url.path == "/book" and request.url.params["token_id"] == "1"
                and v.trades and v.time < START + 300):
            reads.append(v.time - START)
            eligible = stage == "decision" or len(reads) % 2 == 0
            fail = eligible and (not recover or len(failures) < 2)
            if fail:
                failures.append(v.time - START)
                raw = deepcopy(v.books["1"])
                raw["tick_size"] = ".001" if v.ticks["1"] != ".001" else ".01"
                raw["timestamp"] = str(v.time * 1000)
                return httpx.Response(200, request=request, json=raw)
            if failures and recover:
                # Both sources now agree on the supported new tick.
                v.ticks["1"] = v.books["1"]["tick_size"] = ".001"
        return None

    return fault, reads, failures, posts


@pytest.mark.parametrize("stage", ["decision", "admission"])
@pytest.mark.parametrize("recover", [True, False])
async def test_tick_mismatch_pauses_then_recovers_or_waits_to_market_end(env, monkeypatch, stage, recover):
    v, cfg = env
    v.time = START + 60
    fault, reads, failures, posts = tick_fault(v, stage=stage, recover=recover)
    observer = Observer()
    with boundary(monkeypatch, v, fault):
        result = await run(env, observer)
    assert failures and result.stop_elapsed_s >= 300
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert result.confirmed_fills == (2 if recover else 1)
    assert all(t < 300 for t in posts)
    assert all(r["status"] != "LIVE" for r in v.rows.values())
    assert not v.executor.processor.quarantined_fill_keys
    assert v.executor.committed() <= 20
    if recover:
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert result.merged_pairs == 5 and result.delta == 0
        assert [len(p) for p in v.post_requests] == [2, 1]
        assert min(posts[1:]) >= failures[-1]
        quote = next(a.quote for a in v.executor.attempts if a.quote.outcome == "YES" and a.order_id == "o-3")
        assert quote.price == .98 and quote.size == 5
        assert {e["status"] for e in observer.events} >= {"PAUSED", "RESUMED"}
    else:
        assert result.execution_status == "BLOCKED"
        assert result.stop_reason == "DYNAMIC_TICK_UNKNOWN_OR_CHANGED"
        assert len(v.posts) == 2 and result.confirmed_yes == 0 and result.confirmed_no == 5
        assert result.merged_pairs == 0 and v.executor.committed() > 0
    assert reads
    # Reopening expired history must not be broken by recovery diagnostics.
    path = Path(v.executor.journal.path)
    before = await asyncio.to_thread(path.read_bytes)
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS,
                          current_slug=f"btc-updown-5m-{START + 300}", official_time=START + 310)
    journal.close()
    assert await asyncio.to_thread(path.read_bytes) == before


@pytest.mark.parametrize("tick,book_tick", [(".01", ".001"), (".00001", ".00001"), ("bad", ".01")])
def test_tick_failures_stay_strict_and_carry_exact_rejected_values(tick, book_tick):
    v = Venue()
    meta, _ = _market(v.raw, SLUG)
    raw = deepcopy(v.books["1"])
    raw["tick_size"] = book_tick
    with pytest.raises(_Issue, match="DYNAMIC_TICK_UNKNOWN_OR_CHANGED") as caught:
        _book(raw, tick, "1", meta)
    assert retry_read(caught.value)
    context = caught.value.context
    assert context["outcome"] == "YES" and context["token_id"] == "1"
    assert str(context["official_tick"]) == (str(float(tick)) if tick != "bad" else tick)
    assert str(context["book_tick"]) == (str(float(book_tick)) if tick != "bad" else book_tick)
    assert "0.00001" not in context["supported_ticks"]


@pytest.mark.parametrize("stage", ["decision", "admission"])
def test_cli_shows_tick_values_and_preserves_json_and_journal(env, monkeypatch, tmp_path, stage):
    v, _ = env
    v.time = START + 60
    fault, _, failures, _ = tick_fault(v, stage=stage, recover=True)
    with boundary(monkeypatch, v, fault):
        output = invoke(env, monkeypatch, tmp_path)
    assert output.exit_code == 0, output.output
    result = json.loads(output.stdout)
    assert failures and result["execution_status"] == "COMPLETED"
    assert "Tick Check: BLOCKED | DYNAMIC_TICK_UNKNOWN_OR_CHANGED" in output.stderr
    assert "Official Tick=0.01 | Book Tick=0.001" in output.stderr
    assert "Outcome: YES | Token ID: 1" in output.stderr
    assert "Trading Recovery: RESUMED" in output.stderr
    assert journal_records(v.executor.journal.path)[-1]["data"]["payload"] == result


@pytest.mark.parametrize("recover", [True, False])
async def test_startup_tick_failure_pins_market_and_waits_for_recovery_or_end(env, monkeypatch, recover):
    v, _ = env
    v.time = START + 60
    slugs = []
    posts = []
    on_windows(v, lambda *_: None, stop_after=1)

    def fault(request):
        if request.method == "POST":
            posts.append(v.time - START)
        if request.url.path == "/markets":
            slugs.append(request.url.params["slug"])
        if (request.url.path == "/book" and request.url.params["token_id"] == "1"
                and (not recover or v.time < START + 90)):
            raw = deepcopy(v.books["1"])
            raw["tick_size"] = ".001"
            raw["timestamp"] = str(v.time * 1000)
            return httpx.Response(200, request=request, json=raw)
        return None

    class StartupObserver(Observer):
        def emit(self, kind, payload):
            super().emit(kind, payload)
            if kind == "preflight":
                self.preflight = deepcopy(payload)

    observer = StartupObserver()
    with boundary(monkeypatch, v, fault):
        result = await run(env, observer)
    assert result.stop_elapsed_s >= 300 and set(slugs) == {SLUG}
    assert any((e.get("context") or {}).get("tick_check") for e in observer.events)
    if recover:
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert len(v.posts) == 2 and min(posts) >= 90
    else:
        assert result.execution_status == "BLOCKED" and not v.posts
        assert result.stop_reason == "M1_SESSION_NOT_READY"
        assert any(c["detail"] == "DYNAMIC_TICK_UNKNOWN_OR_CHANGED" for c in observer.preflight["checks"])
