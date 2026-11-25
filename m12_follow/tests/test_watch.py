from m12_follow.watch import classify, hourly, market
from m12_follow import watch
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from m12_follow import sqlite_source


def test_confirmed_execution_must_be_terminal_and_residual_must_be_untradable():
    filled = dict(target="5", shares="5.056819", principal="4.45", state="DUST")
    assert classify(filled, True) == "dust"
    assert classify(dict(filled, open_order=True), True) == "pending"
    assert classify(dict(filled, principal="5"), True) == "full"
    assert classify(dict(filled, principal="2"), True) == "partial"
    assert classify(dict(principal="0", open_order=True), True) == "pending"
    assert classify({}, True) == "unfilled"


def test_hour_uses_all_source_intents_once_including_missed_and_delayed_fills(tmp_path, monkeypatch):
    start = 3600
    source = {"missed": {"anchor": ".6"}, "late": {"anchor": ".6"}}
    evidence = {"late": dict(principal="0", shares="0", posts=1, open_order=True)}
    monkeypatch.setattr(watch, "baseline", lambda *_: (source, "SEALED"))
    monkeypatch.setattr(watch, "execution", lambda *_: evidence)
    before = market(tmp_path, start, start+301)
    assert before["counts"]["intents"] == 2 and before["counts"]["pending"] == 1
    evidence["late"].update(target="5", principal="5", all_in="5.10", shares="8", open_order=False)
    after = market(tmp_path, start, start+7200, before)
    rows = {start: after, start+3600: after, start-300: after}
    summary = hourly(start, rows)
    assert summary["counts"]["intents"] == 2
    assert summary["counts"]["satisfied"] == 1
    assert summary["counts"]["unfilled"] == 1
    assert summary["satisfied_pct_of_observed"] == 50
    assert len(summary["coverage_gaps"]) == 11


def test_zero_denominator_and_unavailable_source_are_distinct():
    rows = {i: {"source": "SEALED", "counts": {}} for i in range(0, 3600, 300)}
    assert hourly(0, rows)["satisfied_pct_of_observed"] is None
    assert hourly(0, rows)["coverage_gaps"] == []
    rows[0]["source"] = "SOURCE_NOT_AVAILABLE"
    assert hourly(0, rows)["coverage_gaps"] == [0]


def test_hourly_net_includes_confirmed_partial_and_revises_original_hour():
    rows = {e: {"source": "SEALED", "counts": {}, "intents": {}} for e in range(0, 3900, 300)}
    proof = {"winner": "YES", "token_ids": {"YES": "y", "NO": "n"}}
    yes = dict(outcome="YES", token="y", shares="3.1", principal="1.55", all_in="1.61", result="partial")
    no = dict(outcome="NO", token="n", shares="7", principal="4.2", all_in="4.25")
    rows[0].update(resolution=proof, intents={"a": yes})
    rows[3300]["intents"] = {"b": no}
    rows[3600].update(resolution=proof, intents={"c": yes})
    first = hourly(0, rows)["pnl"]
    assert first["settled_net_usdc"] == "1.49"
    assert first["awaiting_resolution_markets"] == [3300]
    assert first["awaiting_resolution_cost_usdc"] == "4.25"
    rows[3300]["resolution"] = proof
    final = hourly(0, rows)["pnl"]
    assert final["settled_net_usdc"] == "-2.76"
    assert final["settled_fees_usdc"] == "0.11"
    assert (final["won_intents"], final["lost_intents"], final["awaiting_resolution_intents"]) == (1, 1, 0)
    assert hourly(3600, rows)["pnl"]["settled_net_usdc"] == "1.49"


def test_same_table_header_and_unknown_values():
    row = {"source": "SEALED", "counts": dict.fromkeys(watch.GROUPS, 0)}
    complete = {e: row for e in range(0, 3600, 300)}
    summary = hourly(0, complete)
    assert summary["pnl"]["settled_net_usdc"] == "0"
    assert hourly(0, {})["pnl"]["settled_net_usdc"] is None
    assert watch.coverage_table([("15:00", row)]).splitlines()[0] == watch.coverage_table(
        [("15:00–16:00", summary)]).splitlines()[0]
    assert "待核实" in watch.coverage_table([("15:00", {})])


