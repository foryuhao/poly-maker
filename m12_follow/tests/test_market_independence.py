from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest

from test_m4_live_startup import EPOCH, FUNDER, SETTINGS, harness
from polymaker.m4_config import STRATEGY
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_state import M4Ledger
from polymaker.m4_types import M421_LEDGER
from polymaker.taker_pool import TakerPool
from polymaker.taker_runner import strategy_settings


FRAMEWORK = Path(__file__).resolve().parents[1] / "framework"


def test_each_market_uses_current_cash_and_its_own_slots(tmp_path):
    pool = TakerPool(tmp_path / "pool.sqlite3", FRAMEWORK)
    cfg = strategy_settings(FRAMEWORK, M421_LEDGER, M4LiveSettings.load(FRAMEWORK), signal_profile="M42112")

    def decide(epoch, cash):
        return pool.decide(FUNDER, epoch, str(epoch), {M421_LEDGER: cfg},
            {"official_balance_usd": cash, "applicable_allowance_usd": cash},
            clock=lambda: (epoch + 1, epoch + 2))

    first = decide(EPOCH, "15")
    assert first.payload["status"] == "OPEN"
    second = decide(EPOCH + 300, "15")
    assert second.payload["status"] == "OPEN"
    assert second.payload["reserved"] == first.payload["reserved"] == {M421_LEDGER: "10"}
    assert second.payload["available"] == "15" and second.payload["older"] == []
    assert second.strategy_cash(M421_LEDGER, Decimal(15), 10) == 15
    # Duplicate preparation cannot change the same market's frozen decision.
    assert decide(EPOCH + 300, "5").payload == second.payload
    assert decide(EPOCH + 600, "10").payload["reason"] == "TAKER_POOL_INSUFFICIENT_FUNDS"

    directory = tmp_path / "ledgers"
    directory.mkdir()
    # No pool row for the preceding epoch. Even an unreadable old primary
    # ledger cannot enter the new market's available-cash calculation.
    (directory / f"btc-updown-5m-{EPOCH + 900}.sqlite3").write_bytes(b"synthetic invalid old database")
    assert decide(EPOCH + 1200, "15").payload["status"] == "OPEN"


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_at_fees", [True, False])
async def test_t300_detaches_old_recovery_before_new_market_admission(tmp_path, monkeypatch, pause_at_fees):
    h = harness(tmp_path, monkeypatch, phase="market_fees" if pause_at_fees else None)
    original = h.meta.slug
    path = h.startup.root / "ledgers" / f"{original}.sqlite3"
    settings = SETTINGS.model_copy(update={"heartbeat_sec": 20})
    identity = {"strategy": STRATEGY, "mode": "LIVE", "funder": FUNDER,
                "market_id": h.meta.condition_id, "settings_hash": settings.fingerprint}
    ledger = M4Ledger(path, identity)
    ledger.initialize_session("synthetic_old_market", settings.model_dump(mode="json"), asdict(h.meta))
    ledger.set("owned_orders_need_reconciliation", True)
    ledger.close()
    # First path retries an old GET after T300; second reaches T300 during an otherwise successful GET.
    h.clock_values[:] = ([EPOCH + 239, EPOCH + 300, EPOCH + 302] if pause_at_fees else
                         [EPOCH + 239, EPOCH + 300, EPOCH + 301, EPOCH + 302])
    ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
    try:
        assert ready.slug == f"btc-updown-5m-{EPOCH + 300}"
        assert ready.settings == SETTINGS and not ready.startup["recovery"]
        assert ready.startup["transient_read_failures"] == 0
        assert any(e["status"] == "ENDED_MARKET_ARCHIVED" and e["market"] == original for e in h.events)
        assert M4Ledger.metadata(path)["owned_orders_need_reconciliation"] is True
        assert all(reader.closed for reader in h.readers[:-1])
    finally:
        ready.reads.close()
