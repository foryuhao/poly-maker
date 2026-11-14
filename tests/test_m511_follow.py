import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import m511_sim_start
import pytest
from m511_sim import definition
from m511_sim_follow import Tail
from maker_signals.strategies import M5_M10_FREQUENCY


def test_gzip_tail_partial_member_and_second_member_preserve_every_event(tmp_path):
    path = tmp_path / "input.gz"
    raw = b"".join((json.dumps({"seq": i}) + "\n").encode() for i in range(1, 2001))
    first = gzip.compress(raw)
    path.write_bytes(first[: len(first) // 2])
    tail = Tail(path)
    rows = []
    for _ in range(10):
        rows.extend(tail.batch(123))
    assert not tail.eof
    with path.open("ab") as f:
        f.write(first[len(first) // 2 :])
    for _ in range(20):
        rows.extend(tail.batch(123))
    assert tail.eof and [r["seq"] for r in rows] == list(range(1, 2001))
    with path.open("ab") as f:
        f.write(gzip.compress(b'{"seq": 2001}\n'))
    rows.extend(tail.batch())
    assert tail.eof and rows[-1]["seq"] == 2001
    assert tail.sha.hexdigest() == __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    tail.close()


@pytest.mark.parametrize("variant", [None, M5_M10_FREQUENCY])
def test_start_records_validated_signal_versions(tmp_path, monkeypatch, variant):
    monkeypatch.setattr(m511_sim_start, "PROJECT", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    control = tmp_path / "research_outputs/m1234_sim/monitor/control.json"
    control.parent.mkdir(parents=True)
    validation = tmp_path / "validation"
    launch = validation / "launch"
    proof = validation / "btc-example/replay-validation.json"
    launch.mkdir(parents=True)
    proof.parent.mkdir()
    selected = definition(variant)
    files = {
        source / "plan.json": {"started_utc_epoch": 0, "max_seconds": 3600},
        control: {"unit": "source.service", "run_directory": str(source)},
        validation / "plan.json": {"m5_m11": selected},
        validation / "completion.json": {"status": "CAUGHT_UP", "unfinished_markets": []},
        proof: {"status": "VALID", "legacy_brokers_identical": True},
        launch / "directed-validation.json": {"status": "PASS"},
        launch / "economic-validation.json": {"status": "PASS"},
        launch / "extension-manifest.json": {},
        launch / "launch.json": {"source_run": str(source), "source_root": str(tmp_path)},
    }
    for path, value in files.items():
        path.write_text(json.dumps(value))

    def run(command, **_kwargs):
        if command[0] == "systemctl":
            return SimpleNamespace(stdout="ActiveState=active\nMainPID=1\n")
        if any(arg.endswith("/m511_sim_follow.py") for arg in command):
            if variant is None:
                assert "--signal-variant" not in command
            else:
                assert command[-2:] == ["--signal-variant", variant]
            out = Path(command[command.index("--output") + 1])
            (out / "plan.json").write_text(json.dumps({"m5_m11": selected}))

    monkeypatch.setattr(m511_sim_start.subprocess, "run", run)
    record = m511_sim_start.start(validation)
    assert record.get("signal_variant") == variant
    assert record["strategy_versions"] == selected["strategy_versions"]
