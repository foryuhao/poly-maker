from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from rich.console import Console

from polymaker.config import Config, Secrets
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.livetest import resolve_livetest_market, run_livetest


def _market(epoch: int, *, end: int | None = None, slug: str | None = None) -> MarketMeta:
    expiry = end if end is not None else epoch + 300
    return MarketMeta(
        condition_id="condition-" + str(epoch), question="BTC Up or Down?",
        slug=slug or f"btc-updown-5m-{epoch}",
        tokens=(TokenMeta("yes-" + str(epoch), "Up"), TokenMeta("no-" + str(epoch), "Down")),
        tick_size=0.01, neg_risk=False, min_order_size=5,
        rewards_min_size=0, rewards_max_spread=0, rewards_daily_rate=0,
        maker_fee_bps=0, taker_fee_bps=0, fees_enabled=False,
        end_date_iso=datetime.fromtimestamp(expiry, UTC).isoformat(), event_id=None,
    )


def _now() -> float:
    return 650.0


async def test_current_btc5m_selected_when_more_than_sixty_seconds_remain():
    seen = []
    current = _market(600)

    async def resolver(_cfg, slug):
        seen.append(slug)
        return current

    selected = await resolve_livetest_market(Config(), now=_now, resolver=resolver)
    assert selected is current
    assert seen == ["btc-updown-5m-600"]


async def test_near_expiry_current_selects_next_market():
    seen = []
    next_market = _market(300)

    async def resolver(_cfg, slug):
        seen.append(slug)
        return _market(0) if slug.endswith("-0") else next_market

    selected = await resolve_livetest_market(Config(), now=lambda: 250.0, resolver=resolver)
    assert selected is next_market
    assert seen == ["btc-updown-5m-0", "btc-updown-5m-300"]


class _Gateway:
    def __init__(self, book):
        self.book = book
        self.places = []
        self.address = "0x1234567890"

    async def connect(self): pass
    async def get_book(self, _token): return self.book
    async def balance_allowance(self): return "ok"
    async def place(self, quotes, meta):
        self.places.append((quotes, meta))
        return [SimpleNamespace(order_id="order-1")]
    async def open_orders(self): return []
    async def cancel(self, _ids): pass


def _cfg():
    return Config(secrets=Secrets(PK="pk", BROWSER_ADDRESS="0x" + "11" * 20))


async def test_invalid_token_book_aborts_without_place():
    gateway = _Gateway({})
    out = Console(record=True, width=120)
    result = await run_livetest(_cfg(), out, now=lambda: 650,
        resolver=lambda _cfg, _slug: _resolved(_market(600)), gateway_factory=lambda _cfg: gateway)
    assert result is False
    assert gateway.places == []
    assert "no order submitted" in out.export_text()


async def test_expired_market_cannot_reach_place():
    gateway = _Gateway({"best_bid": 0.40, "best_ask": 0.50})
    out = Console(record=True, width=120)
    result = await run_livetest(_cfg(), out, now=lambda: 650,
        resolver=lambda _cfg, _slug: _resolved(_market(600, end=649)), gateway_factory=lambda _cfg: gateway)
    assert result is False
    assert gateway.places == []


async def test_valid_active_market_reaches_existing_place_flow(monkeypatch):
    monkeypatch.setattr("polymaker.livetest.asyncio.sleep", _no_sleep)
    gateway = _Gateway({"best_bid": 0.40, "best_ask": 0.50})
    market = _market(600)
    result = await run_livetest(_cfg(), Console(record=True, width=120), now=lambda: 650,
        resolver=lambda _cfg, _slug: _resolved(market), gateway_factory=lambda _cfg: gateway)
    assert result is True
    assert len(gateway.places) == 1
    quotes, placed_meta = gateway.places[0]
    assert quotes[0].token_id == market.yes.token_id
    assert placed_meta is market


async def test_resolution_failure_does_not_use_catalog_or_other_market():
    gateway = _Gateway({"best_bid": 0.40, "best_ask": 0.50})
    async def fail(_cfg, _slug):
        raise LookupError("missing exact BTC5m market")
    result = await run_livetest(_cfg(), Console(record=True, width=120), now=_now,
        resolver=fail, gateway_factory=lambda _cfg: gateway)
    assert result is False
    assert gateway.places == []


async def _resolved(value):
    return value


async def _no_sleep(_seconds):
    return None
