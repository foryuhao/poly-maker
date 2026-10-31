"""Seal and replay a past public-feed prefix without stopping the collector."""

import gzip
import hashlib
import json
from contextlib import nullcontext

from live_gzip import complete_lines
from study import Book


def seal_and_replay(wsroot, folder, trigger_time, asset, budget=7.01, cap=0.69):
    prefix = folder / "poly_ws_prefix.jsonl.gz"
    coverage_path = folder / "poly_ws_prefix_coverage.json"
    if not coverage_path.exists():
        original_hash = hashlib.sha256()
        last_received = None
        after_boundary = None
        source_records = 0
        with (
            (
                gzip.open(prefix, "wt", compresslevel=1)
                if not prefix.exists()
                else nullcontext(None)
            ) as dst,
            (
                (wsroot / "raw.jsonl").open()
                if (wsroot / "raw.jsonl").exists()
                else nullcontext(complete_lines(wsroot / "raw.jsonl.gz"))
            ) as src,
        ):
            for line in src:
                r = json.loads(line)
                if r["received"] > trigger_time + 10:
                    after_boundary = r["received"]
                    break
                original_hash.update(line.encode())
                source_records += 1
                last_received = r["received"]
                if dst is not None:
                    dst.write(line)
        archived_hash = hashlib.sha256()
        with gzip.open(prefix, "rb") as archived:
            for block in iter(lambda: archived.read(2**20), b""):
                archived_hash.update(block)
        assert archived_hash.hexdigest() == original_hash.hexdigest(), "source prefix changed"
        coverage_path.write_text(
            json.dumps(
                dict(
                    through=trigger_time + 10 if after_boundary is not None else last_received,
                    observed_next_received=after_boundary,
                    source_records=source_records,
                    plaintext_sha256=original_hash.hexdigest(),
                ),
                indent=2,
            )
        )
    coverage = json.loads(coverage_path.read_text())
    b = Book()
    generation = None
    dirty = False
    regressed = False
    source = 0.0
    last_update = None
    pending = [trigger_time, trigger_time + 1.5]
    samples = []
    first_unavailable = None
    errors = []
    lines = 0

    def available():
        remaining = budget
        for p, q in sorted(b.asks.items()):
            if p > cap + 1e-9:
                break
            remaining -= min(remaining, p * q)
            if remaining <= 1e-8:
                return True
        return False

    def snapshot(t):
        return dict(
            target=t,
            generation=generation,
            valid=b.valid,
            dirty=dirty,
            source_regressed=regressed,
            last_book_update_received=last_update,
            book_receipt_age=t - last_update if last_update is not None else None,
            quote=b.quote(),
            cash_budget_available=available(),
            asks_under_cap=[(p, q) for p, q in sorted(b.asks.items()) if p <= cap + 1e-9],
        )

    with gzip.open(prefix, "rt") as f:
        for line in f:
            r = json.loads(line)
            lines += 1
            now = r["received"]
            while pending and pending[0] < now:
                samples.append(snapshot(pending.pop(0)))
            if r.get("generation") != generation:
                b = Book()
                generation = r.get("generation")
                dirty = False
                regressed = False
                source = 0.0
            if r.get("error") and trigger_time - 1 <= now <= trigger_time + 3:
                errors.append(r)
            e = r.get("event", {})
            kind = e.get("event_type")
            if kind == "book" and e.get("asset_id") == asset:
                b.update(e)
                dirty = False
                regressed = False
                source = float(e.get("timestamp", 0))
                last_update = now
            elif kind == "price_change":
                changes = [c for c in e.get("price_changes", []) if c.get("asset_id") == asset]
                for c in changes:
                    b.update(dict(c, event_type=kind))
                if changes:
                    last_update = now
                    c = changes[-1]
                    q = b.quote()
                    ts = float(e.get("timestamp", 0))
                    regressed |= ts < source
                    source = max(source, ts)
                    dirty = bool(
                        q
                        and (
                            abs(q["bid"] - float(c.get("best_bid", q["bid"]))) > 1e-8
                            or abs(q["ask"] - float(c.get("best_ask", q["ask"]))) > 1e-8
                        )
                    )
            if (
                trigger_time <= now <= trigger_time + 5
                and first_unavailable is None
                and b.valid
                and not available()
            ):
                first_unavailable = dict(
                    received=now, source=e.get("timestamp"), line=lines, quote=b.quote()
                )
    while pending and coverage["through"] is not None and pending[0] <= coverage["through"]:
        samples.append(snapshot(pending.pop(0)))
    result = dict(
        prefix=str(prefix),
        sha256=hashlib.sha256(prefix.read_bytes()).hexdigest(),
        lines=lines,
        coverage=coverage,
        samples=samples,
        first_budget_unavailable=first_unavailable,
        errors=errors,
    )
    (folder / "sealed_ws_replay.json").write_text(json.dumps(result, indent=2))
    return result
