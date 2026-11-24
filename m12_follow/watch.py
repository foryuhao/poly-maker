"""Five-minute local, read-only inspection of the independent M12 follower."""
import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager, closing
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

import httpx
from polymaker.m4_control import atomic_json
from polymaker.maker_public import VirtualPublicAPI, validate_resolution
from m12_follow.sqlite_source import market_source

PROJECT = Path(__file__).resolve().parents[1]
STATE = PROJECT / "research_outputs/m12_follow_watch"
LIVE = PROJECT / "m12_follow/framework/research_outputs/m4_live"
UNIT = "polymaker-m12-follow.service"
BJ = ZoneInfo("Asia/Shanghai")
D = Decimal
GROUPS = ("intents", "received", "posts", "with_fill", "satisfied", "full",
          "dust", "partial", "unfilled", "pending", "unknown_orders")
TABLE_HEADER = "| 场次（北京时间） | 准入 INTENT | POST | 确认有成交 | 最终满足（足额 / 尾差） | 部分成交未满足 | 待核对 | 未成交 |"


def stamp(ts):
    return datetime.fromtimestamp(ts, BJ).isoformat(timespec="seconds")


def read(path, default=None):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return default


@contextmanager
def database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.3)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db


def baseline(run, slug):
    directory = run / slug
    sealed = read(directory / "market-report.json")
    if sealed:
        summary, entries = sealed["strategies"]["M12"]
        if sealed["market"] != slug or summary["market"] != slug:
            raise ValueError("unexpected archived M12 source")
        rows = {i["candidate_id"]: {"anchor": i["price"]} for i in entries}
        if len(rows) != int(summary["admitted_orders"]):
            raise ValueError("sealed M12 admission count mismatch")
        complete = summary["status"] == "SEALED" and summary["complete"] and sealed["complete_window"]
        return rows, "SEALED" if complete else "INCOMPLETE"
    path = directory / "M12/simulation.sqlite3"
    if not path.is_file():
        return {}, "SOURCE_NOT_AVAILABLE"
    with database(path) as db:
        meta = {k: json.loads(v) for k, v in db.execute(
            "SELECT name,payload FROM metadata WHERE name IN ('identity','run_owner')")}
        if meta["identity"]["market"] != slug or meta["run_owner"]["path"] != str(run):
            raise ValueError("unexpected baseline ledger identity")
        rows = {}
        for (raw,) in db.execute("SELECT payload FROM audit WHERE kind='INTENT' ORDER BY seq"):
            data = json.loads(raw)
            if data["scenario"] == "sop/anchor":
                i = data["intent"]
                if (i["market"], i["strategy"], i["side"], i["order_type"]) != (
                        slug, "M12", "BUY", "ASSUMED_FIXED_ANCHOR"):
                    raise ValueError("unexpected admission")
                rows[i["opportunity"]] = {"anchor": i["price"], "outcome": i["outcome"]}
        return rows, "OPEN"


def execution(path, run_name):
    if not path.exists():
        return {}
    with database(path) as db:
        admissions = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM facts WHERE kind='m12_follow_admission'")]
        rows = {r["id"]: dict(r) for r in db.execute("SELECT id,state,reason,reserved FROM opportunities")}
        attempts = defaultdict(list)
        for a in db.execute("SELECT task_id,state,started FROM taker_attempts"):
            attempts[a["task_id"]].append(a)
        fills = defaultdict(list)
        for key, raw in db.execute(
            "SELECT a.task_id,t.economics FROM taker_trade_evidence t "
            "JOIN taker_attempts a ON a.order_id=t.order_id WHERE t.status='CONFIRMED'"):
            fills[key].append(json.loads(raw))
        targets = {k.removeprefix("budget_target:"): D(json.loads(v)) for k, v in db.execute(
            "SELECT key,value FROM metadata WHERE key LIKE 'budget_target:%'")}
        latest = max((a["audit_seq"] for a in admissions), default=None)
        result = {}
        for a in admissions:
            if a["source_run"] != run_name or a["baseline_id"] in result:
                raise ValueError("foreign or ambiguous follower admission")
            key = a["id"]
            row = rows.get(key, {})
            orders = attempts[key]
            trades = fills[key]
            principal = sum((D(f["principal_usdc"]) for f in trades), D(0))
            fees = sum((D(f["fee_usdc"]) for f in trades), D(0))
            shares = sum((D(str(f["size"])) for f in trades), D(0))
            if trades and key not in targets:
                raise ValueError("confirmed fill without saved principal target")
            if any(f["our_side"] != "BUY" or f["token_id"] != a["token"] for f in trades):
                raise ValueError("confirmed fill does not match admitted BUY token")
            state = row.get("state", "SUPERSEDED" if a["audit_seq"] != latest else "NOT_SUBMITTED")
            result[a["baseline_id"]] = {
                "received": True, "outcome": a["outcome"], "token": a["token"], "anchor": a["anchor"], "state": state,
                "reason": row.get("reason") or state, "target": str(targets[key]) if key in targets else None,
                "principal": str(principal), "all_in": str(principal + fees), "shares": str(shares),
                "posts": sum(bool(o["started"]) and o["state"] != "UNSENT" for o in orders),
                "unknown_orders": sum(o["state"] == "UNKNOWN" for o in orders),
                "open_order": any(o["state"] not in ("TERMINAL", "UNSENT") for o in orders)
                or D(row.get("reserved", "0")) > 0,
            }
        return result


