from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
import respx

from polymaker.btc5m import (
    PERIOD_S,
    choose_epoch,
    current_epoch,
    next_epoch,
    resolve_btc5m_market,
    run_btc5m_paper,
    seconds_to_boundary,
    slug_for_epoch,
)
from polymaker.catalog.gamma import GammaClient
from polymaker.config import Config, PathsConfig


def test_epoch_floor_slug_and_next_boundary() -> None:
    assert current_epoch(601) == 600
    assert next_epoch(600) == 900
    assert slug_for_epoch(600) == "btc-updown-5m-600"
    assert seconds_to_boundary(600) == 300
    assert seconds_to_boundary(899) == 1


def test_boundary_and_shutdown_buffer_selection() -> None:
    assert choose_epoch(600, 15) == 600
    assert choose_epoch(884, 15) == 600
    assert choose_epoch(886, 15) == 900
    assert choose_epoch(899, 15) == 900


async def test_gamma_slug_lookup_is_exact_and_does_not_scan() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class Client:
        async def get(self, path: str, *, params: dict[str, Any]) -> httpx.Response:
            seen.append((path, params))
            request = httpx.Request("GET", "https://gamma-api.polymarket.com/markets")
            return httpx.Response(200, json=[{"slug": "wanted"}, {"slug": "other"}],
                                  request=request)

    gamma = GammaClient()
    await gamma.aclose()
    gamma._client = Client()  # type: ignore[assignment]
    assert await gamma.market_by_slug("wanted") == {"slug": "wanted"}
    assert seen == [("/markets", {"slug": "wanted", "limit": 1})]


async def test_gamma_slug_lookup_default_retains_legacy_failure_diagnostics(caplog, capsys) -> None:
    sentinel = "legacy-gamma-reason-sentinel"
    caplog.set_level(logging.INFO, logger="httpx")
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://gamma-api.polymarket.com/markets").mock(
            return_value=httpx.Response(
                503, extensions={"reason_phrase": sentinel.encode()},
            ),
        )
        async with GammaClient() as gamma:
            assert await gamma.market_by_slug("wanted") is None
    assert route.call_count == 1
    assert "HTTP Request" in caplog.text
    assert sentinel in caplog.text
    captured = capsys.readouterr()
    diagnostics = caplog.text + captured.out + captured.err
    assert "market_by_slug_failed" in diagnostics
    assert "Server error '503" in diagnostics


async def test_btc5m_resolver_uses_exact_gamma_and_caches_market(tmp_path, monkeypatch) -> None:
    raw = {
        "acceptingOrders": True,
        "conditionId": "condition-btc",
        "question": "Bitcoin Up or Down?",
        "slug": "btc-updown-5m-600",
        "clobTokenIds": '["yes", "no"]',
        "outcomes": '["Up", "Down"]',
        "endDate": "1970-01-01T00:15:00Z",
    }

    class FakeGamma:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def market_by_slug(self, slug: str):
            assert slug == raw["slug"]
            return raw

    monkeypatch.setattr("polymaker.btc5m.GammaClient", lambda _host: FakeGamma())
    cfg = Config(paths=PathsConfig(db=str(tmp_path / "catalog.db")))
    meta = await resolve_btc5m_market(cfg, raw["slug"])
    from polymaker.catalog.store import CatalogStore

    store = CatalogStore(cfg.paths.db)
    try:
        assert store.get_by_slug(raw["slug"]) == meta
    finally:
        store.close()


async def test_once_runs_one_session_and_shuts_engine_down(meta) -> None:
    now_value = 600.0
    sessions: list[tuple[str, bool, bool]] = []

    class FakeEngine:
        def __init__(self, cfg: Config, *, paper: bool) -> None:
            self.cfg = cfg
            self.paper = paper
            self.started = False

        async def start(self) -> None:
            self.started = True

        async def shutdown(self) -> None:
            sessions.append((self.cfg.markets[0].slug or "", self.paper, self.started))

    async def fake_sleep(seconds: float) -> None:
        nonlocal now_value
        now_value += seconds

    cfg = Config()
    await run_btc5m_paper(
        "unused",
        once=True,
        shutdown_buffer_s=15,
        now=lambda: now_value,
        sleep=fake_sleep,
        config_loader=lambda _: cfg,
        resolver=lambda _cfg, _slug: _resolved(meta),
        engine_factory=FakeEngine,
    )
    assert sessions == [("btc-updown-5m-600", True, True)]
    assert now_value == 600 + PERIOD_S - 15


