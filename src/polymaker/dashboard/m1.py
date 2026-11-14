"""Read-only adapters for M1's public simulation ledger and native risk logs."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from polymaker.dashboard.model import PNL_ATTRIBUTION_VERSION, Record

RUNTIME_REPORT_TAIL = re.compile(rb'^  "observed_at"\s*:', re.MULTILINE)


def runtime_report(path: Path) -> Record:
    # write_report appends these top-level keys after the large market history.
    # Read the small final fragment; retain compatibility with older layouts.
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - 64 * 1024))
        tail = stream.read()
    matches = list(RUNTIME_REPORT_TAIL.finditer(tail))
    if matches:
        value = json.loads(b"{" + tail[matches[-1].start():])
        if "runtime" in value:
            return value
    return json.loads(path.read_text())


def real_session(s: Record, events: list[Record] | None = None) -> Record:
    from polymaker.dashboard.m1_native import summarize_native

    return summarize_native(s, events or [])


class VirtualReader:
    def __init__(self, project: Path):
        self.root = project / "research_outputs/m1_virtual"
        self.cache: dict[str, tuple[object, Record]] = {}
        self.runtime_stamp = None
        self.producer: Record = {}
        self.error = None
        self.updated_ids: set[str] = set()
        self.timeline_seq = 0
        self.market_sequences: dict[str, int | None] = {}
        self.source_identity: tuple[int, int] | None = None

    def read(self, now: float, hours: float) -> list[Record]:
        self.updated_ids.clear()
        path = self.root / "state.sqlite3"
        if not path.exists():
            if not self.cache:
                return []
            self.error = "M1 基线数据源暂不可用"
            self.updated_ids.update(s["session_id"] for _, s in self.cache.values())
            return [{**s, "issues": [*s["issues"], "M1_SOURCE_UNAVAILABLE"]} for _, s in self.cache.values()]
        recovering = self.error is not None
        try:
            report = self.root / "latest.json"
            stamp = report.stat().st_mtime_ns if report.exists() else None
            if stamp != self.runtime_stamp:
                snapshot = runtime_report(report) if stamp is not None else {}
                self.producer = {key: snapshot.get(key) for key in ("observed_at", "runtime")}
                self.producer["observed_at"] = self.producer["observed_at"] or 0
                self.runtime_stamp = stamp
            with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.1)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")
                markets = db.execute("SELECT * FROM markets WHERE epoch>=? ORDER BY epoch",
                                     (now-hours*3600,)).fetchall()
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                latest_seq = db.execute("SELECT max(seq) FROM timeline").fetchone()[0] or 0
                reset = identity != self.source_identity or latest_seq < self.timeline_seq
                sequences = {} if reset else dict(self.market_sequences)
                # The producer's timeline is append-only. Follow its primary
                # key rather than re-scanning every market on each refresh.
                for slug, seq in db.execute("SELECT slug,max(seq) FROM timeline NOT INDEXED WHERE seq>? GROUP BY slug",
                                            (0 if reset else self.timeline_seq,)):
                    sequences[slug] = seq
                sessions = []
                for m in markets:
                    if m["slug"] not in sequences:
                        # A wider history window can reintroduce older markets.
                        sequences[m["slug"]] = db.execute("SELECT max(seq) FROM timeline WHERE slug=?", (m["slug"],)).fetchone()[0]
                    seq = sequences[m["slug"]]
                    active = m["state"] == "ACTIVE"
                    version = (tuple(m), seq, self.runtime_stamp if active else None,
                               bool(active and now-self.producer.get("observed_at", 0) > 30))
                    old = None if reset else self.cache.get(m["slug"])
                    if old is None or old[0] != version:
                        orders = [dict(r) for r in db.execute(
                            "SELECT * FROM instructions WHERE slug=? ORDER BY wall,id", (m["slug"],))]
                        events = [dict(r) for r in db.execute(
                            "SELECT wall,kind,data FROM timeline WHERE slug=? AND kind IN "
                            "('RF_CONFIRMED','EX_CONFIRMED','SIGNAL_IDENTIFIED','VIRTUAL_SUCCESS',"
                            "'SIGNAL_SKIPPED','OFFICIAL_RESOLUTION','WS_FAILOVER','MARKET_STARTED','MARKET_CLOSED') "
                            "ORDER BY wall DESC,seq DESC LIMIT 12", (m["slug"],))]
                        components = [dict(r) for kind in ("RF_CONFIRMED", "EX_CONFIRMED")
                            for r in db.execute("SELECT wall,kind,data FROM timeline WHERE slug=? AND kind=? "
                                "ORDER BY wall DESC,seq DESC LIMIT 1", (m["slug"], kind))]
                        component_counts = dict(db.execute("SELECT kind,count(*) FROM timeline WHERE slug=? "
                            "AND kind IN ('RF_CONFIRMED','EX_CONFIRMED') GROUP BY kind", (m["slug"],)))
                        self.cache[m["slug"]] = (version, virtual_session(dict(m), orders, events,
                            self.producer, str(path), now, component_events=components,
                            component_counts=component_counts))
                        self.updated_ids.add(self.cache[m["slug"]][1]["session_id"])
                    sessions.append(self.cache[m["slug"]][1])
            keep = {m["slug"] for m in markets}
            self.cache = {k: v for k, v in self.cache.items() if k in keep}
            self.timeline_seq, self.source_identity = latest_seq, identity
            self.market_sequences = {k: v for k, v in sequences.items() if k in keep}
            if recovering:
                self.updated_ids.update(s["session_id"] for s in sessions)
            self.error = None
            return sessions
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            self.error = f"M1 基线数据读取失败：{type(exc).__name__}"
            self.updated_ids.update(s["session_id"] for _, s in self.cache.values())
            return [{**s, "issues": [*s["issues"], "M1_SOURCE_UNAVAILABLE"]} for _, s in self.cache.values()]

    def archive_events(self, sid: str) -> list[Record]:
        epoch = int(sid.removeprefix("m1_virtual_"))
        path = self.root / "state.sqlite3"
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.1)) as db:
            return [{"kind": k, "ts": w, "payload": json.loads(data), "source_kind": "m1_virtual",
                     "post_type": "VIRTUAL", "offset": seq} for seq, w, k, data in db.execute(
                "SELECT seq,wall,kind,data FROM timeline WHERE slug=? AND kind NOT IN "
                "('INPUT_REJECTED','SIGNAL_INVALIDATED') ORDER BY wall,seq", (f"btc-updown-5m-{epoch}",))]


def virtual_session(m: Record, orders: list[Record], events: list[Record], producer: Record,
                    path: str, now: float, *, component_events: list[Record] | None = None,
                    component_counts: Record | None = None) -> Record:
    meta = json.loads(m["metadata"])
    counts = json.loads(m["counts"])
    runtime = producer.get("runtime") or {}
    active = m["state"] == "ACTIVE"
    current = active and runtime.get("slug") == m["slug"]
    if current:
        counts = runtime.get("signal_counts", counts)
    counts = {**counts, **{kind+"_CONFIRMED": max(counts.get(kind+"_CONFIRMED", 0),
        (component_counts or {}).get(kind+"_CONFIRMED", 0)) for kind in ("RF", "EX")}}
    posted = [o for o in orders if o["status"] == "VIRTUAL_SUCCESS"]
    post_counts = {side: sum(o["outcome"] == side for o in posted) for side in ("YES", "NO")}
    inventory = {side: str(sum((Decimal(o["quantity"]) for o in posted if o["outcome"] == side), Decimal(0)))
                 for side in ("YES", "NO")}
    last = max((e["wall"] for e in events), default=float(m["epoch"]))
    if current:
        last = max(last, producer.get("observed_at", 0))
    issues = []
    if m["state"] == "INTERRUPTED":
        issues.append("OBSERVATION_INTERRUPTED")
    if not active and not m["full_window"]:
        issues.append("OBSERVATION_WINDOW_INCOMPLETE")
    if active and now-producer.get("observed_at", 0) > 30:
        issues.append("M1_HEARTBEAT_STALE")
    signals = {}
    for o in orders:
        signals[o["outcome"]] = {"episode_id": o["id"], "state": o["status"], "reason": o["reason"],
                                  "anchor_bid_price": o["price"], "ts": o["wall"]}
    resolution = json.loads(m["resolution"]) if m["resolution"] else None
    token_sides = {token["token_id"]: side for token, side in zip(meta["tokens"], ("YES", "NO"), strict=True)}
    component_latest = {}
    for e in component_events if component_events is not None else events:
        if e["kind"] in {"RF_CONFIRMED", "EX_CONFIRMED"}:
            kind = e["kind"].split("_")[0]
            if e["wall"] >= component_latest.get(kind, {}).get("ts", 0):
                component_latest[kind] = {"ts": e["wall"],
                    "outcome": token_sides.get(json.loads(e["data"]).get("token_id"), "—")}
    header = {"normal_entry_mode": "m1_v1", "slug": m["slug"], "market_id": meta["condition_id"]}
    recent = []
    for e in reversed(events):
        data = json.loads(e["data"])
        detail = data.get("reason") or (f"{data.get('outcome', '')} @ {data.get('price', '')} × {data.get('quantity', '')}"
            if e["kind"] in {"SIGNAL_IDENTIFIED", "VIRTUAL_SUCCESS"} else
            f"{data.get('winner', '')} PnL={data.get('pnl', '')}" if e["kind"] == "OFFICIAL_RESOLUTION" else "")
        recent.append({"ts": e["wall"], "kind": e["kind"], "reason": detail})
    return {"session_id": "m1_virtual_"+str(m["epoch"]), "slug": m["slug"],
        "market_start": m["epoch"], "market_end": m["epoch"]+300,
        "condition_id": meta["condition_id"], "token_ids": {"YES": meta["tokens"][0]["token_id"], "NO": meta["tokens"][1]["token_id"]},
        "strategy": "M1", "post_type": "VIRTUAL", "normal_entry_mode": "m1_v1", "header": header,
        "phase": "M1 / "+(runtime.get("state", "OBSERVING") if current else m["state"]),
        "reason": runtime.get("reason") if current else m.get("resolution_error"),
        "ended": not active, "execution_status": m["state"], "last_event_at": last,
        "sources": [{"path": path, "kind": "m1_virtual", "caught_up": True, "last_ts": last}],
        "issues": issues, "data_warnings": m["issues"], "observation_complete": bool(m["full_window"]),
        "network": "CONNECTED" if current and runtime.get("connected") else "—",
        "signals": {"M1": signals}, "signal_counts": {"M1": len({
            json.loads(o.get("evidence", "{}")).get("source_opportunity_id", o["id"]) for o in orders})},
        "m1_signals": [{"id": o["id"], "outcome": o["outcome"], "price": o["price"],
            "ts": o["wall"], "state": o["status"], "reason": o["reason"]} for o in orders
            if not json.loads(o.get("evidence", "{}")).get("duplicate_of")],
        "m1_components": {"RF": counts.get("RF_CONFIRMED", 0), "EX": counts.get("EX_CONFIRMED", 0)},
        "m1_component_latest": component_latest,
        "eligible_signals": sum(o["eligible"] for o in orders),
        "submitted_orders": 0, "virtual_submitted_orders": len(posted), "real_submitted_orders": 0,
        "post_types": {"VIRTUAL": post_counts, "REAL": {"YES": 0, "NO": 0}}, "post_counts": {"M1": {"YES": 0, "NO": 0}},
        "filled_orders": 0, "simulated_filled_orders": len(posted), "pending_fills": 0, "pending_orders": 0,
        "orders": [{"order_id": o["id"], "role": "M1", "post_type": "VIRTUAL", "status": o["status"],
            "submitted_at": o["wall"], "simulated_cost": o["cost"], "simulated_pnl": o["pnl"],
            "reason": o["reason"], "confirmed_quantity": "0", "simulated_quantity": o["quantity"] if o in posted else "0",
            "quote": {"outcome": o["outcome"], "side": "BUY", "price": o["price"], "size": o["quantity"]}}
            for o in orders], "recent_events": recent[-8:], "m1_progress": {},
        "inventory": {"YES": "0", "NO": "0"}, "delta": "0", "simulated_inventory": inventory,
        "simulated_spend": str(sum((Decimal(o["cost"]) for o in posted), Decimal(0))),
        "simulated_pnl": m["pnl"], "trading_pnl": None, "known_pnl": "0", "residual_pnl": None,
        "settlement": resolution, "settlement_error": m.get("resolution_error"), "other_pending": False,
        "pair_pnl": "0", "pnl_by_role": {"TP": "0", "SL": "0", "OTHER": "0"},
        "pnl_attribution_version": PNL_ATTRIBUTION_VERSION, "pnl_events": [], "pairs": [], "residual": [],
        "paired_quantity": "0", "merged_pairs": "0", "unmerged_pairs": "0", "fees": "0", "rebates": "UNKNOWN",
        "gas": None, "starting_available_capital_usdc": None}