def classify(row, cutoff):
    if row.get("open_order"):
        return "pending"
    principal = D(row.get("principal", "0"))
    if principal > 0:
        remaining = D(row["target"]) - principal
        if remaining <= 0:
            return "full"
        # Same cent rounding and strict minimum as the follower's principal budget.
        if remaining.quantize(D(".01"), rounding=ROUND_FLOOR) <= 1:
            return "dust"
        if cutoff or row.get("state") in ("SKIPPED", "CUTOFF", "SUPERSEDED"):
            return "partial"
        return "pending"
    if cutoff or row.get("state") in ("SKIPPED", "CUTOFF", "SUPERSEDED", "REJECTED"):
        return "unfilled"
    return "pending"


def source_signature(directory):
    result = []
    for file in ("market-report.json", "M12/simulation.sqlite3", "M12/simulation.sqlite3-wal"):
        try:
            stat = (directory / file).stat()
        except FileNotFoundError:
            continue
        result.append([file, stat.st_ino, stat.st_size, stat.st_mtime_ns])
        if file == "market-report.json":
            break
    return result


def market(run, epoch, now, old=None):
    slug = f"btc-updown-5m-{epoch}"
    old = old or {}
    owner = market_source(run, slug, archived=True)
    signature = source_signature(owner / slug)
    if (old.get("source_signature") == signature and old.get("source_directory") == str(owner)
            and not old.get("error")):
        source = {key: {k: item[k] for k in ("anchor","outcome") if k in item}
                  for key, item in old["intents"].items() if item["in_source"]}
        status = old["source"].removesuffix("_RECEIPTS_OUTSIDE_SOURCE")
    else:
        source, status = baseline(owner, slug)
    live = execution(LIVE / "m421-ledgers" / (slug + ".sqlite3"), run.name)
    if live.keys() - source.keys():
        # Local durable receipts can survive a source rotation. Keep the observed floor visible.
        status += "_RECEIPTS_OUTSIDE_SOURCE"
    counts = dict.fromkeys(GROUPS, 0)
    detail = {}
    for key in source.keys() | live.keys():
        item = {**source.get(key, {}), **live.get(key, {})}
        item["in_source"] = key in source
        outcome = classify(item, now >= epoch + 240)
        item["result"] = outcome
        if key not in live:
            item["reason"] = "BASELINE_ADMITTED_NO_FOLLOWER_RECEIPT"
        counts["intents"] += 1
        counts["received"] += key in live
        counts["with_fill"] += D(item.get("shares", "0")) > 0
        counts["posts"] += item.get("posts", 0)
        counts["unknown_orders"] += item.get("unknown_orders", 0)
        counts["satisfied"] += outcome in ("dust", "full")
        counts[outcome] += 1
        detail[key] = item
    return {"epoch": epoch, "start": stamp(epoch), "source": status, "source_directory": str(owner),
            "source_signature": signature,
            "counts": counts, "intents": detail, "resolution": old.get("resolution")}


def settlement_identity(slug, intents):
    with database(LIVE / "m421-ledgers" / (slug + ".sqlite3")) as db:
        meta = json.loads(db.execute("SELECT value FROM metadata WHERE key='market'").fetchone()[0])
    sides = {"up": "YES", "down": "NO", "yes": "YES", "no": "NO"}
    tokens = {sides[t["outcome"].lower()]: t["token_id"] for t in meta["tokens"]}
    if (meta["slug"] != slug or set(tokens) != {"YES", "NO"} or len(set(tokens.values())) != 2
            or any(i["token"] != tokens[i["outcome"]] for i in intents if D(i.get("shares", "0")) > 0)):
        raise ValueError("settlement identity does not match confirmed position")
    return {"slug": slug, "condition_id": meta["condition_id"], "token_ids": tokens}


