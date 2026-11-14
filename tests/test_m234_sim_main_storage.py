import gzip
import json

from m234_sim_main import MainEngine, experiment_definition
from m234_sim_main_metrics import split_modes
from m234_sim_main_storage import ForwardEngine, storage_definition

from m1234_fixtures import fixture_events
from polymaker.m1234_sim import report
from polymaker.m1234_sim.events import digest


async def test_compression_retains_all_diagnostics_counts_economics_and_definition(tmp_path, monkeypatch):
    tape = fixture_events()
    tape[0]["definition"]["settings"].update(
        m234_execution_main=experiment_definition(), audit_storage=storage_definition())
    states, reports = [], []
    for cls, name in ((MainEngine, "full"), (ForwardEngine, "compressed")):
        e = cls(tmp_path / name)
        for event in tape:
            await e.ingest(event)
        states.append(e.snapshot())
        await e.close()
        reports.append(report.summary(e.store.path))
    assert digest(states[0]) == digest(states[1])
    assert reports[0]["accounts"] == reports[1]["accounts"]
    assert reports[0]["event_counts"] == reports[1]["event_counts"]
    def quality(rows):
        return sorted(rows, key=lambda r: (r["strategy"], r["reason"]))
    assert quality(reports[0]["input_quality_diagnostics"]) == quality(reports[1]["input_quality_diagnostics"])
    import sqlite3
    with sqlite3.connect(tmp_path / "full/simulation.sqlite3") as db:
        full_rows = [(seq, input_seq, json.loads(raw)) for seq, input_seq, raw in db.execute(
            "SELECT seq,input_seq,payload FROM audit WHERE kind='SIGNAL' "
            "AND json_extract(payload,'$.event')='UNAVAILABLE' ORDER BY seq")]
        original = [(input_seq, payload) for _, input_seq, payload in full_rows]
    archive = tmp_path / "compressed/unavailable-diagnostics.jsonl.gz"
    with gzip.open(archive, "rt") as stream:
        relocated = [json.loads(line) for line in stream]
    assert [(r["input_seq"], r["payload"]) for r in relocated] == original
    assert [r["after_audit_seq"] for r in relocated] == [
        seq - index for index, (seq, _, _) in enumerate(full_rows, 1)
    ]
    assert original  # Exercise compression, rather than passing on an empty fixture.
    assert reports[1]["trace"]["diagnostic_archive"]["closed"]


def test_mode_counts_do_not_double_count_baseline_or_duplicate_signals():
    metrics = {"strategies": {s: {"signals": 3} for s in ("M1", "M2", "M3", "M4")},
               "by_profile_scenario": {}}
    for key, n in (("M2/M2/trades-only", 1), ("M2/M2/main/trades-only", 2),
                   ("M1/M1-virtual/trades-only", 3)):
        metrics["by_profile_scenario"][key] = {"submitted_orders": n, "filled_orders": n,
                                              "unresolved_orders": 0, "fill_events": n,
                                              "filled_shares": str(n)}
    result = split_modes(metrics)
    assert result["modes"]["main"]["strategies"]["M2"]["submitted_orders"] == 2
    assert result["modes"]["baseline"]["strategies"]["M2"]["submitted_orders"] == 1
    for mode in result["modes"].values():
        assert mode["strategies"]["M1"]["submitted_orders"] == 3
        assert mode["strategies"]["M2"]["signals"] == 3
