import json
from decimal import Decimal as D
from pathlib import Path

from maker_signals.model import Book


def contract():
    return json.loads((Path.cwd() / "config/m1223-input.json").read_text())


def book():
    return Book(
        "token",
        bids={D(".4"): D(10)},
        asks={D(".42"): D(10)},
        tick=D(".01"),
        generation=1,
        received=0.0,
        source=0.0,
        complete=True,
        tick_verified=True,
    )


def test_explicit_book_age_contract_matches_existing_quality_guard():
    limit = contract()["book_freshness_max_ms"] / 1000
    b = book()
    assert b.legal(limit, limit, 1)
    assert not b.legal(limit + 0.00001, limit + 0.00001, 1)


def test_explicit_source_age_and_future_tolerance_are_not_frequency_knobs():
    c = contract()
    b = book()
    limit = c["source_age_max_ms"] / 1000
    assert b.legal(0, limit, 1)
    assert not b.legal(0, limit + 0.00001, 1)
    b.source = c["source_future_tolerance_ms"] / 1000
    assert b.legal(0, 0, 1)
    b.source += 0.00001
    assert not b.legal(0, 0, 1)
    assert c["quality_gates_relaxed_for_frequency"] is False
    assert c["shared_exchange_sequence_proven"] is False