async def settle_closed_hours(run, rows, hour_start):
    # Resolve only hours due for reporting. Cache final proofs with their market.
    due = [r for e, r in rows.items() if e < hour_start and r.get("counts", {}).get("with_fill")
           and not r.get("resolution")]
    if not due:
        return
    api = VirtualPublicAPI()
    limit = asyncio.Semaphore(4)

    async def resolve(row):
        slug = f"btc-updown-5m-{row['epoch']}"
        try:
            identity = settlement_identity(slug, row["intents"].values())
            path = market_source(run, slug, archived=True) / slug / "M12/simulation.sqlite3"
            proof = None
            if path.is_file():
                with database(path) as db:
                    record = db.execute("SELECT payload FROM audit WHERE kind='OFFICIAL_RESULT' "
                                        "ORDER BY seq DESC LIMIT 1").fetchone()
                if record:
                    proof = validate_resolution(json.loads(record[0])["raw"], identity)
            if not proof:
                async with limit:
                    proof = await api.resolution(identity)
            row["resolution"] = proof
            row.pop("settlement_error", None)
        except (sqlite3.Error, OSError, httpx.HTTPError, TypeError, IndexError, KeyError, ValueError) as exc:
            row["settlement_error"] = type(exc).__name__ + ": " + str(exc)

    try:
        await asyncio.gather(*(resolve(row) for row in due))
    finally:
        await api.client.aclose()


def hourly(hour, rows):
    totals = Counter()
    unverified = []
    gaps = []
    principal = fees = payout = pending_cost = D(0)
    wins = losses = pending_fills = 0
    pending_markets = []
    for epoch in range(hour, hour + 3600, 300):
        row = rows.get(epoch, {})
        totals.update(row.get("counts", {}))
        if row.get("source") != "SEALED" or row.get("error"):
            unverified.append(epoch)
        if row.get("source") not in ("SEALED", "OPEN") or row.get("error"):
            gaps.append(epoch)
        proof = row.get("resolution")
        for i in row.get("intents", {}).values():
            shares = D(i.get("shares", "0"))
            if shares <= 0:
                continue
            cost = D(i["all_in"])
            if not proof:
                pending_fills += 1
                pending_cost += cost
                if epoch not in pending_markets:
                    pending_markets.append(epoch)
                continue
            if i["token"] != proof["token_ids"][i["outcome"]]:
                raise ValueError("resolved position token mismatch")
            principal += D(i["principal"])
            fees += cost - D(i["principal"])
            if i["outcome"] == proof["winner"]:
                payout += shares
                wins += 1
            else:
                losses += 1
    rate = round(100*totals["satisfied"]/totals["intents"], 2) if totals["intents"] else None
    net = payout - principal - fees
    pnl = {"settled_net_usdc": str(net) if wins + losses or not (pending_fills or gaps or totals["pending"]) else None,
           "payout_usdc": str(payout), "settled_principal_usdc": str(principal),
           "settled_fees_usdc": str(fees), "won_intents": wins, "lost_intents": losses,
           "awaiting_resolution_intents": pending_fills, "awaiting_resolution_cost_usdc": str(pending_cost),
           "awaiting_resolution_markets": pending_markets}
    return {"from": stamp(hour), "until_exclusive": stamp(hour + 3600),
            "counts": {k: totals[k] for k in GROUPS}, "satisfied_pct_of_observed": rate,
            "not_sealed_or_missing": unverified, "coverage_gaps": gaps, "pnl": pnl}


