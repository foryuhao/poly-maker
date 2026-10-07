from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from typer.testing import CliRunner

from polymaker.btc5m import PERIOD_S
from polymaker.cli import app
from polymaker.config import Config, PathsConfig
from polymaker.rdp_live import RDPBootstrapError, bootstrap_rdp_market

EPOCH = 2_000_000_100
SLUG = f"btc-updown-5m-{EPOCH}"
END_EPOCH = EPOCH + PERIOD_S
END_DATE = datetime.fromtimestamp(END_EPOCH, UTC).isoformat().replace("+00:00", "Z")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def raw_market(**changes):
    raw = {
        "acceptingOrders": True,
        "closed": False,
        "negRisk": False,
        "conditionId": "condition-btc5m",
        "question": "Bitcoin Up or Down?",
        "slug": SLUG,
        "clobTokenIds": '["token-up", "token-down"]',
        "outcomes": '["Up", "Down"]',
        "endDate": END_DATE,
        # These parser defaults are intentionally distinct from CLOB values.
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
    }
    raw.update(changes)
    return raw


def raw_book(token_id: str, **changes):
    body = {
        "market": "condition-btc5m",
        "asset_id": token_id,
        "tick_size": "0.005" if token_id == "token-up" else "0.001",
        "min_order_size": "12" if token_id == "token-up" else "4",
        "neg_risk": False,
        "bids": [],
        "asks": [],
    }
    body.update(changes)
    return body


def apply_book_changes(token_id: str, changes):
    body = raw_book(token_id)
    for key, value in changes.items():
        if value == "__MISSING__":
            body.pop(key, None)
        else:
            body[key] = value
    return body


def install_mocks(router, raw=None, *, books=None):
    market_raw = raw if raw is not None else raw_market()
    book_rows = books or {
        "token-up": raw_book("token-up"),
        "token-down": raw_book("token-down"),
    }
    gamma = router.get(f"{GAMMA}/markets").mock(
        return_value=httpx.Response(200, json=[market_raw]))

    def fetch_book(request):
        token_id = request.url.params["token_id"]
        return httpx.Response(200, json=book_rows[token_id])

    book = router.get(f"{CLOB}/book").mock(side_effect=fetch_book)
    return gamma, book


def apply_market_changes(changes):
    raw = raw_market()
    for key, value in changes.items():
        if value == "__MISSING__":
            raw.pop(key, None)
        else:
            raw[key] = value
    return raw


def test_slug_parser_requires_canonical_aligned_positive_epoch():
    from polymaker.rdp_live import _parse_slug

    assert _parse_slug(SLUG) == (EPOCH, END_EPOCH)
    for slug in (
        "btc-updown-5m-0", "btc-updown-5m-600x", "btc-updown-5m-0600",
        "btc-updown-5m-601", "btc-downup-5m-600",
    ):
        with pytest.raises(RDPBootstrapError) as caught:
            _parse_slug(slug)
        assert caught.value.category == "INVALID_MARKET_SLUG"


