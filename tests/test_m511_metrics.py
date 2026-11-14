import json
import sqlite3

from m511_sim import GROUPS
from m511_sim_instant_fill import base as instant
from m511_sim_metrics import collect

from test_m1234_sim_instant_fill import append, fixture, resolution, submit


def test_m10_m11_do_not_become_m1_or_duplicate_assumed_fills(tmp_path):
    run, path, tracker, out = fixture(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO metadata VALUES('research_sample_eligibility',?)",
            (json.dumps({"status": "ELIGIBLE"}),),
        )
    append(
        path,
        [
            (
                "ACCOUNT_OPEN",
                {"profile": s, "scenario": "research/trades-only", "initial_cash": "20", "at": 1},
            )
            for s in GROUPS
        ],
    )
    signal = (
        "SIGNAL",
        {
            "strategy": "M10",
            "event": "CANDIDATE",
            "payload": {"id": "candidate-1", "generation": 1},
        },
    )
    append(
        path,
        [
            signal,
            signal,
            submit("research/trades-only", strategy="M10"),
            submit("research/cancel-half", strategy="M10"),
            (
                "MODELLED_FILL",
                {"scenario": "research/cancel-half", "order": "order", "quantity": "2"},
            ),
            ("ORDER_UNCERTAIN", {"scenario": "research/cancel-half", "order": "order"}),
            resolution(),
        ],
    )
    metrics = collect(run, tmp_path / "cache.json")
    m = metrics["modes"]["research"]["strategies"]["M10"]
    assert (
        m["signals"] == 1
        and m["submitted_orders"] == 2
        and m["filled_orders"] == 1
        and m["unresolved_orders"] == 1
    )
    assert metrics["modes"]["main"]["strategies"]["M1"]["signals"] == 0
    ledger = instant.InstantFill(run, out, "research/trades-only")
    try:
        result = ledger.refresh()
        m = result["strategies"]["M10"]
        assert m["signals"] == 1 and m["assumed_filled_orders"] == 1
        assert (
            result["execution_comparison"]["M10"]["research/cancel-half"]["unresolved_orders"] == 1
        )
        again = ledger.refresh()
        assert again["strategies"]["M10"]["assumed_filled_orders"] == 1
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE metadata SET payload=? WHERE name='research_sample_eligibility'",
                (json.dumps({"status": "SELF_CONTAMINATED", "reason": "known real post"}),),
            )
        quarantined = ledger.refresh()
        assert quarantined["strategies"]["M10"]["signals"] == 0
        assert len(quarantined["excluded_research_samples"]) == 1
        filtered = collect(run, tmp_path / "cache.json")
        assert filtered["modes"]["research"]["strategies"]["M10"]["submitted_orders"] == 0
        assert len(filtered["excluded_research_samples"]) == 1
    finally:
        ledger.close()
