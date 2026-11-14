import json
import socket
import subprocess
import sys
from decimal import Decimal as D

import pytest
from helpers import EPOCH, candidate, close, feed_fixture, frame, opportunity, refresh, setup

from polymaker.m6_baseline.config import STRATEGY, M6Config
from polymaker.m6_baseline.control import StopControl
from polymaker.m6_baseline.fill_model import POLICY, TouchFillModel
from polymaker.m6_baseline.settlement import settle
from polymaker.m6_baseline.state import M6Ledger


def test_config_is_separate_and_check_has_no_network_or_wallet(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/m6.toml").write_text('minimum_price="0.02"\nmaximum_price="0.20"\n')
    (tmp_path / "config/m4.toml").write_text('minimum_price="0.50"\n')
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("NETWORK_FORBIDDEN"))
    cfg = M6Config.load(tmp_path)
    assert cfg.minimum_price == D(".02") and cfg.maximum_price == D(".20")
    assert M6Config.model_validate(cfg.model_dump(mode="json")).fingerprint == cfg.fingerprint
    assert not StopControl(tmp_path).inhibited()
    (tmp_path / "runtime/trading-control").mkdir(parents=True)
    (tmp_path / "runtime/trading-control/m4-live-stop.json").write_text("keep")
    assert not StopControl(tmp_path).inhibited()
    (tmp_path / "runtime/trading-control/m6-baseline-stop.json").write_text("stop")
    assert StopControl(tmp_path).inhibited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_price", ".01"),
        ("minimum_price", ".20"),
        ("maximum_price", ".21"),
        ("order_budget_usdc", 6),
        ("session_budget_usdc", 11),
        ("cutoff_sec", 241),
        ("quantity_step", ".01"),
    ],
)
def test_unapproved_contract_rejected(field, value):
    with pytest.raises(ValueError):
        M6Config.model_validate({field: value})


@pytest.mark.parametrize(
    "price,admitted", [(".01", False), (".02", True), (".19", True), (".20", False)]
)
async def test_original_anchor_band_and_virtual_ack_is_not_fill(
    tmp_path, monkeypatch, price, admitted
):
    monkeypatch.setattr(
        socket.socket, "connect", lambda *_: pytest.fail("PRIVATE_NETWORK_FORBIDDEN")
    )
    m, e, f, c = await setup(tmp_path, price)
    try:
        candidate(f, price)
        m.collect()
        assert bool(m.pending) == admitted
        if admitted:
            await m.submit(next(iter(m.pending.values())))
            row = m.ledger.rows()[0]
            assert row["state"] == "ACK" and row["posts"] == 1
            post = m.ledger.facts("virtual_post")[0]
            assert D(str(post["quote"]["price"])) == D(price)
            assert D(str(post["quote"]["size"])) % D(".1") == 0
            assert D(str(post["quote"]["size"])) * D(price) <= 5
            assert not m.ledger.facts("virtual_fill")
        else:
            assert not m.ledger.rows()
    finally:
        await close(m, e)


@pytest.mark.parametrize(
    "at,ask,filled",
    [(1, ".10", False), (1, ".09", True), (220, ".09", True), (220.001, ".09", False)],
)
async def test_post_touch_and_inclusive_t240(tmp_path, at, ask, filled):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        await m.submit(next(iter(m.pending.values())))
        c.now = at
        f.apply(frame(f, str(D(ask) - D(".01")), ask, size=".1"))
        e.reads.fill_model.poll()
        receipts = m.ledger.facts("virtual_fill")
        assert bool(receipts) == filled
        if filled:
            assert D(str(receipts[0]["quote"]["size"])) == 50
            assert D(str(receipts[0]["quote"]["price"])) == D(".10")
        if at > 220:
            assert len(m.ledger.facts("virtual_cancel")) == 1
    finally:
        await close(m, e)


