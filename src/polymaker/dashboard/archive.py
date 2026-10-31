"""Immutable market revisions plus an observed snapshot every five minutes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from polymaker.dashboard.collector import Collector, atomic_json, dumps
from polymaker.dashboard.model import Record

TZ = ZoneInfo("Asia/Shanghai")


def clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def session_report(s: Record) -> str:
    residual = s["residual_pnl"] if s["residual_pnl"] is not None else "PENDING"
    total = s["trading_pnl"] if s["trading_pnl"] is not None else "PENDING"
    return (
        f"# {s['slug']}\n\nSession: `{s['session_id']}`\n\n"
        f"状态: {s['phase']} / {s['reason'] or '—'}\n\n"
        f"CA / BA 确认: {s['signal_counts']['CA']} / {s['signal_counts']['BA']}。"
        "确认数量不等于可交易机会。\n\n"
        f"POST: {s['submitted_orders']}；成交订单: {s['filled_orders']}；"
        f"待确认成交: {s['pending_fills']}；待核实订单: {s['pending_orders']}。\n\n"
        f"YES / NO: {s['inventory']['YES']} / {s['inventory']['NO']} sh；Δ: {s['delta']}。\n\n"
        f"已确认配对 PnL: {s['pair_pnl']} USDC；"
        f"Maker: {s['pnl_by_role']['MAKER']}；Taker: {s['pnl_by_role']['TAKER']}；"
        f"其他: {s['pnl_by_role']['OTHER']}。\n\n"
        f"残仓结算 PnL: {residual}；交易 PnL: {total}。\n\n"
        f"手续费: {s['fees']} USDC（已计入成本，不能重复扣除）；"
        f"返佣: {s['rebates']}；gas: UNKNOWN。\n\n"
        f"配对: {s['paired_quantity']}；已合并: {s['merged_pairs']}；"
        f"已配未合并: {s['unmerged_pairs']}。\n\n"
        f"数据问题: {', '.join(s['issues']) or '无已知问题'}。\n\n"
        "PnL 按已确认成交逐笔 FIFO 配对，费用按份数分摊；完成配对的退出腿决定归因。"
        "CA 再开仓吸收残仓、TP 和系统退出归其他；合并与赎回现金不重复计收益。"
        "分布样本为 FIFO 配对批次。日志未提供的估值与结算不推算。\n"
    )


def publish(directory: Path, files: dict[str, bytes], manifest: Record) -> None:
    """An interrupted write cannot expose a half-built version."""
    directory.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".building-", dir=directory.parent))
    try:
        for name, content in files.items():
            with (temp / name).open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        manifest["files"] = {name: {"sha256": digest(content), "bytes": len(content)}
                             for name, content in files.items()}
        atomic_json(temp / "manifest.json", manifest)
        os.rename(temp, directory)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise


class Archiver:
    def __init__(self, collector: Collector):
        self.collector = collector
        self.output = collector.output
        self.last_error: str | None = None
        self.seen: dict[str, str] = {}
        row = collector.db.execute("SELECT value FROM metadata WHERE key='last_boundary'").fetchone()
        self.last_boundary = int(row[0]) if row else 0

    def market(self, s: Record, observed_at: float) -> Path | None:
        if s["market_end"] is None or s["market_end"] > observed_at:
            return None
        # Backfill is never presented as an observation made at the old cutoff.
        if any(not source.get("caught_up") for source in s["sources"]):
            return None
        day = datetime.fromtimestamp(s["market_start"], TZ)
        root = self.output / "archives" / day.strftime("%Y%m%d") / f"{day:%H%M}_{s['session_id']}"
        signature = digest(dumps(s).encode())
        previous = None
        if (root / "latest.json").exists():
            previous = json.loads((root / "latest.json").read_text())
            if previous["signature"] == signature:
                return None
        version = previous["version"] + 1 if previous else 1
        # Handle a crash after directory publication but before pointer replacement.
        while (root / f"v{version:04}").exists():
            version += 1
        target = root / f"v{version:04}"
        events = self.collector.events(s["session_id"])
        signals: list[Record] = []
        execution: list[Record] = []
        for e in events:
            is_signal = e["source_kind"] == "ca" or e["kind"] in {
                "sm_ba_episode", "sm_opposite_ba_episode", "sm_opposite_ba_execution",
                "sm_ca_normal_execution", "sm_ca_wait", "sm_ca_final_decision"}
            (signals if is_signal else execution).append(e)
        def encode_lines(rows: list[Record]) -> bytes:
            return ("".join(dumps(row) + "\n" for row in rows)).encode()
        status = "FINAL" if s["ended"] and not s["issues"] and s["trading_pnl"] is not None else "PROVISIONAL"
        manifest = {"schema_version": 1, "kind": "market", "session_id": s["session_id"],
                    "market_start": s["market_start"], "market_end": s["market_end"],
                    "observed_at": observed_at, "observed_local": clock(observed_at),
                    "status": status, "version": version, "previous_version": previous["version"] if previous else None,
                    "signature": signature, "sources": s["sources"],
                    "runtime_source_sha256": s["header"].get("runtime_source_sha256", {}),
                    "cutoff_semantics": "full session evidence observed at observed_at; not a reconstructed boundary snapshot"}
        publish(target, {"snapshot.json": (dumps(s) + "\n").encode(),
                         "summary.md": session_report(s).encode(),
                         "signals.jsonl": encode_lines(signals),
                         "orders-fills.jsonl": encode_lines(execution)}, manifest)
        atomic_json(root / "latest.json", {"version": version, "directory": target.name,
                                           "signature": signature, "status": status,
                                           "observed_at": observed_at})
        return target

    def tick(self, snapshot: Record) -> Record:
        now = snapshot["observed_at"]
        boundary = int(now // 300) * 300
        revised = []
        self.last_error = None
        try:
            # Revisions also happen between boundaries as late confirmations arrive.
            for s in snapshot["sessions"]:
                material = digest(dumps({k: s[k] for k in (
                    "ended", "execution_status", "issues", "pairs", "residual", "trading_pnl",
                    "pending_fills", "pending_orders", "filled_orders", "merged_pairs")}).encode())
                if boundary == self.last_boundary and self.seen.get(s["session_id"]) == material:
                    continue
                target = self.market(s, now)
                self.seen[s["session_id"]] = material
                if target:
                    revised.append(str(target))
            if boundary > self.last_boundary:
                local = datetime.fromtimestamp(boundary - 300, TZ)
                root = self.output / "intervals" / local.strftime("%Y%m%d") / local.strftime("%H%M")
                if not root.exists():
                    payload = {**snapshot, "sessions": [
                        {k: s[k] for k in ("session_id", "slug", "phase", "market_start", "market_end",
                                          "pair_pnl", "pnl_by_role", "trading_pnl", "residual_pnl", "issues")}
                        for s in snapshot["sessions"]],
                               "window_start": boundary - 300, "window_end": boundary,
                               "observation_lag_seconds": max(0, now - boundary),
                               "missed_windows": max(0, (boundary - self.last_boundary) // 300 - 1) if self.last_boundary else None}
                    text = (f"# 五分钟运行归档 {clock(boundary - 300)}—{clock(boundary)}\n\n"
                            f"实际采集: {clock(now)}；已发现 {len(snapshot['sessions'])} 个 session。\n\n"
                            "这是归档时实际已观察到的快照；启动补档和停机期间不能还原为当时实况。\n\n"
                            + "\n".join(f"- {s['slug']} / {s['session_id']} / {s['phase']} / 配对PnL {s['pair_pnl']}"
                                        for s in snapshot["sessions"][-12:]))
                    publish(root, {"snapshot.json": (dumps(payload) + "\n").encode(), "summary.md": text.encode()},
                            {"schema_version": 1, "kind": "interval", "window_start": boundary - 300,
                             "window_end": boundary, "observed_at": now,
                             "cutoff_semantics": "observed_at snapshot; boundary names scheduling interval only"})
                self.last_boundary = boundary
                self.collector.db.execute("INSERT OR REPLACE INTO metadata VALUES('last_boundary',?)", (str(boundary),))
                self.collector.db.commit()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        result = {"last_boundary": self.last_boundary, "next_boundary": boundary + 300,
                  "error": self.last_error, "revised_markets": revised}
        snapshot["archive"] = result
        return result
