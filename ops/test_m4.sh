#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$task_root"
exec .venv/bin/python -m pytest -c tests/m4/pytest.ini --confcutdir=tests/m4 tests/m4 "$@"
