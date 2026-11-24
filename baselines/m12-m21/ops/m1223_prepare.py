"""Freeze one explicit SOP on pinned public simulation dependencies."""

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "research_outputs/m1223_sim"
CLEAN_BASE = PROJECT / "research_outputs/m511_sim/runtime/637ef827c161abc1"
RETAINED_BASE = ROOT / "runtime/6e045f97cd5a7df0"
RETAINED_BASE_SHA256 = "6e045f97cd5a7df08ea532e850926cf564848e841ecdc50ea1416e7dbbdd3afe"
BASE_CONFIGS = ("m1234-sim-contract.json", "m1234-sim-24h.toml")
TASK_MODULES = (
    "m1223_capture_diagnostics.py", "m1223_capture_recording.py", "m1223_dataset.py",
    "m1223_execution.py", "m1223_gates.py", "m1223_pair_diagnostics.py",
    "m1223_phase_report.py", "m1223_prepare.py", "m1223_replay.py", "m1223_report.py",
    "m1223_reproduce.py", "m1223_selection.py", "m1223_settlement_closeout.py",
    "m1223_sim.py", "m1223_sim_forward.py", "m1223_start.py", "m1223_status.py",
    "m1223_validate.py", "m1223_variant_replay.py",
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def clean_base_source():
    """Recover a deleted archival base only from a pinned, verified own bundle.

    Do not recreate the legacy task directory or fall back to the live worktree.
    Current SOP/ops modules are overlaid below; native public engine dependencies
    and execution contract files retain the already frozen source identity.
    """
    complete = (all((CLEAN_BASE / "config" / name).is_file() for name in BASE_CONFIGS)
                and all((CLEAN_BASE / name).is_dir() for name in ("src", "extension")))
    if complete:
        return CLEAN_BASE, {"mode": "ORIGINAL_CLEAN_BASE", "path": str(CLEAN_BASE)}
    mp = RETAINED_BASE / "frozen-manifest.json"
    if not mp.is_file():
        raise ValueError("VERIFIED_RETAINED_BASE_UNAVAILABLE_NO_LIVE_WORKTREE_FALLBACK")
    manifest = json.loads(mp.read_text())
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if fingerprint != RETAINED_BASE_SHA256:
        raise ValueError("RETAINED_BASE_MANIFEST_IDENTITY_CHANGED")
    for rel, expected in manifest.items():
        source = RETAINED_BASE / rel
        if (not source.resolve().is_relative_to(RETAINED_BASE.resolve())
                or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected):
            raise ValueError("RETAINED_BASE_FILE_CHANGED:" + rel)
    actual_py = {str(p.relative_to(RETAINED_BASE)) for name in ("src", "extension")
                 for p in (RETAINED_BASE / name).rglob("*.py")}
    expected_py = {n for n in manifest if n.endswith(".py") and n.split("/", 1)[0] in {"src", "extension"}}
    if actual_py != expected_py or not actual_py:
        raise ValueError("RETAINED_BASE_EXTRA_OR_MISSING_SOURCE")
    if any("config/" + name not in manifest for name in BASE_CONFIGS):
        raise ValueError("RETAINED_BASE_REQUIRED_CONFIGURATION_NOT_FROZEN")
    return RETAINED_BASE, {
        "mode": "RECOVERED_VERIFIED_RETAINED_RUNTIME", "path": str(RETAINED_BASE),
        "runtime_sha256": fingerprint, "manifest_sha256": hashlib.sha256(mp.read_bytes()).hexdigest(),
        "verified_manifest_files": len(manifest), "verified_python_files": len(actual_py),
        "unavailable_original_path": str(CLEAN_BASE),
        "original_status": "INCOMPLETE" if CLEAN_BASE.exists() else "MISSING",
        "workspace_base_dependencies_copied": False,
    }


def freeze_runtime(state, *, parameter_file=None):
    """Create an immutable bundle without changing the active capture pointer."""
    if not state.get("enabled") or state["strategy"] not in {"M7", "M9", "M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M21", "M23"}:
        raise ValueError("SOP_STATE_NOT_ENABLED_IMPLEMENTED_STRATEGY")
    if parameter_file is None and state["strategy"] != "M12":
        raise ValueError("NON_M12_STRATEGY_REQUIRES_EXPLICIT_FROZEN_PARAMETERS")
    if parameter_file is not None:
        from sop_signals.parameters import load_revision

        _, revision = load_revision(json.loads(Path(parameter_file).read_text()))
        if revision["strategy"] != state["strategy"]:
            raise ValueError("SOP_PARAMETER_STRATEGY_MISMATCH")
    base, base_provenance = clean_base_source()
    entries = {}
    for prefix in ("src", "extension"):
        for p in sorted((base / prefix).rglob("*.py")):
            entries[str(p.relative_to(base))] = p
    # The v7/v8 complementary feed loads this immutable JSON at import time.
    # Older native-only bundles omitted it; recover only the pinned document,
    # never copy arbitrary current legacy configuration into a frozen run.
    legacy_parameter_snapshot = "research/signal_discovery/spec_archive/parameters-3a8347790f905216.json"
    legacy_parameters = Path(state["sop_freeze"]) / legacy_parameter_snapshot
    if not legacy_parameters.is_file():
        legacy_parameters = PROJECT / legacy_parameter_snapshot
    if hashlib.sha256(legacy_parameters.read_bytes()).hexdigest() != (
        "3a8347790f905216b5b511f24892b0507a4777873a6dce374ab53d5e9c82ea2c"
    ):
        raise ValueError("PINNED_LEGACY_PARAMETER_DOCUMENT_CHANGED")
    entries["extension/maker_signals/parameters.json"] = legacy_parameters
    for p in sorted((PROJECT / "research/sop_signals").glob("*.py")):
        entries["extension/sop_signals/" + p.name] = p
    for name in TASK_MODULES:
        source = PROJECT / "ops" / name
        entries["extension/" + source.name] = source
    for name in (
        "m1223-execution.json",
        "m1223-input.json",
        "m1223-development.toml",
        "m1223-24h.toml",
    ):
        entries["config/" + name] = PROJECT / "config" / name
    for name in ("m1234-sim-contract.json", "m1234-sim-24h.toml"):
        entries["config/" + name] = base / "config" / name
    if parameter_file is not None:
        entries["config/m1223-parameters.json"] = Path(parameter_file)
    freeze = Path(state["sop_freeze"])
    for rel, expected in json.loads((freeze / "manifest.json").read_text()).items():
        p = freeze / rel
        if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError("SOP_FREEZE_CHANGED:" + rel)
        entries["documents/" + rel] = p
    inventory = {
        name: hashlib.sha256(p.read_bytes()).hexdigest() for name, p in sorted(entries.items())
    }
    fingerprint = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    runtime = ROOT / "runtime" / fingerprint[:16]
    if runtime.exists():
        if json.loads((runtime / "frozen-manifest.json").read_text()) != inventory:
            raise ValueError("EXISTING_RUNTIME_IDENTITY_CONFLICT")
        for rel, expected in inventory.items():
            if hashlib.sha256((runtime / rel).read_bytes()).hexdigest() != expected:
                raise ValueError("EXISTING_RUNTIME_CHANGED:" + rel)
    else:
        for name, p in entries.items():
            dst = runtime / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
        write(runtime / "frozen-manifest.json", inventory)
    return {
        "source_root": str(runtime),
        "sha256": fingerprint,
        "files": len(inventory),
        "clean_base": str(base),
        "clean_base_provenance": base_provenance,
        "workspace_unrelated_dirty_code_included": False,
    }


def prepare():
    state = json.loads((ROOT / "state.json").read_text())
    if state.get("active_run") or state.get("active_job"):
        raise ValueError("SOP_STATE_NOT_IDLE")
    result = freeze_runtime(state)
    state.update(
        updated_beijing=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        stage="FROZEN_DIRECTED_VALIDATION",
        source_root=result["source_root"],
        runtime_sha256=result["sha256"],
        clean_base=result["clean_base"],
        clean_base_provenance=result["clean_base_provenance"],
        code_freeze_files=result["files"],
    )
    write(ROOT / "state.json", state)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    prepare()
