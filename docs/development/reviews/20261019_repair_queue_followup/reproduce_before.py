"""Expected three failures: load old classes only in this isolated test process."""

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

import polymaker.rdp_sm_execution as execution
import polymaker.rdp_sm_lifecycle as lifecycle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main():
    sys.path.insert(0, str(ROOT / "tests"))
    hashes = json.loads((HERE / "source_before.json").read_text())
    for module, filename, name in (
        (execution, "rdp_sm_execution.py", "_SMProcessor"),
        (lifecycle, "rdp_sm_lifecycle.py", "BatchLifecycle"),
    ):
        source = (HERE / (filename+".before")).read_bytes()
        assert hashlib.sha256(source).hexdigest() == hashes["src/polymaker/"+filename]
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), filename+" (before)", "exec"),
             module.__dict__)
    return pytest.main([
        "-q", str(ROOT / "tests/test_repair_queue_followup.py"),
        "-k", "preserves_same_tp_queue or allows_fresh_account_without_timeout",
        "--tb=short", "-o", "junit_family=xunit1",
        "--basetemp=/home/ubuntu/poly-maker-data/research_outputs/REPAIR_20261019_RELEASE_TMP/queue_before_reproduced",
        "--junitxml="+str(HERE / "before.xml"),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