async def test_gap_is_diagnostic_and_later_valid_touch_still_fills(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        await m.submit(next(iter(m.pending.values())))
        c.now = 1
        f.invalidate("OFFLINE_TEST_GAP")
        f.connect()
        f.apply(frame(f, ".08", ".09"))
        receipt = m.ledger.facts("virtual_fill")[0]
        obs = m.ledger.facts("virtual_observation")[0]
        assert receipt["quote"]["price"] == 0.10 and obs["gaps"]
    finally:
        await close(m, e)


async def test_stale_source_cannot_create_touch_or_candidate(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        await m.submit(next(iter(m.pending.values())))
        c.now = 1
        f.apply(frame(f, ".08", ".09", stamp=f.wall() - 1))
        assert not m.ledger.facts("virtual_fill")
        assert not f.m1.legal(f.books["1"], c.now)
        c.now = 221
        e.reads.fill_model.poll()
        obs = m.ledger.facts("virtual_observation")[0]
        assert obs["status"] == "NOT_TOUCHED" and obs["gaps"]
    finally:
        await close(m, e)


async def test_atomic_frame_keeps_valid_intermediate_touch(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        await m.submit(next(iter(m.pending.values())))
        c.now = 1
        f.apply([frame(f, ".08", ".09"), frame(f, ".10", ".11")])
        assert len(m.ledger.facts("virtual_fill")) == 1
        assert f.books["1"].best_ask == D(".11")
    finally:
        await close(m, e)


async def test_invalid_atomic_frame_discards_touch(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        await m.submit(next(iter(m.pending.values())))
        c.now = 1
        f.apply([frame(f, ".08", ".09"), {"event_type": "book", "market": "foreign"}])
        assert not m.ledger.facts("virtual_fill")
    finally:
        await close(m, e)


async def test_short_signal_latency_budget_and_cutoff_prevent_post(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        for at in [0.1, 0.2, 0.3, 0.4]:
            c.now = at
            refresh(f)
        await m.submit(m.pending.pop(next(iter(m.pending))))
        assert not m.ledger.facts("virtual_post")
        assert m.ledger.rows()[0]["reason"] == "M6_POST_LATENCY_BUDGET"
        c.now = 220
        refresh(f)
        candidate(f)
        m.collect()
        assert not m.pending and not m.entry_window_open()
    finally:
        await close(m, e)


async def test_crash_recovery_does_not_repost_or_invent_fill(tmp_path):
    m, e, f, c = await setup(tmp_path)
    candidate(f)
    m.collect()
    await m.submit(next(iter(m.pending.values())))
    ident, path = m.ledger.get("identity"), m.ledger.path
    settings, market, sid = (
        m.ledger.get("settings"),
        m.ledger.get("market"),
        m.ledger.get("session_id"),
    )
    await close(m, e)
    book = M6Ledger(
        path,
        {k: v for k, v in ident.items() if k not in {"limit", "order_budget", "prepost_policy"}},
    )
    try:
        book.initialize_session(sid, settings, market, baseline_fill_model=POLICY)
        f, c = feed_fixture()
        observer = TouchFillModel(f, book)
        assert not observer.pending and len(book.facts("virtual_post")) == 1
        assert not book.facts("virtual_fill")
        assert book.rows()[0]["posts"] == 1 and book.committed() == 5
        duplicate = book.observe(opportunity("another", ".10"), minimum=5, elapsed=30)
        assert not book.reserve(duplicate["id"], spendable=10)
        assert book.row(duplicate["id"])["reason"] == "M6_DIRECTION_OPPORTUNITY_LIMIT"
    finally:
        book.close()


async def test_official_settlement_both_cohorts_and_risk_retention(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        await m.submit(next(iter(m.pending.values())))
        c.now = 221
        e.reads.fill_model.poll()
        resolution = {
            "slug": f.meta.slug,
            "condition_id": f.meta.condition_id,
            "token_ids": {"YES": "1", "NO": "2"},
            "winner": "YES",
        }
        result = settle(m.ledger.path, m.ledger.get("identity"), resolution)
        assert D(result["modeled_pnl_usdc"]) == 0
        assert D(result["instant_fill_comparison"]["modeled_pnl_usdc"]) == 45
        assert m.ledger.committed() == 5 and not result["actual_fills_queried"]
    finally:
        await close(m, e)


def test_live_mode_and_foreign_ledger_rejected(tmp_path):
    cfg = M6Config()
    with pytest.raises(ValueError):
        M6Ledger(
            tmp_path / "live.sqlite3",
            dict(strategy=STRATEGY, mode="LIVE", settings_hash=cfg.fingerprint),
        )
    result = subprocess.run(
        [sys.executable, "-B", "-m", "polymaker.m6_baseline.run", "--live"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "unrecognized arguments" in result.stderr


def test_two_directions_share_gross_ten(tmp_path):
    cfg = M6Config()
    book = M6Ledger(
        tmp_path / "risk.sqlite3",
        dict(strategy=STRATEGY, mode="VIRTUAL", settings_hash=cfg.fingerprint),
    )
    try:
        for key, outcome in [("yes", "YES"), ("no", "NO")]:
            book.observe(opportunity(key, ".10", outcome), minimum=5, elapsed=20)
            assert book.reserve(key, spendable=10)
        assert book.committed() == 10
    finally:
        book.close()


async def test_cutoff_memory_release_and_repeated_close_are_safe(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        candidate(f)
        m.collect()
        c.now = 221
        await m.recycle_after_cutoff()
        assert m.memory_recycled and m.memory_recycle["status"] == "OK"
        assert f.runtime_released and not f.research.books and not f.research.registry.facts
        await f.close()
        assert not m.ledger.facts("virtual_post")
    finally:
        await close(m, e)


async def test_complete_runner_bootstrap_is_m6_only_and_network_free(tmp_path, monkeypatch):
    import time

    from polymaker.m6_baseline import run as runner

    (tmp_path / "config").mkdir()
    (tmp_path / "config/m6.toml").write_text('minimum_price="0.02"\nmaximum_price="0.20"\n')
    f, _ = feed_fixture()

    class Public:
        async def clock(self):
            stamp = time.monotonic()
            return EPOCH + 5, stamp, stamp

        async def market(self, slug):
            return f.meta

        async def ticks(self, tokens):
            return {t: D(".01") for t in tokens}

        async def close(self):
            pass

    class Market:
        def __init__(self, executor, settings, stop, **kwargs):
            self.e = executor
            assert executor.ledger.path.is_relative_to(tmp_path / "research_outputs/m6_virtual")
            assert settings.minimum_price == D(".02")

        async def run(self):
            assert self.e.ledger.get("identity")["mode"] == "VIRTUAL"
            self.e.ledger.set("complete", True)
            return True

    async def no_socket(self):
        await self.stop_event.wait()

    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("NETWORK_FORBIDDEN"))
    monkeypatch.setattr(runner, "VirtualPublicAPI", Public)
    monkeypatch.setattr(runner, "M6Market", Market)
    monkeypatch.setattr(runner.M6Feed, "run", no_socket)
    result = await runner.run(tmp_path, once=True)
    assert result == {"status": "COMPLETE", "real_orders": 0}
    root = tmp_path / "research_outputs/m6_virtual"
    scope = json.loads((root / "statistics-scope.json").read_text())
    assert scope["first_market_epoch"] == EPOCH and scope["strategy"] == STRATEGY
    assert not (tmp_path / "research_outputs/m4_virtual").exists()


@pytest.mark.parametrize("change_description", [False, True])
async def test_runner_resumes_saved_json_market_without_reset(tmp_path, monkeypatch, change_description):
    import time
    from dataclasses import replace

    from polymaker.m6_baseline import run as runner

    (tmp_path / "config").mkdir()
    (tmp_path / "config/m6.toml").write_text('minimum_price="0.02"\nmaximum_price="0.20"\n')
    f, _ = feed_fixture()
    meta = f.meta
    sessions = []

    class Public:
        async def clock(self):
            stamp = time.monotonic()
            return EPOCH + 5, stamp, stamp

        async def market(self, slug):
            return meta

        async def ticks(self, tokens):
            return {t: D(".01") for t in tokens}

        async def close(self):
            pass

    class Market:
        def __init__(self, executor, settings, stop, **kwargs):
            self.e = executor

        async def run(self):
            book = self.e.ledger
            sessions.append(book.get("session_id"))
            if len(sessions) == 1:
                book.set("recovery_test_marker", "preserve")
                return False
            assert book.get("recovery_test_marker") == "preserve"
            assert book.get("market")["question"] == f.meta.question
            book.set("complete", True)
            return True

    async def no_socket(self):
        await self.stop_event.wait()

    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("NETWORK_FORBIDDEN"))
    monkeypatch.setattr(runner, "VirtualPublicAPI", Public)
    monkeypatch.setattr(runner, "M6Market", Market)
    monkeypatch.setattr(runner.M6Feed, "run", no_socket)
    assert (await runner.run(tmp_path, once=True))["status"] == "RETAINED"
    if change_description:
        meta = replace(meta, question="Updated public description")
    assert (await runner.run(tmp_path, once=True))["status"] == "COMPLETE"
    assert len(sessions) == 2 and sessions[0] == sessions[1]


@pytest.mark.parametrize("source_time", [float("-inf"), float("inf"), float("nan")])
async def test_collect_waits_for_frozen_feed_quality_without_obi_snapshot(tmp_path, source_time):
    from dataclasses import replace

    m, e, f, c = await setup(tmp_path)
    try:
        for token in f.books:
            f.research.unavailable(token, "WAITING_FIRST_VALID_SNAPSHOT")
            f.books[token] = replace(f.books[token], source_time=source_time, snapshot_generation=0)
        m.collect()
        assert not m.pending and not m.ledger.rows()
        assert f.m1.failure is None
    finally:
        await close(m, e)
