"""Keep capture intact; skip legacy RAM-heavy optional analysis after files close."""

import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--pid", type=int, required=True)
    a = p.parse_args()
    root = a.root
    collection = root / "collection.json"
    status = "WAITING_FOR_CLOSED_CAPTURE"
    while True:
        try:
            data = json.loads(collection.read_text())
            if "counts" in data and "duration_requested" in data:
                break
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            os.kill(a.pid, 0)
        except ProcessLookupError:
            (root / "completion_guard.json").write_text(
                json.dumps(
                    dict(
                        status="PROCESS_TERMINAL_BEFORE_COLLECTION", checked=time.time(), pid=a.pid
                    ),
                    indent=2,
                )
            )
            return
        time.sleep(0.5)
    # collect() closes all raw/quote/trade handles before writing collection.json.
    proc = Path(f"/proc/{a.pid}/cmdline")
    if proc.exists():
        args = proc.read_bytes().decode().split("\0")
        if (
            "research/microstructure_discovery/study.py" in args
            and "--output" in args
            and args[args.index("--output") + 1] == str(root)
        ):
            os.kill(a.pid, signal.SIGTERM)
            status = "CAPTURE_COMPLETE_LEGACY_OPTIONAL_ANALYSIS_SKIPPED"
        else:
            status = "PID_ARGUMENT_MISMATCH_NOT_SIGNALED"
    else:
        status = "CAPTURE_PROCESS_ALREADY_EXITED"
    files = {}
    for name in (
        "discovery_raw.json",
        "markets.json",
        "raw.jsonl",
        "quotes.jsonl",
        "trades.jsonl",
        "collection.json",
        "fees_provenance.json",
    ):
        path = root / name
        if not path.exists():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(8 * 1024 * 1024):
                digest.update(chunk)
        files[name] = dict(bytes=path.stat().st_size, sha256=digest.hexdigest())
    (root / "stream_manifest.json").write_text(
        json.dumps(
            dict(
                created_utc=dt.datetime.now(dt.UTC).isoformat(),
                files=files,
                hash_method="streamed_8MiB_chunks",
            ),
            indent=2,
        )
    )
    (root / "completion_guard.json").write_text(
        json.dumps(
            dict(
                status=status,
                checked=time.time(),
                pid=a.pid,
                reason="Shared host has~2GiB RAM; raw evidence is closed and complete. Legacy all-quotes-in-memory exploratory analysis is unnecessary for frozen streaming stage evaluators. Preserve immutable source modules and avoid disturbing live trading with optional analysis memory pressure.",
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