@pytest.mark.asyncio
async def test_bootstrap_reads_exact_market_and_both_books_get_only():
    with respx.mock(assert_all_called=True) as router:
        gamma, books = install_mocks(router)
        result = await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)

    assert result.market.slug == SLUG
    assert result.market.condition_id == "condition-btc5m"
    assert (result.epoch, result.end_epoch) == (EPOCH, END_EPOCH)
    assert [(item.outcome, item.token_id, item.tick_size, item.min_order_size)
            for item in result.token_metadata] == [
        ("Up", "token-up", .005, 12.0),
        ("Down", "token-down", .001, 4.0),
    ]
    assert gamma.call_count == 1
    assert gamma.calls[0].request.method == "GET"
    assert dict(gamma.calls[0].request.url.params) == {"slug": SLUG, "limit": "1"}
    assert books.call_count == 2
    assert [call.request.method for call in books.calls] == ["GET", "GET"]
    assert [call.request.url.params["token_id"] for call in books.calls] == [
        "token-up", "token-down"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw", "category"), [
    ({"closed": "__MISSING__"}, "MARKET_NOT_TRADABLE"),
    ({"acceptingOrders": False}, "MARKET_NOT_TRADABLE"),
    ({"acceptingOrders": "__MISSING__"}, "MARKET_NOT_TRADABLE"),
    ({"closed": True}, "MARKET_NOT_TRADABLE"),
    ({"closed": None}, "MARKET_NOT_TRADABLE"),
    ({"negRisk": "__MISSING__"}, "INVALID_GAMMA_MARKET"),
    ({"negRisk": "false"}, "INVALID_GAMMA_MARKET"),
    # GammaClient filters nonmatching slugs from its exact lookup response.
    ({"slug": "btc-updown-5m-2000000400"}, "GAMMA_LOOKUP_FAILED"),
    ({"conditionId": ""}, "INVALID_GAMMA_MARKET"),
    ({"clobTokenIds": '["token-up", "token-up"]'}, "INVALID_GAMMA_MARKET"),
    ({"outcomes": '["Up", "Yes"]'}, "INVALID_OUTCOME_MAPPING"),
    ({"outcomes": '["Down", "Up"]'}, "INVALID_OUTCOME_MAPPING"),
    ({"endDate": "2033-05-18T03:40:00"}, "INVALID_END_TIME"),
    ({"endDate": "2033-05-18T03:45:00Z"}, "INVALID_END_TIME"),
])
async def test_bootstrap_rejects_invalid_raw_gamma_metadata(raw, category):
    with respx.mock(assert_all_called=False) as router:
        gamma, books = install_mocks(router, apply_market_changes(raw))
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == category
    assert gamma.call_count == 1 and books.call_count == 0  # no book read after bad Gamma


@pytest.mark.asyncio
@pytest.mark.parametrize(("book_change", "category"), [
    ({"market": "foreign-condition"}, "BOOK_IDENTITY_MISMATCH"),
    ({"asset_id": "foreign-token"}, "BOOK_IDENTITY_MISMATCH"),
    ({"tick_size": None}, "INVALID_BOOK_METADATA"),
    ({"tick_size": "__MISSING__"}, "INVALID_BOOK_METADATA"),
    ({"tick_size": True}, "INVALID_BOOK_METADATA"),
    ({"tick_size": "NaN"}, "INVALID_BOOK_METADATA"),
    ({"tick_size": "Infinity"}, "INVALID_BOOK_METADATA"),
    ({"tick_size": "0"}, "INVALID_BOOK_METADATA"),
    ({"tick_size": "1"}, "INVALID_BOOK_METADATA"),
    ({"tick_size": "0.999999999999999999"}, "INVALID_BOOK_METADATA"),
    ({"min_order_size": "-1"}, "INVALID_BOOK_METADATA"),
    ({"min_order_size": "5.0000000000000001"}, "INVALID_BOOK_METADATA"),
    ({"min_order_size": "__MISSING__"}, "INVALID_BOOK_METADATA"),
    ({"min_order_size": False}, "INVALID_BOOK_METADATA"),
    ({"neg_risk": "false"}, "INVALID_BOOK_METADATA"),
    ({"neg_risk": True}, "INVALID_BOOK_METADATA"),
])
async def test_bootstrap_rejects_invalid_clob_book_metadata(book_change, category):
    books = {"token-up": apply_book_changes("token-up", book_change),
             "token-down": raw_book("token-down")}
    with respx.mock(assert_all_called=True) as router:
        install_mocks(router, books=books)
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == category


