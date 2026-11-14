#!/usr/bin/env bash
# Manual entrypoint only: consecutive full markets until the first filled signal finishes.
set -euo pipefail

project=/home/ubuntu/polymaker
cd "$project"

mode=first-fill
resume_dir=
case "${1:-}" in
  '') ;;
  --once) mode=once; shift ;;
  --resume) resume_dir=${2:?请指定此前的运行目录}; shift 2 ;;
  --check)
    exec "$project/.venv/bin/python" -B -m polymaker.m4_run --project-dir "$project" --mode live --check
    ;;
  *) echo '用法：m4_first_taker_market.sh [--once | --resume 运行目录 | --check]' >&2; exit 2 ;;
esac
if (( $# )); then
  echo '存在无法识别的参数。' >&2
  exit 2
fi

live_state=$(systemctl --user show polymaker-m4-live.service -p ActiveState --value)
if [[ "$live_state" != inactive ]]; then
  echo "实盘常驻服务状态为 $live_state，请先检查；本脚本未启动交易。" >&2
  exit 1
fi

mkdir -p "$project/research_outputs/m4_live/manual-runs"
exec 9>"$project/research_outputs/m4_live/manual-runs/launch.lock"
flock -n 9 || { echo "已有实盘启动脚本运行中。" >&2; exit 1; }
if [[ -n "$resume_dir" ]]; then
  run_dir=$(realpath -e -- "$resume_dir")
  if [[ "$run_dir" != "$project/research_outputs/m4_live/manual-runs/"* || ! -f "$run_dir/campaign.json" ]]; then
    echo '恢复目录必须是已有 campaign.json 的 M4 manual-runs 子目录。' >&2
    exit 1
  fi
else
  run_dir=$(mktemp -d "$project/research_outputs/m4_live/manual-runs/first-taker-$(date +%Y%m%dT%H%M%S)-XXXXXX")
fi

"$project/.venv/bin/python" -B - "$run_dir" <<'PY'
from pathlib import Path

from polymaker.m4_control import StopControl
from polymaker.m4_live_config import M4LiveSettings

root = Path('/home/ubuntu/polymaker')
settings = M4LiveSettings.load(root)
if settings.execution.mode != 'taker':
    raise SystemExit('执行模式不是 taker，未启动交易。')

if StopControl(root, live=True).inhibited():
    raise SystemExit('存在停机保护，保留标记并取消启动。')
print('Taker 配置校验通过。')
PY

echo "本次日志：$run_dir/run.log"
if [[ "$mode" == once ]]; then
  options=(--once)
  echo '运行一个完整市场后退出。提前停止请按 Ctrl+C。'
else
  options=(--until-first-fill "$run_dir/campaign.json")
  echo '连续运行完整市场；一次执行一个信号，首个成交信号按原限制补足后，本场收尾退出。'
  echo '场末 UNKNOWN / 待确认成交或异常会停止继续开场。提前停止请按 Ctrl+C。'
fi
PYTHONUNBUFFERED=1 "$project/.venv/bin/python" -B -m polymaker.m4_run \
  --project-dir "$project" --mode live --live "${options[@]}" \
  2>&1 | tee -a "$run_dir/run.log"
