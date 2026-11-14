import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

from polymaker.config import RDPObiConfig
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.m6_baseline.config import STRATEGY, M6Config
from polymaker.m6_baseline.control import StopControl
from polymaker.m6_baseline.execution import M6Market
from polymaker.m6_baseline.fill_model import POLICY
from polymaker.m6_baseline.journal import M6RiskJournal
from polymaker.m6_baseline.run import Executor
from polymaker.m6_baseline.signals import M6Feed
from polymaker.m6_baseline.state import M6Ledger
from polymaker.m6_baseline.types import Opportunity
from polymaker.m6_baseline.virtual import VirtualReads
from polymaker.rdp_sm_writer import AsyncRiskWriter

EPOCH = 1800000000


def feed_fixture(price=".10"):
    meta = MarketMeta(
        condition_id="0x" + "ab" * 32,
        question="Synthetic M6 baseline",
        slug=f"btc-updown-5m-{EPOCH}",
        tokens=(TokenMeta("1", "Up"), TokenMeta("2", "Down")),
        tick_size=0.01,
        neg_risk=False,
        min_order_size=5.0,
        rewards_min_size=0.0,
        rewards_max_spread=0.0,
        rewards_daily_rate=0.0,
        maker_fee_bps=0,
        taker_fee_bps=0,
        fees_enabled=False,
        end_date_iso=datetime.fromtimestamp(EPOCH + 300, UTC).isoformat(),
        event_id=None,
    )
    clock = SimpleNamespace(now=0.0)
    feed = M6Feed(
        meta, RDPObiConfig(), monotonic=lambda: clock.now, wall=lambda: EPOCH + 20 + clock.now
    )
    feed.verify_ticks({"1": D(".01"), "2": D(".01")})
    feed.connect()
    refresh(feed, price)
    return feed, clock


def frame(feed, price=".10", ask=None, token="1", size="100", stamp=None):
    bid = D(price)
    return {
        "event_type": "book",
        "market": feed.meta.condition_id,
        "asset_id": token,
        "timestamp": str((feed.wall() if stamp is None else stamp) * 1000),
        "bids": [{"price": str(bid), "size": size}],
        "asks": [{"price": str(D(ask) if ask else bid + D(".01")), "size": size}],
    }


def refresh(feed, price=".10"):
    feed.apply([frame(feed, price), frame(feed, str(D(".99") - D(price)), token="2")])


def candidate(feed, price=".10"):
    c = feed.research
    rounds = [
        c.fact(
            "ROUND",
            "1",
            c.now,
            c.now - 0.5,
            c.now,
            anchor=D(price),
            values={
                "qualified": True,
                "stable": True,
                "recovery": D(".7"),
                "u": D(".1"),
                "d": D(".1"),
            },
        )
        for _ in range(2)
    ]
    rf = c.fact(
        "RF_BID",
        "1",
        c.now + 8,
        c.now - 0.5,
        c.now,
        anchor=D(price),
        refs=[r.id for r in rounds],
        values={"rounds": rounds},
    )
    lw = c.fact("LW_ASK", "1", c.now + 0.75, c.now - 0.1, c.now, anchor=D(price) + D(".01"))
    feed.strategies.on_fact(rf)
    feed.strategies.on_fact(lw)
    c.publish()
    feed.m1.evaluate()
    assert feed.m1.selected is not None
    return feed.m1.selected


def opportunity(key="root", price=".10", outcome="YES", generation=1):
    return Opportunity(
        key, "1" if outcome == "YES" else "2", outcome, D(price), 0, 0.75, "RF", key, generation
    )


async def setup(tmp_path, price=".10"):
    feed, clock = feed_fixture(price)
    settings = M6Config()
    ident = dict(
        strategy=STRATEGY,
        mode="VIRTUAL",
        funder="0x" + "0" * 39 + "6",
        market_id=feed.meta.condition_id,
        settings_hash=settings.fingerprint,
    )
    book = M6Ledger(tmp_path / "ledger.sqlite3", ident)
    sid = "m6_virtual_" + uuid4().hex
    book.initialize_session(
        sid, settings.model_dump(mode="json"), asdict(feed.meta), baseline_fill_model=POLICY
    )
    reads = VirtualReads(feed, book, None)
    backing = M6RiskJournal(tmp_path / "journal", ident["funder"], session_id=sid)
    journal = AsyncRiskWriter(backing)
    executor = Executor(
        None,
        feed.meta.slug,
        sid,
        reads,
        journal,
        ledger=book,
        control=StopControl(tmp_path),
        monotonic=feed.monotonic,
    )
    executor.meta = feed.meta
    executor.official_time, executor.time_anchor = EPOCH + 20, 0
    executor.stop_event = asyncio.Event()
    feed.m1.record = lambda payload: (
        executor.record("sm_m6_signal", payload) or executor.journal.ready
    )
    market = M6Market(executor, settings, executor.stop_event, display=lambda _: None)
    await market.refresh_account()
    return market, executor, feed, clock


async def close(market, executor):
    await market.drain()
    await executor.journal.flush()
    executor.close()
    market.ledger.close()
