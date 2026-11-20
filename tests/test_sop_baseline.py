"""Sequential public baseline: fixed versions, budget, no duplicate cohorts."""
import json
import sqlite3
from copy import deepcopy
from decimal import Decimal as D
from functools import partial
from pathlib import Path

import pytest
from m1223_baseline_contract import (
    atomic_json,
    cohort,
    completed,
    parameter_document,
    validate_cohort,
    validate_plan,
    verify_runtime,
)
from m1223_baseline_forward import run_class
from m1223_execution import sop_quantity
from m1223_report import install_report_compatibility
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.parameters import load_revision


def plan():
    return json.loads(Path("config/m1223-baseline-plan.json").read_text())


@pytest.mark.parametrize("strategy", ["M23", "M12"])
def test_explicit_frozen_variant_parameters_not_historical_default(strategy):
    selected, proof = load_revision(parameter_document(strategy))
    assert proof["variant_id"] == plan()["variants"][strategy]
    assert definition(selected)["variant_id"] == proof["variant_id"]


@pytest.mark.parametrize("field,value", [
    ("strategy_order", ["M12", "M23"]), ("markets_per_strategy", 24),
    ("quantity_step", "0.01"), ("price_filter", [0.02, 0.2]),
    ("session_budget_usdc", "20"), ("maximum_posts_per_direction", 2),
])
def test_no_implicit_change_to_confirmed_test_contract(field, value):
    with pytest.raises(ValueError, match="CONFIRMED_CONTRACT"):
        validate_plan({**plan(), field: value})


@pytest.mark.parametrize("price", [".01", ".02", ".19", ".20", ".37", ".5", ".88", ".99"])
def test_baseline_sizes_all_legal_prices_on_tenth_share_step(price):
    rule = {"quantity_step": "0.1", "session_budget_usdc": "10"}
    p = D(price)
    q = sop_quantity(p, D(5), rule)
    assert q % D(".1") == 0 and q >= 5 and p * q <= 5
    assert p * (q + D(".1")) > 5


def test_venue_minimum_remains_a_constraint_and_shared_sizing_is_unchanged():
    with pytest.raises(ValueError, match="MINIMUM_EXCEEDS_BUDGET"):
        sop_quantity(D(".9"), D(6), {"quantity_step": "0.1", "session_budget_usdc": "10"})
    assert sop_quantity(D(".37"), D(5), {}) == D("13.51")
    assert sop_quantity(D(".37"), D(5), {"quantity_step": "0.1", "session_budget_usdc": "10"}) == D("13.5")


def test_cohort_is_24h_consecutive_and_bound_to_strategy_and_runtime():
    c = cohort("M23", 1794908400, plan(), "frozen")
    assert len(c["market_epochs"]) == 288
    assert c["market_epochs"][-1] + 300 - c["window_start_epoch"] == 86400
    validate_cohort(c, "M23", plan(), "frozen")
    for change in ({"strategy": "M12"}, {"runtime_sha256": "changed"},
                   {"market_epochs": c["market_epochs"][1:]}):
        with pytest.raises(ValueError, match="COHORT_CHANGED"):
            validate_cohort({**c, **change}, "M23", plan(), "frozen")


def test_next_strategy_requires_full_ordered_cohort_and_clean_shutdown():
    c = cohort("M23", 1794908400, plan(), "frozen")
    result = {"status": "PASS", "reason": "PLANNED_COMPLETE", "markets": 288,
              "members": [{"market": f"btc-updown-5m-{epoch}", "ended": True,
                           "database_closed": True} for epoch in c["market_epochs"]]}
    assert completed(result, c)
    for change in ({"markets": 287}, {"reason": "MANUAL_STOP"}, {"status": "FAIL"},
                   {"members": result["members"][::-1]},
                   {"members": [{**m, "ended": False} for m in result["members"]]}):
        assert not completed({**result, **change}, c)


def test_atomic_state_preserves_unknown_fields_and_manifest_checks_extra_code(tmp_path):
    path = tmp_path / "state.json"
    atomic_json(path, {"stage": "RUNNING", "retained": ["unresolved"]})
    value = json.loads(path.read_text())
    value["stage"] = "COMPLETED"
    atomic_json(path, value)
    assert json.loads(path.read_text())["retained"] == ["unresolved"]
    runtime = tmp_path / "runtime"
    (runtime / "src").mkdir(parents=True)
    (runtime / "extension").mkdir()
    atomic_json(runtime / "frozen-manifest.json", {})
    verify_runtime(runtime)
    (runtime / "extension/extra.py").write_text("x=1\n")
    with pytest.raises(ValueError, match="UNMANIFESTED"):
        verify_runtime(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["M23", "M12"])
