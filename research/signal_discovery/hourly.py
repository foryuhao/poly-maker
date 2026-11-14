"""Bounded SOP progress report and hourly wakeup for this authorized task only."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[2]
STATE = PROJECT / "research_outputs/signal_discovery"
THREAD = "01a1952a-bb2c-7bb2-812a-ef460ed3be4b"
MAX_FILE = 131072


def read(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("REPORT_INPUT_TOO_LARGE")
    return json.loads(data)


def write(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2)
    if len(data.encode()) > MAX_FILE:
        raise ValueError("REPORT_OUTPUT_TOO_LARGE")
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(data + "\n")
    os.replace(temp, path)


def run(wake):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "hourly.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        control = read(STATE / "control.json")
        if not control.get("enabled") or control.get("thread_id") != THREAD:
            return
        now = time.time()
        bucket = int(now // 3600)
        stamp = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).isoformat()
        paths = sorted((PROJECT / "research/signals").glob("M[0-9]*.md"))
        primitive_paths = sorted((PROJECT / "research/signals").glob("P_*.md"))
        variant_paths = sorted((PROJECT / "research/signals").glob("V_*.md"))
        if len(paths) + len(primitive_paths) + len(variant_paths) > 100:
            raise ValueError("SOP_CATALOG_LIMIT")
        files = [{"path": p.name, "bytes": p.stat().st_size,
                  "modified_at": p.stat().st_mtime} for p in paths]
        primitive_files = [{"path": p.name, "bytes": p.stat().st_size,
                            "modified_at": p.stat().st_mtime} for p in primitive_paths]
        variant_files = [{"path": p.name, "bytes": p.stat().st_size,
                          "modified_at": p.stat().st_mtime} for p in variant_paths]
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in ("MemAvailable", "MemTotal"):
                mem[key + "_KiB"] = int(value.split()[0])
        disk = os.statvfs(PROJECT)
        report = {"reported_at_beijing": stamp, "hour_bucket": bucket,
                  "scope": "SIGNAL_AND_SOP_ONLY_TESTING_OWNED_BY_USER_MODULE",
                  "sop_files": files, "primitive_sop_files": primitive_files,
                  "variant_sop_files": variant_files,
                  "progress": read(STATE / "progress.json"),
                  "resources": {**mem, "disk_available_bytes": disk.f_bavail * disk.f_frsize},
                  "tests_started_by_this_task": 0}
        report_dir = STATE / "hourly"
        report_dir.mkdir(exist_ok=True)
        # Only delete exact numeric filenames produced by this worker, in its own directory.
        old = []
        with os.scandir(report_dir) as entries:
            for index, entry in enumerate(entries):
                if index >= 1000:
                    raise ValueError("HOURLY_DIRECTORY_LIMIT")
                if entry.is_file(follow_symlinks=False) and re.fullmatch(r"[0-9]+\.json", entry.name):
                    old.append(Path(entry.path))
        for path in sorted(old, key=lambda p: int(p.stem))[:-47]:
            path.unlink()
        destination = report_dir / f"{bucket}.json"
        write(destination, report)
        write(STATE / "latest.json", report)
        sent = read(STATE / "delivery.json") if (STATE / "delivery.json").exists() else {}
        if wake and sent.get("last_attempt_bucket") != bucket:
            # Persist before enqueue: a timeout/crash must not produce repeated wakeups.
            sent = {"last_attempt_bucket": bucket, "status": "ATTEMPTING", "at": stamp}
            write(STATE / "delivery.json", sent)
            prompt = (
                "【本任务已授权的每小时研究简报】请读取 " + str(STATE / "latest.json") +
                " 和 research/signals/README.md、FIXED_ANCHOR_CONTRACT.md及progress.next。"
                "先在本任务内继续一次有边界的盘口机制挖掘或确认/失效逻辑审查，再简要汇报新增信号、SOP修订和下一步。"
                "口径为信号有效期间在固定锚价成交，Maker/Taker独立列出，保留真实执行可行性限制；"
                "用户已明确：交付每信号独立MD SOP；同家族新分支优先独立V_*.md，不换编号重复计为独立信息。"
                "先与现有M1–M23及原语查重，不为每小时产出强行新增编号。"
                "测试由用户的信号测试模块负责。本任务不要启动信号测试、回测、模拟或实现模拟适配器。"
                "此研究阶段不计算交易数、胜率、PnL，不把尚未测试的假说称为已提高结算概率。"
                "控制CPU、内存和磁盘，只读必要小文件，不生成行情或逐tick日志。"
                "不更改或停止其他研究、信号测试、模拟和交易进程；不新建会话、不向其他任务发消息。"
                "如用户已取消本小时任务，应关闭本任务 control.json 的 enabled 并停止自己的 timer。"
            )
            try:
                result = subprocess.run(
                    [str(Path.home() / ".local/bin/codex"), "queue", "--thread", THREAD,
                     "--message", prompt, "--resume"], cwd=PROJECT,
                    capture_output=True, text=True, timeout=45, check=False)
                warning = "warning" in (result.stdout + result.stderr).lower()
                sent["status"] = ("QUEUE_FAILED" if result.returncode else
                                  "QUEUED_RESUME_UNCONFIRMED" if warning else
                                  "QUEUED_AND_RESUME_REQUESTED")
                sent["returncode"] = result.returncode
            except (OSError, subprocess.SubprocessError) as error:
                sent.update(status="QUEUE_UNCONFIRMED", error_type=type(error).__name__)
            write(STATE / "delivery.json", sent)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wake", action="store_true")
    run(parser.parse_args().wake)
