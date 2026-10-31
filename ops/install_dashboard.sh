#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    echo 'Missing project Python environment; run uv sync --extra dev --locked first.' >&2
    exit 2
fi
mkdir -p "$unit_dir"
"$project_dir/.venv/bin/python" - "$project_dir" "$unit_dir" <<'PY'
import os
import sys
from pathlib import Path

project = Path(sys.argv[1])
unit_dir = Path(sys.argv[2])
# systemd expands percent specifiers even inside quotes.
escaped = str(project).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
if any(ord(char) < 32 or ord(char) == 127 for char in str(project)):
    raise SystemExit('Project path must not contain control characters')
template = (project / 'ops/polymaker-dashboard-archive.service').read_text()
rendered = template.replace('@PROJECT_DIR@', escaped).replace(
    '@WORKING_DIR@', str(project).replace('%', '%%'))
target = unit_dir / 'polymaker-dashboard-archive.service'
temporary = target.with_suffix('.service.tmp')
temporary.write_text(rendered)
temporary.chmod(0o644)
os.replace(temporary, target)
PY
systemctl --user daemon-reload
systemctl --user enable polymaker-dashboard-archive.service
systemctl --user restart polymaker-dashboard-archive.service
systemctl --user --no-pager status polymaker-dashboard-archive.service