async def _resolved(meta):
    return meta


async def test_unavailable_slug_is_retried_then_session_starts(meta) -> None:
    now_value = 600.0
    lookups: list[str] = []
    started: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        nonlocal now_value
        sleeps.append(seconds)
        now_value += seconds

    async def resolver(_cfg: Config, slug: str):
        lookups.append(slug)
        if len(lookups) == 1:
            raise LookupError("not published yet")
        return meta

    class FakeEngine:
        def __init__(self, cfg: Config, *, paper: bool) -> None:
            assert paper
            started.append(cfg.markets[0].slug or "")

        async def start(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

    await run_btc5m_paper(
        "unused", once=True, now=lambda: now_value, sleep=fake_sleep,
        config_loader=lambda _: Config(), resolver=resolver, engine_factory=FakeEngine,
    )
    assert lookups == ["btc-updown-5m-600", "btc-updown-5m-600"]
    assert started == ["btc-updown-5m-600"]
    assert sleeps[0] == 1.5


async def test_unavailable_slug_cutoff_advances_without_starting_stale_session(meta) -> None:
    now_value = 880.0
    lookups: list[str] = []
    started: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        nonlocal now_value
        assert seconds >= 0
        now_value += seconds

    async def resolver(_cfg: Config, slug: str):
        lookups.append(slug)
        if slug == "btc-updown-5m-600":
            raise LookupError("not published")
        return meta

    class FakeEngine:
        def __init__(self, cfg: Config, *, paper: bool) -> None:
            assert paper
            started.append(cfg.markets[0].slug or "")

        async def start(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

    await run_btc5m_paper(
        "unused", once=True, shutdown_buffer_s=15, now=lambda: now_value,
        sleep=fake_sleep, config_loader=lambda _: Config(), resolver=resolver,
        engine_factory=FakeEngine,
    )
    assert lookups[0] == "btc-updown-5m-600"
    assert lookups[-1] == "btc-updown-5m-900"
    assert started == ["btc-updown-5m-900"]


async def test_continuous_mode_survives_transient_lookup_failure(meta) -> None:
    now_value = 600.0
    lookups: list[str] = []
    started: list[str] = []

    class StopAfterRollover(Exception):
        pass

    async def fake_sleep(seconds: float) -> None:
        nonlocal now_value
        now_value += seconds

    async def resolver(_cfg: Config, slug: str):
        lookups.append(slug)
        if slug == "btc-updown-5m-900":
            raise StopAfterRollover
        if len(lookups) == 1:
            raise LookupError("temporary Gamma error")
        return meta

    class FakeEngine:
        def __init__(self, cfg: Config, *, paper: bool) -> None:
            assert paper
            started.append(cfg.markets[0].slug or "")

        async def start(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

    with pytest.raises(StopAfterRollover):
        await run_btc5m_paper(
            "unused", once=False, now=lambda: now_value, sleep=fake_sleep,
            config_loader=lambda _: Config(), resolver=resolver,
            engine_factory=FakeEngine,
        )
    assert lookups[:2] == ["btc-updown-5m-600", "btc-updown-5m-600"]
    assert lookups[-1] == "btc-updown-5m-900"
    assert started == ["btc-updown-5m-600"]


async def test_inside_buffer_waits_then_selects_next_market(meta) -> None:
    now_value = 890.0
    resolved: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        nonlocal now_value
        now_value += seconds

    async def resolver(_cfg: Config, slug: str):
        resolved.append(slug)
        return meta

    class FakeEngine:
        async def start(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

        def __init__(self, cfg: Config, *, paper: bool) -> None:
            assert cfg.markets[0].slug == "btc-updown-5m-900"
            assert paper

    await run_btc5m_paper(
        "unused", once=True, shutdown_buffer_s=15, now=lambda: now_value,
        sleep=fake_sleep, config_loader=lambda _: Config(), resolver=resolver,
        engine_factory=FakeEngine,
    )
    assert resolved == ["btc-updown-5m-900"]
