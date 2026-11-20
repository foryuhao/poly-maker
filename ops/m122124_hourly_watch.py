"""Report the M12/M21/M24 anchor-only baseline to its authorized chat."""

import argparse
import asyncio
import fcntl
import json
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from m1223_settlement_closeout import canonical_identity, sha256
from polymaker.m6_baseline.control import atomic_json
from polymaker.maker_public import VirtualPublicAPI, validate_resolution


PROJECT = Path(__file__).resolve().parents[1]
STATE = PROJECT / "research_outputs/m122124_hourly_watch"
STRATEGIES = ("M12", "M21", "M24")
BEIJING = ZoneInfo("Asia/Shanghai")
GAMMA = "https://gamma-api.polymarket.com/markets"


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def identity(directory, ledger, copy):
    with copy.open("wb") as target:
        subprocess.run(["zstd", "-qdc", "--", str(directory / ledger["file"])], stdout=target, check=True)
    if sha256(copy) != ledger["sqlite_sha256"]:
        raise ValueError(f"Ledger checksum mismatch in {directory}")
    return canonical_identity(copy)


async def public_results(identities):
    if not identities:
        return {}, {}
    api = VirtualPublicAPI()
    found, errors = {}, Counter()
    limit = asyncio.Semaphore(6)

    async def batch(slugs):
        try:
            params = [("closed", "true"), ("limit", str(len(slugs)))]
            params.extend(("slug", slug) for slug in slugs)
            response = await api.client.get(GAMMA, params=params)
            response.raise_for_status()
            for market in response.json():
                slug = market["slug"]
                if slug in slugs:
                    proof = validate_resolution(market, identities[slug])
                    if proof:
                        found[slug] = proof
        except Exception as exc:
            errors[type(exc).__name__] += 1

    async def exact(slug):
        async with limit:
            try:
                proof = await api.resolution(identities[slug])
                if proof:
                    found[slug] = proof
            except Exception as exc:
                errors[type(exc).__name__] += 1

    try:
        slugs = list(identities)
        await asyncio.gather(*(batch(slugs[i:i + 12]) for i in range(0, len(slugs), 12)))
        await asyncio.gather(*(exact(slug) for slug in slugs if slug not in found))
    finally:
        await api.client.aclose()
    return found, dict(errors)


def collect(run, previous, public, now):
    plan = read(run / "plan.json")["market_epochs"]
    signals, orders = Counter(), {label: [] for label in STRATEGIES}
    sealed, unsealed, incomplete = [], [], []
    sources, official = {}, {}
    completion = read(run / "completion.json")
    last_observed = max((int(p.name.rsplit("-", 1)[-1]) for p in run.glob("btc-updown-5m-*")), default=None)
    for epoch in plan:
        if epoch + 300 > now.timestamp() or (completion and last_observed is not None and epoch > last_observed):
            break
        slug = f"btc-updown-5m-{epoch}"
        directory = run / slug
        report = read(directory / "market-report.json")
        if not report:
            unsealed.append(epoch)
            continue
        stats = report["strategies"]
        if not report["complete_window"] or any(
            not stats[label][0]["complete"] or stats[label][0]["status"] != "SEALED" for label in STRATEGIES
        ):
            incomplete.append(epoch)
            continue
        sealed.append(epoch)
        for label in STRATEGIES:
            summary, entries = stats[label]
            signal_ids = set(summary["signal_ids"])
            if len(signal_ids) != summary["signal_count"] or len(entries) != summary["assumed_orders"]:
                raise ValueError(f"Ledger counts disagree for {slug} {label}")
            if any(item["market"] != slug or item["candidate_id"] not in signal_ids for item in entries):
                raise ValueError(f"Ledger signal identity disagrees for {slug} {label}")
            signals[label] += len(signal_ids)
            orders[label].extend(entries)
            if entries:
                sources.setdefault(slug, (directory, report["ledgers"][label]))
                if report["official_winner"]:
                    official[slug] = report["official_winner"]

    with tempfile.TemporaryDirectory() as temp:
        identities = {}
        for slug in sources.keys() - official.keys() - public.keys():
            ledger_copy = Path(temp) / f"{slug}.sqlite3"
            identities[slug] = identity(*sources[slug], ledger_copy)
            if identities[slug]["slug"] != slug:
                raise ValueError(f"Ledger market identity disagrees for {slug}")
        checked, errors = asyncio.run(public_results(identities))
    public.update(checked)

    rows = {}
    for label, entries in orders.items():
        if len({item["order_id"] for item in entries}) != len(entries):
            raise ValueError(f"Duplicate order in {label}")
        pnl, wins, losses, pending = Decimal(0), 0, 0, 0
        for item in entries:
            slug = item["market"]
            quantity, price = Decimal(item["quantity"]), Decimal(item["price"])
            if quantity != Decimal(item["assumed_filled_quantity"]) or not (quantity > 0 and 0 < price < 1):
                raise ValueError(f"Invalid anchor order in {slug} {label}")
            proof = public.get(slug)
            if slug in official:
                if item["official_winner"] != official[slug] or (proof and proof["winner"] != official[slug]):
                    raise ValueError(f"Official result conflict in {slug}")
                amount = Decimal(item["assumed_gross_pnl"])
            elif proof:
                tokens = proof["token_ids"]
                if item["token"] not in tokens.values():
                    raise ValueError(f"Settlement token mismatch in {slug}")
                amount = quantity * (int(item["token"] == tokens[proof["winner"]]) - price)
            else:
                pending += 1
                continue
            pnl += amount
            wins += amount > 0
            losses += amount < 0
        old_pnl = previous.get("pnl", {}).get(label)
        rows[label] = {
            "signals": signals[label], "trades": len(entries), "wins": wins,
            "losses": losses, "pending": pending, "pnl": str(pnl),
            "delta": str(pnl - Decimal(old_pnl)) if old_pnl is not None else None,
        }

    return {
        "snapshot": now.isoformat(), "hour": now.strftime("%Y%m%dT%H"),
        "start_epoch": plan[0], "sealed_through_epoch": sealed[-1] + 300 if sealed else None,
        "sealed_markets": len(sealed), "unsealed_markets": unsealed, "incomplete_markets": incomplete,
        "previous_snapshot": previous.get("snapshot"), "rows": rows, "public_read_errors": errors,
        "run_finished": bool(completion),
        "final": bool(completion) and not any(row["pending"] for row in rows.values()),
    }


