"""Read-only, bounded input audit for the order-book signal specification.

Reads public market/signal facts only; never imports the execution engine or
opens credentials, account databases, or network clients. File byte boundaries
and SHA256 hashes make the sampled claims inspectable on a running workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def rows(path: Path, manifest: list[dict], root: Path):
    size = path.stat().st_size
    digest = hashlib.sha256()
    count = 0
    partial = False
    with path.open("rb") as stream:
        while stream.tell() < size:
            line = stream.readline(size - stream.tell())
            digest.update(line)
            if not line.endswith(b"\n"):
                partial = True
                break
            count += 1
            yield json.loads(line)
    manifest.append({"path": str(path.relative_to(root)), "bytes": size,
                     "sha256": digest.hexdigest(), "rows": count,
                     "partial_last_line": partial})


def ended(path: Path) -> bool:
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 262144))
        return b'"kind": "sm_run_result"' in stream.read()


def inventory(root: Path, relative: str) -> dict:
    files = [p for p in (root / relative).rglob("*") if p.is_file()]
    return {"path": relative, "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "file_names": dict(Counter(p.name for p in files))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frozen", type=Path,
                        help="Prior evidence JSON: reuse its sampled session IDs")
    args = parser.parse_args()
    root = args.root.resolve()
    journals = sorted((root / "journal").glob("rdp_sm_*.jsonl"),
                      key=lambda p: (p.stat().st_mtime_ns, p.name))
    completed = [p for p in journals if ended(p)]
    if args.frozen:
        wanted = set(json.loads(args.frozen.read_text())["sample_session_ids"])
        selected = [p for p in journals if p.stem.rsplit("_", 1)[-1] in wanted]
        if len(selected) != len(wanted):
            raise ValueError("Frozen sample is no longer available")
    else:
        if not completed:
            raise ValueError("No ended strategy sessions found")
        indices = sorted({round(i * (len(completed) - 1) / 5) for i in range(6)})
        selected = [completed[i] for i in indices]
    manifest = []
    ca_index = {}
    for path in sorted((root / "research_outputs/ca_observe").glob("*/events.jsonl")):
        with path.open() as stream:
            first = stream.readline()
        if first:
            sid = json.loads(first).get("session_id")
            if sid in ca_index:
                raise ValueError("Multiple CA files reference the same strategy session")
            ca_index[sid] = path
    result = {
        "asof_beijing": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": "Six stratified completed strategy sessions; not all historical sessions",
        "selection": "Evenly spaced by journal file mtime among ended sessions at audit start",
        "completed_journals_at_selection": len(completed),
        "sample_session_ids": [], "sessions": [], "manifest": manifest,
        "inventory": [inventory(root, p) for p in (
            "research_outputs/ca_observe", "research_outputs/periodic_sync")],
    }
    for path in selected:
        sid = path.stem.rsplit("_", 1)[-1]
        result["sample_session_ids"].append(sid)
        kinds = Counter()
        ba_events = Counter()
        ba_ids = set()
        header = {}
        times = []
        for record in rows(path, manifest, root):
            kind = record.get("kind")
            kinds[kind] += 1
            data = record.get("data", {}).get("payload", {})
            if kind == "sm_session":
                header = data
            if isinstance(record.get("ts"), (int, float)):
                times.append(record["ts"])
            # The opposite detector often observes the SAME underlying evidence.
            # Keep it separate; never pool it as another independent episode.
            if kind == "sm_ba_episode":
                ba_events[data.get("event")] += 1
                ba_ids.add(data.get("episode_id"))
        session = {
            "session_id": sid, "slug": header.get("slug"),
            "start_wall": min(times) if times else None,
            "end_wall": max(times) if times else None,
            "journal_kinds": dict(kinds),
            "raw_market_rows": {k: kinds[k] for k in (
                "book", "price_change", "last_trade_price", "BOOK", "DELTA", "TRADE")},
            "ba_events": dict(ba_events), "ba_episode_count": len(ba_ids),
        }
        ca_path = ca_index.get(sid)
        if ca_path:
            events = Counter()
            facts = {}
            ca_ids = set()
            sequence_errors = 0
            previous = 0
            summary = None
            for record in rows(ca_path, manifest, root):
                events[record.get("event")] += 1
                sequence_errors += record["sequence"] != previous + 1
                previous = record["sequence"]
                payload = record.get("payload", {})
                if record.get("event") == "CA_SUMMARY":
                    summary = payload
                ep = payload.get("episode", {})
                if ep:
                    ca_ids.add(ep["episode_id"])
                for key in ("first_sell", "threshold_sell", "confirming_buy"):
                    fact = ep.get(key)
                    if fact:
                        facts[(ep["token_id"], ep["generation"], fact["ordinal"])] = fact
            counts = summary.get("counts", {}) if summary else {}
            session["ca"] = {
                "path": str(ca_path.relative_to(root)), "events": dict(events),
                "episode_count": len(ca_ids), "sequence_errors": sequence_errors,
                "has_summary": summary is not None,
                "detector_trade_events_counter": counts.get("trade_events"),
                "counter_caveat": "Counts detector processing, not a certified unique complete tape",
                "selected_unique_trade_facts": len(facts),
                "selected_trade_sides": dict(Counter(f["side"] for f in facts.values())),
                "writer_failure": summary.get("writer", {}).get("failure") if summary else None,
                "writer_dropped": summary.get("writer", {}).get("dropped") if summary else None,
            }
        sync = root / "research_outputs/periodic_sync" / sid
        sync_counts = Counter()
        if (sync / "events.jsonl").is_file():
            for record in rows(sync / "events.jsonl", manifest, root):
                sync_counts[record.get("kind")] += 1
        session["sync_event_kinds"] = dict(sync_counts)
        snapshots = sync / "snapshots.jsonl"
        if snapshots.is_file():
            shapes = Counter()
            market_rows = book_rows = 0
            for record in rows(snapshots, manifest, root):
                shapes[tuple(sorted(record))] += 1
                market_rows += bool(record.get("market"))
                book_rows += any(k in record for k in ("bids", "asks", "books", "yes_book", "no_book"))
            session["sync_snapshot_shapes"] = [
                {"keys": list(keys), "rows": n} for keys, n in shapes.items()]
            session["sync_snapshot_market_rows"] = market_rows
            session["sync_snapshot_top_level_book_rows"] = book_rows
        result["sessions"].append(session)
    source_paths = (
        "src/polymaker/rdp_sm_marketdata.py", "src/polymaker/rdp_sm_ca.py",
        "src/polymaker/rdp_sm_ba.py", "src/polymaker/marketdata/service.py",
        "src/polymaker/research/schema.py", "src/polymaker/research/capture.py",
        "src/polymaker/rdp_sm_sync_companion.py", "config/config.toml",
    )
    result["source_sha256"] = {
        p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in source_paths}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"sessions": len(result["sessions"]),
                      "files_audited": len(manifest),
                      "out": str(args.out.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
