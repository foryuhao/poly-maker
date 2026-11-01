"""Offline M1 timing audit and controlled public-frame replay; no execution I/O.

Replay retains recorded receive/processing times, frame order and pending-input
watermarks. It uses a 25ms maintenance timer and constant time inside a frame;
it is a diagnostic comparison, not exact reconstruction of live scheduling.
"""

import argparse
import gzip
import hashlib
import importlib.util
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


def quantiles(values):
    values = sorted(values)
    return {str(q): round(values[min(len(values)-1, int((len(values)-1)*q))], 3)
            for q in (0, .5, .95, .99, 1)} if values else {}


def audit(path):
    frames, counts, reasons, ap_sides, stable = [], Counter(), Counter(), Counter(), Counter()
    decisions, regressions, previous = Counter(), Counter(), {}
    delays, source_ages, rollbacks = [], [], []
    digest = hashlib.sha256()
    size = path.stat().st_size
    compressed = path.suffix == ".gz"
    with (gzip.open(path, "rb") if compressed else path.open("rb")) as stream:
        while compressed or stream.tell() < size:
            line = stream.readline() if compressed else stream.readline(size-stream.tell())
            digest.update(line)
            if not line.endswith(b"\n"):
                break
            event = json.loads(line)
            kind = event.get("event")
            counts[kind] += 1
            if kind in {"UNAVAILABLE", "SIGNAL_INVALIDATED", "EVENT_IGNORED"}:
                reasons[kind+":"+str(event.get("reason"))] += 1
            if kind == "AP_CONFIRMED":
                ap_sides[event["side"]] += 1
            if kind == "ROUND_CLOSED":
                stable[str(event["stable"])] += 1
            if kind == "EVENT_DECISION":
                decisions[str((event.get("kind"), event.get("accepted"), event.get("reason")))] += 1
            if kind != "RAW_FRAME":
                continue
            frames.append(event)
            delays.append((event["processed_monotonic"]-event["received_monotonic"])*1000)
            raw = event["raw"]
            if raw in ("PONG", "PING"):
                continue
            rows = json.loads(raw) if isinstance(raw, str) else raw
            for row in rows if isinstance(rows, list) else [rows]:
                if "timestamp" not in row:
                    continue
                stamp = float(row["timestamp"])
                source_ages.append(event["received_wall"]*1000-stamp)
                tokens = ({ch["asset_id"] for ch in row["price_changes"]}
                          if row["event_type"] == "price_change" else {row["asset_id"]})
                for token in tokens:
                    old = previous.get(token)
                    if old and stamp < old[0]:
                        regressions[old[1]+" -> "+row["event_type"]] += 1
                        rollbacks.append(old[0]-stamp)
                    elif not old or stamp >= old[0]:
                        previous[token] = stamp, row["event_type"]
    return frames, {
        "path": str(path), "bytes": size, "sha256": digest.hexdigest(),
        "sha256_scope": "UNCOMPRESSED_JSONL" if compressed else "FILE_PREFIX",
        "recorded_counts": dict(counts), "recorded_reasons": dict(reasons),
        "recorded_ap_sides": dict(ap_sides), "recorded_closed_rounds": dict(stable),
        "recorded_decisions": dict(decisions), "receive_to_process_ms": quantiles(delays),
        "frames_over_200ms": sum(d > 200 for d in delays),
        "source_age_at_receive_ms": quantiles(source_ages),
        "source_regressions": dict(regressions), "source_rollback_ms": quantiles(rollbacks),
    }