@pytest.mark.asyncio
async def test_bootstrap_preserves_json_numeric_precision_before_validation():
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{GAMMA}/markets").mock(
            return_value=httpx.Response(200, json=[raw_market()]))

        def fetch_book(request):
            if request.url.params["token_id"] == "token-up":
                body = (b'{"market":"condition-btc5m","asset_id":"token-up",'
                        b'"tick_size":0.005,"min_order_size":5.0000000000000001,'
                        b'"neg_risk":false}')
                return httpx.Response(200, content=body,
                                      headers={"content-type": "application/json"})
            return httpx.Response(200, json=raw_book("token-down"))

        books = router.get(f"{CLOB}/book").mock(side_effect=fetch_book)
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == "INVALID_BOOK_METADATA"
    assert books.call_count == 1


@pytest.mark.asyncio
async def test_bootstrap_rejects_non_mapping_clob_book():
    books = {"token-up": [], "token-down": raw_book("token-down")}
    with respx.mock(assert_all_called=False) as router:
        _gamma, route = install_mocks(router, books=books)
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == "INVALID_BOOK_METADATA"
    assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "host", "category"), [
    ("gamma_host", "https://gamma-api.polymarket.com.evil.test", "UNAPPROVED_GAMMA_ORIGIN"),
    ("gamma_host", "http://gamma-api.polymarket.com", "UNAPPROVED_GAMMA_ORIGIN"),
    ("clob_host", "https://clob.polymarket.com.evil.test", "UNAPPROVED_CLOB_ORIGIN"),
    ("clob_host", "https://clob.polymarket.com/unofficial", "UNAPPROVED_CLOB_ORIGIN"),
])
async def test_bootstrap_rejects_unapproved_origin_before_requests(field, host, category):
    with respx.mock() as router:
        cfg = Config()
        setattr(cfg.wallet, field, host)
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(cfg, SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == category
    assert len(router.calls) == 0


@pytest.mark.asyncio
async def test_bootstrap_uses_validated_origin_snapshots_after_config_mutation(monkeypatch):
    cfg = Config()
    evil_clob = "https://unapproved.example"

    async def mutate_config_and_return(self, slug):
        assert slug == SLUG
        cfg.wallet.gamma_host = "https://unapproved.example"
        cfg.wallet.clob_host = evil_clob
        return raw_market()

    monkeypatch.setattr("polymaker.rdp_live.GammaClient.market_by_slug",
                        mutate_config_and_return)
    with respx.mock(assert_all_called=False) as router:
        official = router.get(f"{CLOB}/book").mock(
            side_effect=lambda request: httpx.Response(
                200, json=raw_book(request.url.params["token_id"])))
        unapproved = router.get(f"{evil_clob}/book").mock(
            side_effect=lambda request: httpx.Response(
                200, json=raw_book(request.url.params["token_id"])))
        result = await bootstrap_rdp_market(cfg, SLUG, now=lambda: EPOCH + 1)
    assert result.market.slug == SLUG
    assert official.call_count == 2
    assert unapproved.call_count == 0


@pytest.mark.asyncio
async def test_bootstrap_rejects_bad_slug_before_requests():
    with respx.mock() as router, pytest.raises(RDPBootstrapError) as caught:
        await bootstrap_rdp_market(Config(), "btc-updown-5m-600x",
                                   now=lambda: EPOCH + 1)
    assert caught.value.category == "INVALID_MARKET_SLUG"
    assert len(router.calls) == 0


@pytest.mark.asyncio
async def test_bootstrap_rejects_already_ended_interval_before_gamma_request():
    with respx.mock() as router, pytest.raises(RDPBootstrapError) as caught:
        await bootstrap_rdp_market(Config(), SLUG, now=lambda: END_EPOCH)
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert len(router.calls) == 0


@pytest.mark.asyncio
async def test_bootstrap_rechecks_expiry_after_metadata_reads():
    times = iter((EPOCH + 1, EPOCH + 2, END_EPOCH))
    with respx.mock(assert_all_called=True) as router:
        _gamma, books = install_mocks(router)
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: next(times))
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert books.call_count == 2


