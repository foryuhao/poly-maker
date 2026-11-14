"""Read-only evaluation gate: known real posts cannot be stripped from L2.

This gate is NOT a signal input. It conservatively excludes the whole shared
binary market from primary research statistics, preserving the diagnostic audit.
"""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def own_market_gate(project, market):
    path = Path(project) / "research_outputs/m4_live/ledgers" / (market + ".sqlite3")
    base = {
        "scope": "KNOWN_LOCAL_M4_LIVE_POSTS",
        "source": str(path),
        "checked_at": time.time(),
        "granularity": "WHOLE_BINARY_MARKET",
        "signal_input": False,
    }
    if not path.exists():
        return {**base, "status": "ELIGIBLE", "reason": "NO_KNOWN_LOCAL_M4_LEDGER"}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            identity = json.loads(
                db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]
            )
            if identity.get("mode") != "LIVE":
                raise ValueError("OWN_LEDGER_MODE_UNVERIFIED")
            posts = db.execute(
                "SELECT token,SUM(posts),MAX(started) FROM opportunities GROUP BY token"
            ).fetchall()
        affected = [{"token": t, "posts": n, "started": s} for t, n, s in posts if n or s]
        return {
            **base,
            "status": "SELF_CONTAMINATED" if affected else "ELIGIBLE",
            "reason": "REAL_POST_CANNOT_BE_STRIPPED_FROM_AGGREGATE_L2"
            if affected
            else "NO_KNOWN_LOCAL_M4_POST",
            "post_evidence": affected,
        }
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        return {**base, "status": "UNAVAILABLE", "reason": type(error).__name__ + ":" + str(error)}


def read_gate(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        row = db.execute(
            "SELECT payload FROM metadata WHERE name='research_sample_eligibility'"
        ).fetchone()
    return (
        json.loads(row[0])
        if row
        else {"status": "UNAVAILABLE", "reason": "OWN_ACTIVITY_NOT_REVIEWED"}
    )
