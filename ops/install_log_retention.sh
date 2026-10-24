#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' 'Run this installer with sudo.' >&2
    exit 1
fi

retention_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -d -o root -g root -m 0755 /usr/local/lib/poly-maker
install -o root -g root -m 0644 "$retention_source_dir/log_retention.py" /usr/local/lib/poly-maker/log_retention.py
install -o root -g root -m 0644 "$retention_source_dir/poly-maker-log-retention.service" /etc/systemd/system/poly-maker-log-retention.service
install -o root -g root -m 0644 "$retention_source_dir/poly-maker-log-retention.timer" /etc/systemd/system/poly-maker-log-retention.timer
systemd-analyze verify /etc/systemd/system/poly-maker-log-retention.service /etc/systemd/system/poly-maker-log-retention.timer
systemctl daemon-reload
systemctl start poly-maker-log-retention.service
systemctl enable --now poly-maker-log-retention.timer
systemctl list-timers poly-maker-log-retention.timer --no-pager
