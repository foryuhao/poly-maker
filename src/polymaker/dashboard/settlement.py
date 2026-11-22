"""Public Gamma resolution reads; no account credentials or trading SDK."""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from polymaker.dashboard.model import summarize
from polymaker.maker_public import BASE, validate_resolution as validate_resolution


def fetch_resolution(session):
    import httpx

    slug = session["slug"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", slug):
        raise ValueError("SETTLEMENT_INVALID_SLUG")
    response = httpx.get(BASE + slug, timeout=3, follow_redirects=False)
    response.raise_for_status()
    return validate_resolution(response.json(), session)


class SettlementObserver:
    """One background GET at a time; retry pending/error markets every 30s."""

    def __init__(self, collector, fetch=fetch_resolution):
        self.collector, self.fetch = collector, fetch
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dashboard-resolution")
        self.future = self.session = None
        self.retry_at = {}

    def tick(self, snapshot):
        c = self.collector
        if self.future is not None and self.future.done():
            sid = self.session["session_id"]
            try:
                result = self.future.result()
                payload = {"result": result, "error": None}
            except Exception as exc:
                payload = {"result": None, "error": type(exc).__name__ + ": " + str(exc)[:200]}
            c.db.execute("INSERT OR REPLACE INTO settlements VALUES(?,?)", (sid, json.dumps(payload)))
            summary = summarize(sid, c.events(sid), c.sources(sid))
            c.db.execute("UPDATE summaries SET snapshot=? WHERE sid=?", (json.dumps(summary), sid))
            c.db.commit()
            self.retry_at[sid] = time.monotonic() + 30
            self.future = self.session = None
            snapshot["sessions"] = c.snapshots(since=snapshot["observed_at"]-c.history_hours*3600)
        if self.future is None:
            for s in snapshot["sessions"]:
                if (s["ended"] and s["residual"] and s["residual_pnl"] is None
                        and s.get("market_end") is not None
                        and s["market_end"] <= snapshot["observed_at"]
                        and all(src.get("caught_up") for src in s["sources"])
                        and self.retry_at.get(s["session_id"], 0) <= time.monotonic()):
                    self.session = s
                    self.future = self.pool.submit(self.fetch, s)
                    break

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