def test_hour_delivery_skips_only_sealing_and_display_changes(tmp_path):
    path = tmp_path / "hours" / "0.json"
    delivered = {}
    rows = {e: {"source": "OPEN", "counts": {}} for e in range(0, 3600, 300)}
    first = hourly(0, rows)
    assert watch.update_hour(path, first, delivered)["revision"] is False
    for row in rows.values():
        row["source"] = "SEALED"
        current = dict(hourly(0, rows), table="new formatting")
        assert watch.update_hour(path, current, delivered) is None
        assert watch.read(path) == current
    for changed in (dict(current, counts={**current["counts"], "satisfied": 1}),
                    dict(current, pnl={**current["pnl"], "settled_net_usdc": "1.5"})):
        before = delivered.copy()
        assert watch.update_hour(path, changed, delivered)["revision"] is True
        assert watch.update_hour(path, changed, before)["revision"] is True  # retry before delivery
        assert watch.update_hour(path, changed, delivered) is None
    gap = dict(changed, coverage_gaps=[300])
    assert watch.update_hour(path, gap, delivered)["revision"] is True
    assert watch.update_hour(path, changed, delivered)["revision"] is True


def test_existing_delivered_hour_remains_quiet_after_upgrade_and_preview(tmp_path):
    path = tmp_path / "hours" / "0.json"
    old = hourly(0, {e: {"source": "OPEN"} for e in range(0, 3600, 300)})
    watch.atomic_json(path, old)
    watch.atomic_json(tmp_path / "reports" / "first.json", {"hourly": [dict(old, revision=False)]})
    last_delivery = {"0": watch.digest(old)}
    sealed = dict(old, not_sealed_or_missing=[])
    assert watch.update_hour(path, sealed, last_delivery.copy()) is None
    assert watch.update_hour(path, sealed, last_delivery) is None
    assert last_delivery["0"] == watch.hour_notice_key(sealed)


def test_active_handoff_moves_reader_and_watch_but_preserves_market_and_run_identity(tmp_path, monkeypatch):
    root = tmp_path / "baseline"
    phase = root / "m12-m21-phase"
    phase.mkdir(parents=True)
    clock = SimpleNamespace(now=1)
    monkeypatch.setattr(sqlite_source, "time", SimpleNamespace(monotonic=lambda: clock.now))
    slug = "btc-updown-5m-600"
    meta = SimpleNamespace(slug=slug, condition_id="condition")
    marker = phase / "m12-m21-handoff.json"
    handoff = dict(state="SWITCHING", cutoff_epoch=600, strategies=["M12", "M21"])
    (phase / "plan.json").write_text(json.dumps(dict(path=str(phase), schema="m122124-shared-public-72h-v1",
                                                     mode="SIMULATION_ONLY_PUBLIC_DATA")))
    for owner in (root, phase):
        db_path = owner / slug / "M12/simulation.sqlite3"
        db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db_path)) as db:
            db.executescript("CREATE TABLE metadata(name PRIMARY KEY, payload);"
                             "CREATE TABLE audit(seq INTEGER PRIMARY KEY,input_seq,kind,payload);")
            db.executemany("INSERT INTO metadata VALUES(?,?)", [
                ("identity", json.dumps(dict(database_id=owner.name, market=slug, condition_id="condition"))),
                ("run_owner", json.dumps(dict(mode="SIMULATION_ONLY_PUBLIC_DATA", path=str(owner))))])
            if owner == phase:
                i = dict(opportunity="new",market=slug,strategy="M12",profile="M12",side="BUY",
                             order_type="ASSUMED_FIXED_ANCHOR",
                             signal_version=sqlite_source.BASELINE_VERSIONS["M12"],price=".6",outcome="NO")
                db.execute("INSERT INTO audit VALUES(1,1,'INTENT',?)", (json.dumps(dict(scenario="sop/anchor",intent=i)),))
            db.commit()
    with closing(sqlite_source.SQLiteIntents(root, meta, 5)) as reader:
        assert not reader.read(force=True).intents
        old = watch.market(root, 600, 601)
        marker.write_text(json.dumps(handoff))
        clock.now += 2
        assert reader.read(force=True) is None
        marker.write_text(json.dumps(dict(handoff, state="ACTIVE")))
        assert reader.read(force=True).intents[0]["opportunity"] == "new"
        assert reader.read(force=True).input_seq == 1
        assert len(reader.pending_admissions) == 1
        def owned_execution(path, run_name):
            assert run_name == root.name
            return {}
        monkeypatch.setattr(watch, "execution", owned_execution)
        updated = watch.market(root, 600, 605, old)
        assert updated["source_directory"] == str(phase)
        assert updated["counts"]["intents"] == 1
    previous = "btc-updown-5m-300"
    assert sqlite_source.market_source(root, previous) == root
    archive = phase / "recovered" / previous / "market-report.json"
    archive.parent.mkdir(parents=True)
    archive.write_text("{}")
    assert sqlite_source.market_source(root, previous, archived=True) == archive.parent.parent
    assert sqlite_source.market_source(root, previous) == root