def load_source(name, path):
    import sys
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def replay(frames, detector_source=None, feed_source=None, signal_config=None, start_sec=100):
    from polymaker.config import RDPM1SignalConfig, RDPObiConfig
    from polymaker.rdp_sm_marketdata import SessionFeed

    if detector_source:
        module = load_source("m1_audit_baseline", detector_source)
    if feed_source:
        SessionFeed = load_source("m1_audit_feed", feed_source).SessionFeed
    initial = json.loads(frames[0]["raw"])
    initial = initial if isinstance(initial, list) else [initial]
    tokens = list(dict.fromkeys(r["asset_id"] for r in initial if r["event_type"] == "book"))
    if len(tokens) != 2:
        raise ValueError("Replay requires an initial two-token snapshot")
    meta = SimpleNamespace(condition_id=initial[0]["market"], tick_size=".01",
                           yes=SimpleNamespace(token_id=tokens[0]), no=SimpleNamespace(token_id=tokens[1]))
    clock = SimpleNamespace(now=frames[0]["received_monotonic"])
    offset = frames[0]["received_wall"]-clock.now
    feed = SessionFeed(meta, RDPObiConfig(), monotonic=lambda: clock.now,
                       wall=lambda: offset+clock.now, normal_entry_mode="m1_v1")
    feed.verify_ticks({t: ".01" for t in tokens})
    feed.connect()
    if detector_source:
        feed.m1 = module.M1Signals(feed)
    elif signal_config is not None:
        from polymaker.rdp_sm_m1_signal import M1Signals

        feed.m1 = M1Signals(feed, signal_config=RDPM1SignalConfig(**signal_config))
    parameters = getattr(feed.m1, "cfg", RDPM1SignalConfig())
    feed.m1_pending_received.extend(e["received_monotonic"] for e in frames)
    next_tick = clock.now+.025
    opportunities, ap_sides, reasons, stable = set(), Counter(), Counter(), Counter()
    executable, executable_keys = [], set()
    event_counts = Counter()
    ap_failures, round_failures, interruptions = Counter(), Counter(), Counter()
    latest_frame = 0

    def capture(row):
        event_counts[row["event"]] += 1
        if row["event"] == "AP_CONFIRMED":
            ap_sides[row["side"]] += 1
        if row["event"] == "ROUND_CLOSED":
            stable[str(row["stable"])] += 1
        if row["event"] == "AP_NOT_CONFIRMED":
            volume, total = Decimal(row["volume"]), Decimal(row["total_volume"])
            checks = {"volume_below_minimum": volume < Decimal(row["minimum"]),
                      "insufficient_prints": row["records"] < parameters.ap_min_records,
                      "fewer_than_2_bins": len(row["occupied_subwindows"]) < 2,
                      "no_last_bin_trade": 2 not in row["occupied_subwindows"],
                      "share_below_threshold": not total or volume/total < parameters.ap_min_share,
                      "single_print_dominates": row["concentration"] is not None
                          and Decimal(row["concentration"]) > parameters.ap_max_concentration}
            ap_failures.update(row["side"]+":"+key for key, failed in checks.items() if failed)
        if row["event"] == "ROUND_NOT_QUALIFIED":
            minimum = max(parameters.min_round_shares,
                          Decimal(row["initial"])*parameters.min_round_depth_fraction)
            if Decimal(row["hit"]) < minimum:
                round_failures["insufficient_sell_volume"] += 1
            if Decimal(row["initial"])-Decimal(row["trough"]) < minimum:
                round_failures["insufficient_visible_depletion"] += 1
        if row["event"] == "RF_INVALIDATED":
            interruptions["ANCHOR_CHANGED:completed_rounds="+str(row["completed_rounds"])] += 1
        if row["event"] == "SIGNAL_INVALIDATED":
            for state in row.get("interrupted", []):
                interruptions[row["reason"]+":completed_rounds="+str(state["completed_rounds"])] += 1
        if row["event"] in {"SIGNAL_INVALIDATED", "UNAVAILABLE", "RF_INVALIDATED"}:
            reasons[row.get("reason", "?")] += 1
        return True

    feed.m1.record = capture
    for index, frame in enumerate(frames):
        if frame["generation"] != feed.generation:
            feed.connect()
            feed.m1_pending_received.extend(e["received_monotonic"] for e in frames[index:])
        while next_tick < frame["processed_monotonic"]:
            clock.now = next_tick
            feed.m1.evaluate(feed.snapshot())
            opportunities.update(feed.m1.opportunities)
            ep = feed.m1.selected
            epoch = int(frames[0]["received_wall"]//300)*300
            elapsed = offset+clock.now-epoch
            if (ep is not None and ep.episode_id not in executable_keys and start_sec <= elapsed < 240
                    and ep.order_deadline-clock.now >= .450):
                executable_keys.add(ep.episode_id)
                executable.append({"episode_id": ep.episode_id, "elapsed_s": elapsed,
                    "remaining_ms": (ep.order_deadline-clock.now)*1000,
                    "token_id": ep.token_id, "rf_id": ep.rf_id, "ex_id": ep.ex_id})
            next_tick += .025
        clock.now = frame["processed_monotonic"]
        latest_frame = frame["frame"]
        try:
            feed.apply(frame["raw"], received_mono=frame["received_monotonic"],
                       received_wall=frame["received_wall"])
        except ValueError as exc:
            raise ValueError(f"Replay failed at frame {latest_frame}") from exc
        feed.m1_pending_received.popleft()
    return {"counts": dict(event_counts), "ap_sides": dict(ap_sides), "reasons": dict(reasons),
            "closed_rounds": dict(stable), "unique_opportunity_pairs": len(opportunities),
            "ap_failed_checks_nonexclusive": dict(ap_failures),
            "round_failed_checks_nonexclusive": dict(round_failures),
            "refill_interruptions": dict(interruptions),
            "parameters": parameters.model_dump(mode="json"),
            "observation_window": {"start_sec": start_sec, "cutoff_sec": 240},
            "offline_candidates_with_450ms_budget": executable,
            "last_frame": latest_frame,
            "schedule": "Recorded processing starts + 25ms maintenance; constant intraframe clock"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--detector-source", type=Path)
    parser.add_argument("--feed-source", type=Path)
    parser.add_argument("--signal-config", type=Path)
    parser.add_argument("--start-sec", type=int, default=100)
    args = parser.parse_args()
    if not 15 <= args.start_sec < 240:
        parser.error("--start-sec must be in [15, 240)")
    results = []
    for path in args.paths:
        frames, result = audit(path)
        if args.replay:
            result["replay"] = replay(frames, args.detector_source, args.feed_source,
                                      json.loads(args.signal_config.read_text()) if args.signal_config else None,
                                      start_sec=args.start_sec)
        results.append(result)
        print(path.parent.name, result.get("replay", {}).get("counts", result["recorded_counts"]), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2)+"\n")


if __name__ == "__main__":
    main()
