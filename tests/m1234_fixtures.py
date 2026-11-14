"""Artificial raw-event review tape, with no injected fills or order outcomes."""

import hashlib
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal as D
from pathlib import Path

from polymaker.m1_live_config import M1LiveConfig
from polymaker.m2_config import M2Config
from polymaker.m1234_sim import INPUT_SCHEMA, MODEL
from test_m234_signal import queue_hit, shared
from test_rdp_sm_ba import depth, trade
from test_rdp_sm_m1_strategy import advance, hit


def fixture_events(*, m1_price=".05", m1_ask=".06", m1_trade_price=None,
                   m1_fill_sizes=("85", "100")):
    feed, clock, rows = shared(frequency=True)
    cfg = {
        "profiles": {
            "M1-virtual": M1LiveConfig().model_dump(mode="json"),
            "M1-live-mirror": M1LiveConfig(maximum_price=".09").model_dump(mode="json"),
        },
        "m234": M2Config(
            include_m3_m4=True,
            m4_frequency_study=True,
            host_cpu_guard_enabled=False,
            m1_cpu_guard_enabled=False,
        ).model_dump(mode="json"),
        "scenarios": {"trades-only": {}, "cancel-half": {"ahead_cancel_fraction": ".5"}},
        "seed": 1234,
    }
    cfg["implementation"] = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path("src/polymaker").rglob("*.py"))
    }
    definition = {
        "model": MODEL,
        "input_schema": INPUT_SCHEMA,
        "market": asdict(feed.meta),
        "official_clock": [feed.wall(), 0.0, 0.0],
        "ticks": {t: str(b.tick) for t, b in feed.books.items()},
        "settings": cfg,
        "artificial_fixture": True,
    }
    events = []

    def append(kind, **payload):
        events.append(
            {
                "schema": INPUT_SCHEMA,
                "seq": len(events) + 1,
                "kind": kind,
                "available_mono": clock.now,
                "available_wall": feed.wall(),
                "generation": feed.generation,
                **deepcopy(payload),
            }
        )

    append("INIT", definition=definition)
    append("CONNECT")
    append("FRAME", raw=rows, received_mono=clock.now, received_wall=feed.wall())
    original = feed.apply

    def capture(raw, **kwargs):
        append("FRAME", raw=raw, received_mono=clock.now, received_wall=feed.wall())
        result = original(raw, **kwargs)
        append("TIMER")
        return result

    feed.apply = capture
    # CA -> new BA; a later large direct print can consume its own FIFO queue.
    advance(feed, clock, 0.02)
    feed.apply(trade(feed, size="5"))
    advance(feed, clock, 0.05)
    feed.apply(trade(feed, side="BUY", price=".52", size="10"))
    advance(feed, clock, 0.10)
    feed.apply(trade(feed, size="5"))
    feed.apply(depth(feed, 75))
    feed.apply(depth(feed, 80))
    advance(feed, clock, 0.25)
    feed.apply(trade(feed, size="85"))
    # Separate causal AP_BUY -> two new refill rounds for M2.
    advance(feed, clock, 4.0)
    for at in (4.02, 4.28, 4.53):
        advance(feed, clock, at)
        feed.apply(trade(feed, side="BUY", price=".52", size="10"))
    advance(feed, clock, 4.80)
    hit(feed, clock, 4.9)
    hit(feed, clock, 5.5)
    advance(feed, clock, 6.1)
    # Independent frequency-v2 support/reference/weakness path.
    queue_hit(feed, clock, 8.02)
    queue_hit(feed, clock, 8.60, side="BID")
    queue_hit(feed, clock, 9.20, recovery=D(".2"))
    advance(feed, clock, 10.1)
    # Natural cal-16 M1 formation at a legal low price; two profiles share it.
    advance(feed, clock, 12.0)
    feed.apply(
        [
            {
                "event_type": "book",
                "market": feed.meta.condition_id,
                "asset_id": token,
                "timestamp": str(feed.wall() * 1000),
                "bids": [{"price": bid, "size": "80"}],
                "asks": [{"price": ask, "size": "80"}],
            }
            for token, bid, ask in (("1", m1_price, m1_ask),
                                    ("2", str(1 - D(m1_ask)), str(1 - D(m1_price))))
        ]
    )
    for at in (12.02, 12.28, 12.53, 12.80, 13.30, 13.60, 13.85):
        hit(feed, clock, at)
    advance(feed, clock, 14.825)
    advance(feed, clock, 15.05)
    feed.apply(trade(feed, price=m1_trade_price or m1_price, size=m1_fill_sizes[0]))
    advance(feed, clock, 15.3)
    feed.apply(trade(feed, price=m1_trade_price or m1_price, size=m1_fill_sizes[1]))
    advance(feed, clock, 16.0)
    append("GAP", reason="ARTIFICIAL_RESTART_GAP")
    append("CONNECT")
    append(
        "FRAME",
        raw=[{**r, "timestamp": str(feed.wall() * 1000)} for r in rows],
        received_mono=clock.now,
        received_wall=feed.wall(),
    )
    append("TIMER")
    clock.now = 281.0
    append("END", reason="ARTIFICIAL_T300_PLUS")
    raw = {
        "slug": feed.meta.slug,
        "conditionId": feed.meta.condition_id,
        "clobTokenIds": [feed.meta.yes.token_id, feed.meta.no.token_id],
        "outcomePrices": ["1", "0"],
        "closed": True,
        "resolutionStatus": "resolved",
    }
    append("SETTLEMENT", raw=raw)
    return events