@pytest.mark.asyncio
async def test_bootstrap_handles_missing_exact_market_without_scanning():
    with respx.mock(assert_all_called=False) as router:
        gamma = router.get(f"{GAMMA}/markets").mock(
            return_value=httpx.Response(200, json=[raw_market(slug="another-market")]))
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == "GAMMA_LOOKUP_FAILED"
    assert gamma.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(503, json={"error": "unavailable"}),
    httpx.Response(200, content=b"not-json"),
])
async def test_bootstrap_fails_closed_on_gamma_http_or_json_errors(response):
    with respx.mock(assert_all_called=False) as router:
        gamma = router.get(f"{GAMMA}/markets").mock(return_value=response)
        books = router.get(f"{CLOB}/book")
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == "GAMMA_LOOKUP_FAILED"
    assert gamma.call_count == 1 and books.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"unexpected": "object"}, [None]])
async def test_bootstrap_rejects_malformed_gamma_envelopes(payload):
    with respx.mock(assert_all_called=False) as router:
        gamma = router.get(f"{GAMMA}/markets").mock(
            return_value=httpx.Response(200, json=payload))
        books = router.get(f"{CLOB}/book")
        with pytest.raises(RDPBootstrapError) as caught:
            await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
    assert caught.value.category == "INVALID_GAMMA_RESPONSE"
    assert gamma.call_count == 1 and books.call_count == 0


@pytest.mark.asyncio
async def test_bootstrap_classifies_book_http_timeout_and_malformed_json():
    for failure in (
        httpx.ReadTimeout("timeout"),
        httpx.Response(503, json={"error": "unavailable"}),
        httpx.Response(200, content=b"not-json"),
    ):
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{GAMMA}/markets").mock(
                return_value=httpx.Response(200, json=[raw_market()]))
            route = router.get(f"{CLOB}/book")
            if isinstance(failure, Exception):
                route.mock(side_effect=failure)
            else:
                route.mock(return_value=failure)
            with pytest.raises(RDPBootstrapError) as caught:
                await bootstrap_rdp_market(Config(), SLUG, now=lambda: EPOCH + 1)
        assert caught.value.category == "CLOB_BOOK_READ_FAILED"


def test_rdp_live_help_is_explicitly_read_only():
    result = CliRunner().invoke(app, ["rdp-live", "--help"])
    assert result.exit_code == 0
    assert "--preflight-only" in result.stdout
    assert "read-only" in result.stdout.lower() or "never trades" in result.stdout.lower()


def test_rdp_live_cli_success_persists_structured_evidence_and_never_trades(
    tmp_path, monkeypatch,
):
    from polymaker.config import Config, PathsConfig
    from polymaker.engine import Engine
    from polymaker.execution.gateway import ExecutionGateway

    cfg = Config(paths=PathsConfig(log_dir=str(tmp_path / "logs")))
    monkeypatch.setattr(Config, "load", lambda _config_dir: cfg)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only preflight invoked a trading component")
    monkeypatch.setattr(Engine, "__init__", forbidden)
    monkeypatch.setattr(ExecutionGateway, "connect", forbidden)
    monkeypatch.setattr(ExecutionGateway, "place", forbidden)
    monkeypatch.setattr(ExecutionGateway, "cancel", forbidden)

    from polymaker.marketdata import service as marketdata_service

    class FakeWS:
        def __init__(self):
            self.frames = [
                {"event_type": "book", "market": "condition-btc5m", "asset_id": token,
                 "bids": [{"price": "0.4", "size": "3"}],
                 "asks": [{"price": "0.6", "size": "4"}]}
                for token in ("token-up", "token-down")
            ]
            self.sent = []
            self.hold = asyncio.Event()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, value):
            self.sent.append(json.loads(value))

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.frames:
                return json.dumps(self.frames.pop(0))
            await self.hold.wait()
            raise StopAsyncIteration

    socket = FakeWS()
    monkeypatch.setattr(marketdata_service.websockets, "connect", lambda *_a, **_k: socket)

    with respx.mock(assert_all_called=True) as router:
        install_mocks(router)
        result = CliRunner().invoke(app, ["rdp-live", "--market", SLUG,
                                          "--preflight-only"])
    assert result.exit_code == 0, result.stdout
    assert "read-only" in result.stdout.lower()
    events = [json.loads(line) for line in
              (tmp_path / "logs" / "rdp-live-preflight.jsonl").read_text().splitlines()]
    bootstrap_event = next(event for event in events if event["event"] == "rdp_bootstrap_result")
    feed_event = next(event for event in events if event["event"] == "rdp_feed_readiness")
    assert bootstrap_event["result"] == "SUCCESS"
    assert feed_event["result"] == "SUCCESS"
    assert feed_event["connection_generation"] == 1
    assert feed_event["real_money_trading_performed"] is False
    assert socket.sent == [{"assets_ids": ["token-up", "token-down"], "type": "market"}]


