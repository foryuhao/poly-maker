#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
# Paths in the bundled service intentionally match this deployment.
if [[ "$project_dir" != "$HOME/poly-maker" ]]; then
    echo 'Install the user service after updating its WorkingDirectory/ExecStart for this project path.' >&2
    exit 2
fi
install -m 0644 "$project_dir/ops/polymaker-dashboard-archive.service" "$unit_dir/"
systemctl --user daemon-reload
systemctl --user enable --now polymaker-dashboard-archive.service
systemctl --user --no-pager status polymaker-dashboard-archive.service