async def test_selected_baseline_survives_full_native_capture_ledger_switch_and_cleanup(tmp_path, monkeypatch, strategy):
    import test_m1234_sim_continuous as native
    from m1234_fixtures import fixture_events
    from polymaker.m1234_sim import runner

    parameters = load_revision(parameter_document(strategy))[0]
    tape = deepcopy(fixture_events())
    rule = {**execution_configuration(), "quantity_step": "0.1", "session_budget_usdc": "10"}
    # The deployed immutable bundle stores this as its execution configuration;
    # the worktree keeps the historical default file untouched.
    monkeypatch.setattr("m1223_sim_forward.execution_configuration", lambda: rule)
    tape[0]["definition"]["settings"].update(profiles={}, sop=definition(parameters),
        sop_execution=rule, scenarios={"trade-through": {"post_ms": 100, "ack_ms": 200,
                                                       "cancel_ms": 200, "ahead_cancel_fraction": "0"}})
    first = int(tape[0]["definition"]["market"]["slug"].rsplit("-", 1)[1])
    monkeypatch.setattr(native, "rr1_events", lambda _: tape)
    monkeypatch.setattr(runner, "Engine", partial(SopEngine, parameters=parameters))
    monkeypatch.setattr(runner, "LiveRun", run_class(runner, [first, first + 300, first + 600]))
    install_report_compatibility()
    run = native.coordinator(tmp_path, monkeypatch, markets=3)
    result = await run.run()
    assert result["status"] == "PASS" and result["complete_windows"] == 3
    assert all(e.store.closed for e in run.engines)
    assert all(not rec.thread.is_alive() for rec in run.recorders.values())
    for engine in run.engines:
        with sqlite3.connect(engine.store.path) as db:
            state = json.loads(db.execute(
                "SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
        broker = state["brokers"]["sop/trade-through"]
        assert D(broker["accounts"][strategy]["initial"]) == 10


@pytest.mark.parametrize("first_fails,hold_next", [(False, False), (True, False), (False, True)])
def test_sequential_supervisor_launches_m23_then_m12_once_and_never_replays_a_failed_batch(tmp_path, monkeypatch, first_fails, hold_next):
    import m1223_baseline as supervisor

    root = tmp_path / "m1223_baseline"
    runtime = tmp_path / "runtime"
    atomic_json(root / "plan.json", plan())
    atomic_json(runtime / "config/m1223-baseline-plan.json", plan())
    control = {"enabled": True}
    if hold_next:
        control["auto_start_next_strategy"] = False
    atomic_json(root / "control.json", control)
    atomic_json(root / "state.json", {"source_root": str(runtime), "runtime_sha256": "frozen",
                                     "strategies": {s: {"stage": "PENDING"} for s in ["M23", "M12"]}})
    monkeypatch.setattr(supervisor, "verify_runtime", lambda _: "frozen")
    monkeypatch.setattr(supervisor, "checks", lambda *_: {"synthetic": "PASS"})
    monkeypatch.setattr(supervisor.signal, "signal", lambda *_: None)
    epochs = iter([1794908400, 1794908400 + 86400])

    async def first(_stop):
        epoch = next(epochs)
        return epoch, [epoch - 60, 0, 0]

    monkeypatch.setattr(supervisor, "public_first", first)
    launches = []

    class Child:
        def __init__(self, cmd, **_):
            strategy = cmd[cmd.index("--strategy") + 1]
            launches.append(strategy)
            output = Path(cmd[cmd.index("--output") + 1])
            selected = json.loads(Path(cmd[cmd.index("--cohort") + 1]).read_text())
            result = {"status": "PASS", "reason": "PLANNED_COMPLETE", "markets": 288,
                      "members": [{"market": f"btc-updown-5m-{e}", "ended": True,
                                   "database_closed": True} for e in selected["market_epochs"]]}
            self.returncode = 1 if first_fails else 0
            if not first_fails:
                atomic_json(output / "completion.json", result)
            self.pid = 999

        def poll(self):
            return self.returncode

    monkeypatch.setattr(supervisor.subprocess, "Popen", Child)
    supervisor.execute(tmp_path, root)
    state = json.loads((root / "state.json").read_text())
    if first_fails:
        assert launches == ["M23"] and state["stage"] == "FAILED"
        with pytest.raises(ValueError, match="PRIOR_START_NOT_REPLAYED"):
            supervisor.execute(tmp_path, root)
        assert launches == ["M23"]
    elif hold_next:
        assert launches == ["M23"] and state["stage"] == "COMPLETED_M23_M12_HELD"
        assert state["strategies"]["M12"]["stage"] == "HELD_BY_USER"
        supervisor.execute(tmp_path, root)
        assert launches == ["M23"]
    else:
        assert launches == ["M23", "M12"] and state["stage"] == "COMPLETED"
        supervisor.execute(tmp_path, root)
        assert launches == ["M23", "M12"]