def test_rdp_live_cli_requires_preflight_before_network_and_records_failure(tmp_path, monkeypatch):
    cfg = Config(paths=PathsConfig(log_dir=str(tmp_path / "logs")))
    monkeypatch.setattr(Config, "load", lambda _config_dir: cfg)
    with respx.mock() as router:
        result = CliRunner().invoke(app, ["rdp-live", "--market", SLUG])
    assert result.exit_code == 2
    assert len(router.calls) == 0
    lines = (tmp_path / "logs" / "rdp-live-preflight.jsonl").read_text().splitlines()
    event = json.loads(lines[-1])
    assert event["category"] == "PREFLIGHT_REQUIRED"
    assert event["real_money_trading_performed"] is False


def test_rdp_live_cli_bootstrap_failure_persists_category_and_exit_code(tmp_path, monkeypatch):
    cfg = Config(paths=PathsConfig(log_dir=str(tmp_path / "logs")))
    monkeypatch.setattr(Config, "load", lambda _config_dir: cfg)
    with respx.mock() as router:
        result = CliRunner().invoke(app, ["rdp-live", "--market", "invalid-slug",
                                          "--preflight-only"])
    assert result.exit_code == 1
    assert len(router.calls) == 0
    lines = (tmp_path / "logs" / "rdp-live-preflight.jsonl").read_text().splitlines()
    event = json.loads(lines[-1])
    assert event["result"] == "FAILURE"
    assert event["category"] == "INVALID_MARKET_SLUG"
    assert event["real_money_trading_performed"] is False


def test_rdp_live_cli_feed_failure_persists_separate_readiness_failure(tmp_path, monkeypatch):
    from polymaker.rdp_marketdata import RDPFeedError

    cfg = Config(paths=PathsConfig(log_dir=str(tmp_path / "logs")))
    monkeypatch.setattr(Config, "load", lambda _config_dir: cfg)

    async def fail_feed(*_args, **_kwargs):
        raise RDPFeedError("FEED_STARTUP_TIMEOUT", "test timeout")

    monkeypatch.setattr("polymaker.rdp_marketdata.await_rdp_feed", fail_feed)
    with respx.mock(assert_all_called=True) as router:
        install_mocks(router)
        result = CliRunner().invoke(app, ["rdp-live", "--market", SLUG, "--preflight-only"])
    assert result.exit_code == 1
    events = [json.loads(line) for line in
              (tmp_path / "logs" / "rdp-live-preflight.jsonl").read_text().splitlines()]
    assert any(event["event"] == "rdp_bootstrap_result" and event["result"] == "SUCCESS"
               for event in events)
    feed = next(event for event in events if event["event"] == "rdp_feed_readiness")
    assert feed["result"] == "FAILURE"
    assert feed["category"] == "FEED_STARTUP_TIMEOUT"
    assert feed["real_money_trading_performed"] is False