def coverage_table(periods):
    lines = [TABLE_HEADER, "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for label, row in periods:
        c = row.get("counts", {}) if row else {}
        incomplete = not row or row.get("error") or row.get("coverage_gaps")
        if row and "source" in row and row["source"] not in ("OPEN", "SEALED"):
            incomplete = True
        def number(key):
            if key not in c or (incomplete and not c[key]):
                return "待核实"
            return ("≥" if incomplete else "") + str(c[key])
        cells = [label, number("intents"), number("posts"), number("with_fill"),
                 f'{number("satisfied")}（{number("full")} / {number("dust")}）',
                 number("partial"), number("pending"), number("unfilled")]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def hour_notice_key(summary):
    # A real coverage gap affects confidence even when the trading figures are identical.
    fields = {key: summary.get(key) for key in ("counts", "pnl", "coverage_gaps")}
    return "v2:" + digest(fields)


def update_hour(path, summary, delivered):
    """Save sealing progress locally and compare chat updates against the last delivery."""
    previous = read(path)
    last_key = delivered.get(path.stem)
    if last_key and not last_key.startswith("v2:"):
        # Old state hashes the entire delivered summary. A preview or failed delivery
        # may have advanced the hour file, so only migrate from a verified match.
        if previous is not None and digest(previous) == last_key:
            last_key = hour_notice_key(previous)
        else:
            for report_path in sorted((path.parent.parent / "reports").glob("*.json"), reverse=True):
                entries = read(report_path).get("hourly", [])
                for entry in entries:
                    if entry.get("from") == summary["from"]:
                        old = {k: v for k, v in entry.items() if k != "revision"}
                        if digest(old) == last_key:
                            last_key = hour_notice_key(old)
                            break
                if last_key.startswith("v2:"):
                    break
    if summary != previous:
        atomic_json(path, summary)
    key = hour_notice_key(summary)
    delivered[path.stem] = key
    if key != last_key:
        return {**summary, "revision": last_key is not None}


def resources(previous):
    props = ("ActiveState", "MainPID", "NRestarts", "ControlGroup", "InvocationID")
    cmd = ["systemctl", "--user", "show", UNIT] + ["--property=" + p for p in props]
    unit = dict(line.split("=", 1) for line in subprocess.check_output(cmd, text=True, timeout=8).splitlines())
    now = time.time()
    sample = {"at": now, "disk_free_bytes": shutil.disk_usage(LIVE).free, **unit}
    group = unit["ControlGroup"]
    if not group or int(unit["MainPID"]) <= 0:
        return sample
    cg = Path("/sys/fs/cgroup") / group.lstrip("/")
    def pairs(path):
        return dict(line.replace(":", "").split() for line in path.read_text().splitlines())
    try:
        sample["cpu_usec"] = int(pairs(cg / "cpu.stat")["usage_usec"])
        sample["memory_bytes"] = int((cg / "memory.current").read_text())
        io = pairs(Path("/proc") / unit["MainPID"] / "io")
        for name in ("read_bytes", "write_bytes"):
            sample[name] = int(io[name])
        sockets = subprocess.check_output(["ss", "-tinHeO", "state", "established"], text=True, timeout=5)
        sample["tcp"] = {}
        for line in sockets.splitlines():
            if ("cgroup:" + group) not in line.split():
                continue
            fields = dict(re.findall(r"(ino|bytes_sent|bytes_received):(\d+)", line))
            if "ino" in fields:
                sample["tcp"][fields["ino"]] = {k: int(fields.get(k, 0)) for k in ("bytes_sent","bytes_received")}
        if previous.get("InvocationID") == unit["InvocationID"] and "tcp" in previous:
            elapsed = now - previous["at"]
            if elapsed > 0:
                sample["window_seconds"] = round(elapsed, 1)
                sample["avg_cpu_pct_one_core"] = round((sample["cpu_usec"]-previous["cpu_usec"])/elapsed/10000, 2)
                for k in ("read_bytes", "write_bytes"):
                    sample["delta_" + k] = sample[k] - previous[k]
                for k in ("bytes_sent","bytes_received"):
                    sample["tcp_observed_" + k] = sum(max(0, item[k] - previous["tcp"].get(ino, {}).get(k, 0))
                                                       for ino, item in sample["tcp"].items())
    except (OSError, KeyError, subprocess.SubprocessError, ValueError) as exc:
        sample["sample_error"] = type(exc).__name__
    return sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-wake", action="store_true")
    args = parser.parse_args()
    with (STATE / "watch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        control = read(STATE / "control.json")
        if not control["enabled"]:
            return
        now = int(time.time())
        bucket = now // 300 * 300
        state = read(STATE / "watch-state.json", {})
        if not args.no_wake and state.get("queued") == bucket:
            return
        first = int(control["service_started_epoch"]) // 3600 * 3600
        last = state.get("last_market", first)
        epochs = set(range(max(first, last - 300), bucket + 1, 300)) | set(state.get("recheck", []))
        if state.get("hourly_pnl_version") != 1:
            epochs.update(range(first, bucket + 1, 300))
        recheck, rows = [], {}
        prior_rows = {}
        this_hour = bucket // 3600 * 3600
        run = Path(control["source"])
        for epoch in sorted(epochs):
            path = STATE / "markets" / f"{epoch}.json"
            old = read(path, {})
            prior_rows[epoch] = old
            try:
                row = market(run, epoch, now, old)
            except (sqlite3.Error, OSError, KeyError, ValueError, ArithmeticError) as exc:
                row = {**old, "epoch": epoch, "error": type(exc).__name__ + ": " + str(exc)}
            rows[epoch] = row
        asyncio.run(settle_closed_hours(run, rows, this_hour))
        for epoch, row in rows.items():
            if row != prior_rows[epoch]:
                atomic_json(STATE / "markets" / f"{epoch}.json", row)
            counts = row.get("counts", {})
            if (row.get("source") not in ("SEALED", "INCOMPLETE") or row.get("error") or counts.get("pending")
                    or (counts.get("with_fill") and not row.get("resolution"))):
                recheck.append(epoch)
        next_hour = state.get("next_hour", first)
        hours = set(range(next_hour, this_hour, 3600)) | {e//3600*3600 for e in epochs if e < this_hour}
        delivered = state.get("delivered_hours", {}).copy()
        summaries = []
        for h in sorted(hours):
            cohort = {e: read(STATE / "markets" / f"{e}.json", {}) for e in range(h, h+3600, 300)}
            summary = hourly(h, cohort)
            period = f'{stamp(h)[:16].replace("T", " ")}–{stamp(h+3600)[11:16]}'
            summary["table"] = coverage_table([(period, summary)])
            path = STATE / "hours" / f"{h}.json"
            notice = update_hour(path, summary, delivered)
            if notice is not None:
                summaries.append(notice)
        hb_path = LIVE / "m421-latest.json"
        hb = read(hb_path, {})
        health = resources(state.get("resources", {}))
        report = {"observed_at": stamp(now), "service_start": stamp(control["service_started_epoch"]),
                  "market_table": coverage_table([(stamp(e)[11:16], rows.get(e)) for e in (bucket-300, bucket)]),
                  "resources": health, "heartbeat": {k: hb.get(k) for k in (
                      "slug", "state", "reason", "account_reason", "signal_failure", "market_input_health")},
                  "heartbeat_age_s": round(time.time()-hb_path.stat().st_mtime,1) if hb_path.exists() else None,
                  "startup": read(LIVE / "startup-status.json", {}), "previous_market": rows.get(bucket-300),
                  "current_market": rows[bucket], "hourly": summaries,
                  "recheck_markets": recheck, "read_errors": {e:r["error"] for e,r in rows.items() if "error" in r},
                  "stop_markers": sorted(p.name for p in (PROJECT/"m12_follow/framework/runtime/trading-control").glob("*stop*.json"))}
        path = STATE / "reports" / (datetime.fromtimestamp(now,BJ).strftime("%Y%m%dT%H%M%S") + ".json")
        atomic_json(path, report)
        atomic_json(STATE / "latest.json", report)
        if args.no_wake:
            print(path)
            return
        prompt = ("M12 独立实盘跟随器五分钟巡检。用户已授权在本聊天汇报。读取本轮快照和逐场记录，"
                  "先报刚结束场和当前场的基线已准入 INTENT、最终满足、已确认有成交、部分、待核对、未成交及原因。"
                  "满足=确认成交且本金用足或余款已不足再下最小单，拆出足额及尾差。ACK 不算成交。"
                  "hourly 非空时逐个报告北京时间上一自然小时及迟到修订，按五分钟市场归属、INTENT 去重，"
                  "逐场直接展示 market_table，每个小时及迟到修订直接展示其 table，保留相同表头和列序。"
                  "逐小时在表下报满足率、仍待确认、数据覆盖及 pnl 的已结算净 PnL（USDC）、本金、手续费、"
                  "兑付及待官方结算的成交数和含费成本。PnL 仅含本跟随器 CONFIRMED 成交，"
                  "部分成交也按实际数量计，归属市场开场小时；无可核实净值报待核实。首小时标明跨试跑与持续运行。"
                  "来源缺失或读取失败时写明可核实下限，不将未知报作零信号。零分母不报百分比。"
                  "留意心跳、CPU、磁盘、TCP采样下限。只对明确可复现的跟随器BUG保存证据、及时修复、离线验证；"
                  "不动基线及原实盘，不造试单，不清风控。停机标记优先，遵守最新人工指令。"
                  f" 本轮文件 {path} 。")
        result = subprocess.run([str(Path.home()/".local/bin/codex"), "queue", "--thread", control["thread"],
                                 "--message", prompt, "--resume"], cwd=PROJECT, timeout=45,
                                text=True, capture_output=True, check=True)
        atomic_json(STATE / "watch-state.json", {"queued": bucket, "last_market": bucket, "hourly_pnl_version": 1,
                    "resources": health, "recheck": recheck, "next_hour": this_hour, "delivered_hours": delivered,
                    "report": str(path), "resume_unconfirmed": "warning" in (result.stdout+result.stderr).lower()})
        print(path)


if __name__ == "__main__":
    main()