def amount(value):
    if value is None:
        return "—"
    return format(Decimal(value), "+.3f").replace("-", "−")


def timestamp(epoch):
    return datetime.fromtimestamp(epoch, BEIJING).strftime("%m 月 %d 日 %H:%M")


def render(report, path):
    now = datetime.fromisoformat(report["snapshot"])
    start = timestamp(report["start_epoch"])
    stop = report["sealed_through_epoch"]
    horizon = f"{start} 至 {timestamp(stop)}" if stop else f"{start} 起"
    status = "本轮基线已结束" if report["run_finished"] else "本轮基线统计"
    lines = [
        f"截至 **{now.strftime('%m 月 %d 日 %H:%M')}（北京时间）**，{status}覆盖 "
        f"**{horizon} 的 {report['sealed_markets']} 个共同完整、已封账市场**。",
        "",
        "| 策略 | 信号数 | 锚价假设成交 | 盈利 / 亏损笔数 | 已结算毛 PnL（USDC） | 较上次变化 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, row in report["rows"].items():
        lines.append(
            f"| {label} | {row['signals']} | {row['trades']} | {row['wins']} / {row['losses']} "
            f"| {amount(row['pnl'])} | {amount(row['delta'])} |"
        )
    pending = sum(row["pending"] for row in report["rows"].values())
    settled = sum(row["trades"] - row["pending"] for row in report["rows"].values())
    notes = [f"已按官方结果结算 **{settled} 笔**。毛 PnL 未扣手续费和滑点。"]
    if pending:
        pending_by_strategy = "、".join(
            f"{label} {row['pending']} 笔" for label, row in report["rows"].items() if row["pending"]
        )
        notes.append(f"待官方结算 {pending_by_strategy}，结算后计入 PnL。")
    for field, label in [("unsealed_markets", "待封账"), ("incomplete_markets", "采集不完整")]:
        if report[field]:
            notes.append(f"{label} {len(report[field])} 场，单列于巡检记录。")
    if report["final"]:
        notes.append("本轮基线汇报收尾。")
    return "\n".join([*lines, "", " ".join(notes), "", f"[本轮巡检记录]({path})"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-wake", action="store_true", help="Build a snapshot without posting to the chat")
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "watch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        control = read(STATE / "control.json")
        previous = read(STATE / "last-queued.json")
        now = datetime.now(BEIJING)
        if not control.get("enabled") or previous.get("final"):
            return
        if not args.no_wake and previous.get("hour") == now.strftime("%Y%m%dT%H"):
            return
        run = Path(control["run_directory"])
        cached = read(STATE / "public-settlements.json")
        report = collect(run, previous or control["previous_report"], cached, now)
        report["run_directory"] = str(run)
        report_path = STATE / "reports" / f"{now.strftime('%Y%m%dT%H%M%S%f')}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(STATE / "public-settlements.json", cached)
        atomic_json(report_path, report)
        atomic_json(STATE / "latest.json", report)
        message = render(report, report_path)
        print(message, flush=True)
        current_control = read(STATE / "control.json")
        if args.no_wake or not current_control.get("enabled") or current_control["thread"] != control["thread"]:
            return
        prompt = (
            "M12、M21、M24 三策略基线每小时汇报。用户授权在本聊天汇报。"
            "请在本聊天按下面已经核对的表格和说明回复，保留数字和口径。遵守最新人工指令。\n\n"
            + message
        )
        result = subprocess.run(
            [str(Path.home() / ".local/bin/codex"), "queue", "--thread", control["thread"],
             "--message", prompt, "--resume"],
            cwd=PROJECT, capture_output=True, text=True, timeout=45, check=True,
        )
        atomic_json(STATE / "last-queued.json", {
            "snapshot": report["snapshot"], "hour": report["hour"], "final": report["final"],
            "report_path": str(report_path),
            "resume_unconfirmed": "warning" in (result.stdout + result.stderr).lower(),
            "pnl": {label: row["pnl"] for label, row in report["rows"].items()},
        })


if __name__ == "__main__":
    main()
