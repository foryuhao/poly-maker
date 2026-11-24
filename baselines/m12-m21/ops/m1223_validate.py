"""Test the immutable simulation bundle and retain the exact tested sources."""

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def exclusive_validation_window():
    """Full regression work must not compete with this task's live input loop.

    State files alone are insufficient: query actual user units. A validation
    launched in its own finite unit may see itself, but no other M12–M23 job.
    Unrelated live/research services are neither blocked nor changed here.
    """
    cgroup = Path("/proc/self/cgroup").read_text()
    own_units = {part for line in cgroup.splitlines() for part in line.split("/")
                 if part.startswith("polymaker-m1223-") and part.endswith(".service")}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=service", "--all", "--plain", "--no-legend",
             "--state=activating,active,reloading,deactivating", "polymaker-m1223-*.service"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("SOP_VALIDATION_SERVICE_STATUS_UNAVAILABLE") from exc
    if result.returncode:
        raise RuntimeError("SOP_VALIDATION_SERVICE_STATUS_UNAVAILABLE")
    active, unrelated = [], []
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        if (len(fields) < 4 or not fields[0].startswith("polymaker-m1223-")
                or not fields[0].endswith(".service")):
            raise RuntimeError("SOP_VALIDATION_SERVICE_STATUS_UNPARSEABLE")
        if fields[2] in {"activating", "active", "reloading", "deactivating"} and fields[0] not in own_units:
            # An independently owned baseline capture shares the historical
            # unit prefix. Verify its exact entry/root before classifying it;
            # never stop it or treat an unobserved command as unrelated.
            if fields[0] == "polymaker-m1223-baseline.service":
                command = subprocess.run(
                    ["systemctl", "--user", "show", fields[0], "--property=ExecStart", "--value"],
                    capture_output=True, text=True, timeout=5,
                )
                if (command.returncode == 0
                        and "/research_outputs/m1223_baseline/runtime/" in command.stdout
                        and "/extension/m1223_baseline.py" in command.stdout
                        and "--root /home/ubuntu/polymaker/research_outputs/m1223_baseline run" in command.stdout):
                    unrelated.append(fields[0])
                    continue
            active.append(fields[0])
    if active:
        raise RuntimeError("SOP_FULL_VALIDATION_DEFERRED_WHILE_TASK_SERVICE_ACTIVE:" + ",".join(sorted(active)))
    return {"status": "NO_OTHER_ACTIVE_M1223_SERVICE", "source": "ACTUAL_USER_SYSTEMD_UNITS",
            "own_units_excluded": sorted(own_units), "task_services_changed": False,
            "separate_baseline_units_observed_not_changed": unrelated}


def validate(runtime, output, *, project):
    runtime, output, project = runtime.resolve(), output.resolve(), project.resolve()
    if output.exists() or not output.is_relative_to(project / "research_outputs/m1223_sim"):
        raise ValueError("NEW_OWN_VALIDATION_OUTPUT_REQUIRED")
    validation_window = exclusive_validation_window()
    manifest = json.loads((runtime / "frozen-manifest.json").read_text())
    for rel, expected in manifest.items():
        source = (runtime / rel).resolve()
        if (
            not source.is_relative_to(runtime)
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("VALIDATION_FROZEN_SOURCE_CHANGED:" + rel)
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if runtime.name != fingerprint[:16]:
        raise ValueError("VALIDATION_RUNTIME_IDENTITY_MISMATCH")
    # A separately owned live baseline now shares the old filename prefix.
    # Its own launcher/configuration are deliberately outside this bundle.
    candidates = sorted((project / "tests").glob("test_sop_*.py"))
    excluded_tests = {p.name: "SEPARATE_M1223_BASELINE_TASK_NOT_IN_THIS_FROZEN_BUNDLE"
                      for p in candidates if p.name.startswith("test_sop_baseline")}
    tests = [p for p in candidates if p.name not in excluded_tests]
    if not tests:
        raise ValueError("DIRECTED_SOP_TESTS_UNAVAILABLE")
    output.mkdir(parents=True)
    source_manifest = {}
    # Freeze executed tests and their local helper imports, not unrelated tests
    # another task may legitimately edit while this immutable suite is running.
    test_root = project / "tests"
    sources = set(tests)
    conftest = test_root / "conftest.py"
    if conftest.is_file():
        sources.add(conftest)
    pending = list(sources)
    while pending:
        path = pending.pop()
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                     else [v.name for v in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                helper = test_root / (name.replace(".", "/") + ".py")
                if helper.is_file() and helper not in sources:
                    sources.add(helper)
                    pending.append(helper)
    for path in sorted(sources):
        rel = path.relative_to(project / "tests")
        destination = output / "test-sources" / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        source_manifest[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "test-source-manifest.json").write_text(json.dumps(source_manifest, indent=2) + "\n")
    command = [
        str(project / ".venv/bin/python"),
        "-m",
        "pytest",
        "--assert=plain",
        "-q",
        "-p",
        "no:cacheprovider",
        "--junitxml=" + str(output / "tests.xml"),
        *[str(path) for path in tests],
    ]
    result = subprocess.run(
        command,
        cwd=runtime,
        env={
            **os.environ,
            "PYTHONPATH": str(runtime / "extension")
            + ":"
            + str(runtime / "src")
            + ":"
            + str(project / "tests"),
        },
        capture_output=True,
        text=True,
    )
    (output / "tests.txt").write_text(result.stdout + result.stderr)
    xml = ET.parse(output / "tests.xml").getroot()
    cases = list(xml.iter("testcase"))
    passed = sum(
        not any(node.tag in {"failure", "error", "skipped"} for node in case) for case in cases
    )
    sources_unchanged = all(
        hashlib.sha256((project / "tests" / rel).read_bytes()).hexdigest() == expected
        for rel, expected in source_manifest.items()
    )
    record = {
        "status": "PASS"
        if result.returncode == 0 and passed == len(cases) and cases and sources_unchanged
        else "FAIL",
        "runtime_sha256": fingerprint,
        "runtime": str(runtime),
        "returncode": result.returncode,
        "command": command,
        "cwd": str(runtime),
        "test_cases": len(cases),
        "passed": passed,
        "test_sources_unchanged_during_execution": sources_unchanged,
        "test_source_manifest_sha256": hashlib.sha256(
            (output / "test-source-manifest.json").read_bytes()
        ).hexdigest(),
        "scope": "DIRECTED_TESTS_NOT_REALTIME_OR_FREQUENCY_ADMISSION",
        "separate_task_tests_not_executed": excluded_tests,
        "validation_resource_exclusion": validation_window,
        "checked_beijing": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    (output / "validation.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project", default=Path("/home/ubuntu/polymaker"), type=Path)
    args = parser.parse_args()
    result = validate(args.runtime, args.output, project=args.project)
    print(
        json.dumps(
            {key: result[key] for key in ("status", "runtime_sha256", "test_cases", "passed")}
        )
    )
    if result["status"] != "PASS":
        raise SystemExit(1)
