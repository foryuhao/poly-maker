"""Durable virtual instructions and official-resolution accounting; no wallet."""

import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from polymaker.rdp_sm_ca import plain
from polymaker.rdp_sm_m1_policy import BASELINE_MAX_INSTRUCTIONS, baseline_quantity

MODEL = "SIGNAL_PRICE_FULL_FILL_HOLD_TO_OFFICIAL_RESOLUTION"


class VirtualStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory/"state.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS markets(
            slug TEXT PRIMARY KEY,epoch INTEGER NOT NULL,metadata TEXT NOT NULL,
            state TEXT NOT NULL,full_window INTEGER NOT NULL DEFAULT 0,
            issues INTEGER NOT NULL DEFAULT 0,counts TEXT NOT NULL DEFAULT '{}',
            winner TEXT,resolution TEXT,pnl TEXT,resolution_error TEXT);
          CREATE TABLE IF NOT EXISTS instructions(
            id TEXT PRIMARY KEY,slug TEXT NOT NULL,rf TEXT NOT NULL,ex TEXT NOT NULL,
            outcome TEXT NOT NULL,price TEXT NOT NULL,quantity TEXT NOT NULL,cost TEXT NOT NULL,
            eligible INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT,
            wall REAL NOT NULL,elapsed REAL NOT NULL,evidence TEXT NOT NULL,pnl TEXT,
            UNIQUE(slug,rf),UNIQUE(slug,ex));
          CREATE TABLE IF NOT EXISTS timeline(
            seq INTEGER PRIMARY KEY,slug TEXT,wall REAL NOT NULL,kind TEXT NOT NULL,data TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS virtual_timeline_market_time ON timeline(slug,wall,seq);
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(plain(value))))

    def event(self, kind, data, slug=None, *, wall=None):
        with self.db:
            self.db.execute("INSERT INTO timeline(slug,wall,kind,data) VALUES(?,?,?,?)",
                (slug, time.time() if wall is None else wall, kind, json.dumps(plain(data))))

    def observation_batch(self, events, incidents):
        """One disk commit per drain, never one fsync per public input frame."""
        with self.db:
            self.db.executemany("INSERT INTO timeline(slug,wall,kind,data) VALUES(?,?,?,?)",
                [(slug, wall, kind, json.dumps(plain(data))) for kind, data, slug, wall in events])
            reset = self.get("reset_through_epoch", 0)
            for kind, reason, slug, wall in incidents:
                self.db.execute("UPDATE markets SET issues=issues+1 WHERE slug=?", (slug,))
                reset = max(reset, int(slug.rsplit("-", 1)[1]))
                self.db.execute("INSERT INTO timeline(slug,wall,kind,data) VALUES(?,?,?,?)",
                    (slug, wall, kind, json.dumps({"reason": reason, "streak_reset": True})))
            if incidents:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('reset_through_epoch',?)", (json.dumps(reset),))

    def incident(self, kind, reason, slug=None):
        with self.db:
            if slug:
                self.db.execute("UPDATE markets SET issues=issues+1 WHERE slug=?", (slug,))
                epoch = int(slug.rsplit("-", 1)[1])
            else:
                epoch = int(time.time()//300)*300
            self.set("reset_through_epoch", max(epoch, self.get("reset_through_epoch", 0)))
            self.event(kind, {"reason": str(reason)[:500], "streak_reset": True}, slug)

    def boot(self, fingerprint):
        previous = self.get("fingerprint")
        active = self.db.execute("SELECT slug FROM markets WHERE state='ACTIVE'").fetchall()
        for row in active:
            self.incident("PROCESS_RECOVERY", "Prior market observation interrupted", row[0])
            with self.db:
                self.db.execute("UPDATE markets SET state='INTERRUPTED' WHERE slug=?", (row[0],))
        if previous is not None and previous != fingerprint:
            self.set("reset_through_epoch", int(time.time()//300)*300)
            self.event("DEPLOYMENT_RESET", {"previous": previous, "current": fingerprint})
        self.set("fingerprint", fingerprint)
        self.set("boots", self.get("boots", 0)+1)
        if self.get("started_at") is None:
            self.set("started_at", time.time())
        self.event("SERVICE_BOOT", {"observation_only": True, "real_post_enabled": False, "model": MODEL})

    def add_market(self, meta, elapsed):
        slug, epoch = meta["slug"], int(meta["slug"].rsplit("-", 1)[1])
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO markets(slug,epoch,metadata,state) VALUES(?,?,?,'ACTIVE')",
                            (slug, epoch, json.dumps(plain(meta))))
            self.db.execute("UPDATE markets SET state='ACTIVE' WHERE slug=?", (slug,))
        self.event("MARKET_STARTED", {"elapsed": elapsed, "metadata": meta}, slug)

    def instructions(self, slug):
        return [dict(r) for r in self.db.execute("SELECT * FROM instructions WHERE slug=? ORDER BY wall", (slug,))]

    def issue(self, opportunity, *, price, quantity, eligible, reason, elapsed, wall=None,
              execution_id=None, duplicate_of=None):
        opportunity = dict(opportunity)
        slug = opportunity.pop("slug")
        root_id = opportunity["episode_id"]
        rf, ex = opportunity["rf_id"], opportunity["ex_id"]
        if duplicate_of is not None:
            if not execution_id or duplicate_of != root_id:
                raise ValueError("VIRTUAL_DUPLICATE_RETRY_IDENTITY")
            identity = root_id+":retry:"+execution_id
            if self.db.execute("SELECT 1 FROM instructions WHERE id=?", (identity,)).fetchone():
                return None
            parent = self.db.execute("SELECT * FROM instructions WHERE id=?", (root_id,)).fetchone()
            children = [r for r in self.instructions(slug) if json.loads(r["evidence"]).get("duplicate_of") == root_id]
            if (parent is None or parent["slug"] != slug or parent["rf"] != rf or parent["ex"] != ex
                    or parent["status"] != "VIRTUAL_SUCCESS" or children):
                raise ValueError("VIRTUAL_DUPLICATE_RETRY_NOT_AUTHORIZED")
            opportunity.update(episode_id=identity, source_opportunity_id=root_id,
                               duplicate_of=root_id, execution_id=execution_id)
            # DB uniqueness identifies physical instructions. Original RF/EX
            # evidence stays intact; the retry does not create a new signal.
            rf, ex = rf+":retry:"+execution_id, ex+":retry:"+execution_id
        elif self.db.execute("SELECT 1 FROM instructions WHERE slug=? AND (rf=? OR ex=?)",
                             (slug, rf, ex)).fetchone():
            return None
        quantity, price = Decimal(str(quantity)), Decimal(str(price))
        cost = price*quantity
        posted = [r for r in self.instructions(slug) if r["status"] == "VIRTUAL_SUCCESS"]
        used = len({json.loads(r["evidence"]).get("source_opportunity_id", r["id"]) for r in posted})
        if eligible and duplicate_of is None and used >= BASELINE_MAX_INSTRUCTIONS:
            reason = "SESSION_INSTRUCTION_LIMIT"
        if eligible and sum((Decimal(r["cost"]) for r in posted), Decimal(0))+cost > 20:
            reason = "M1_SESSION_BUDGET"
        success = eligible and reason is None
        status = "VIRTUAL_SUCCESS" if success else "SKIPPED"
        wall = time.time() if wall is None else wall
        row = {"id": opportunity["episode_id"], "slug": slug, "outcome": opportunity["outcome"],
            "price": str(price), "quantity": str(quantity), "cost": str(cost), "status": status,
            "reason": reason, "eligible": bool(eligible), "wall": wall, "elapsed": elapsed,
            "simulation": True, "real_post": False, "post_only": True, "side": "BUY", "model": MODEL}
        if success and quantity != baseline_quantity(price, 5):
            raise ValueError("VIRTUAL_QUOTE_EXCEEDS_CONTRACT")
        with self.db:
            self.db.execute("INSERT INTO instructions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (row["id"], slug, rf, ex, row["outcome"],
                 str(price), str(quantity), str(cost), int(eligible), status, reason, wall,
                 elapsed, json.dumps(plain(opportunity))))
        self.event("UNCERTAIN_RETRY_INSTRUCTION" if duplicate_of else "SIGNAL_IDENTIFIED", row, slug, wall=wall)
        if success:
            self.event("VIRTUAL_OPEN_INSTRUCTION", row, slug, wall=wall)
            self.event("VIRTUAL_SUCCESS", {**row, "fill_assumption": "FULL_FILL_AT_INSTRUCTION_PRICE"}, slug)
        else:
            self.event("SIGNAL_SKIPPED", row, slug, wall=wall)
        return row

    def finish_market(self, slug, *, full_window, counts, interrupted=False):
        with self.db:
            self.db.execute("UPDATE markets SET state=?,full_window=?,counts=? WHERE slug=?",
                ("INTERRUPTED" if interrupted else "CLOSED", int(full_window), json.dumps(counts), slug))
        self.event("MARKET_CLOSED", {"full_window": full_window, "counts": counts, "interrupted": interrupted}, slug)

    def pending_resolutions(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM markets WHERE state!='ACTIVE' AND winner IS NULL ORDER BY epoch")]

    def settle(self, slug, resolution):
        winner = resolution["winner"]
        if winner not in {"YES", "NO"}:
            raise ValueError("VIRTUAL_INVALID_WINNER")
        total = Decimal(0)
        with self.db:
            prior = self.db.execute("SELECT winner FROM markets WHERE slug=?", (slug,)).fetchone()
            if prior is None or prior[0] not in (None, winner):
                raise ValueError("VIRTUAL_RESOLUTION_CONFLICT")
            for row in self.instructions(slug):
                if row["status"] != "VIRTUAL_SUCCESS":
                    continue
                pnl = (Decimal(row["quantity"]) if row["outcome"] == winner else Decimal(0))-Decimal(row["cost"])
                total += pnl
                self.db.execute("UPDATE instructions SET pnl=? WHERE id=?", (str(pnl), row["id"]))
            self.db.execute("UPDATE markets SET winner=?,resolution=?,pnl=?,resolution_error=NULL WHERE slug=?",
                            (winner, json.dumps(resolution), str(total), slug))
        self.event("OFFICIAL_RESOLUTION", {"winner": winner, "pnl": str(total), "evidence": resolution}, slug)

    @classmethod
    def read_report(cls, directory):
        """Independent read transaction for the report worker; no shared thread state."""
        reader = cls.__new__(cls)
        reader.db = sqlite3.connect((Path(directory)/"state.sqlite3").as_uri()+"?mode=ro", uri=True)
        reader.db.row_factory = sqlite3.Row
        try:
            reader.db.execute("BEGIN")
            return reader.report()
        finally:
            reader.db.close()

    def report(self, *, details=True):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM markets ORDER BY epoch")]
        reset = self.get("reset_through_epoch", 0)
        stable = []
        for row in rows:
            if row["epoch"] <= reset:
                continue
            if row["state"] == "ACTIVE":
                break
            if row["state"] != "CLOSED" or not row["full_window"] or row["issues"]:
                stable = []
            else:
                if stable and row["epoch"] != stable[-1]["epoch"]+300:
                    stable = []
                stable.append(row)
        settled_streak = []
        for row in stable:
            if row["winner"] is None:
                break
            settled_streak.append(row)
        result = {"mode": "VIRTUAL_ONLY", "real_posts": 0, "model": MODEL,
            "settings": self.get("settings"),
            "boots": self.get("boots", 0), "started_at": self.get("started_at"),
            "continuous_normal_markets": len(stable), "consecutive_complete_reports": len(settled_streak),
            "required_consecutive_reports": 12, "awaiting_user_confirmation": len(settled_streak) >= 12,
            "stage_complete": False, "reset_through_epoch": reset,
            "settled_pnl": str(sum((Decimal(r["pnl"]) for r in rows if r["pnl"] is not None), Decimal(0))),
            "market_count": len(rows), "markets": []}
        for row in rows[-48:] if details else []:
            row["metadata"] = json.loads(row["metadata"])
            row["counts"] = json.loads(row["counts"])
            row["resolution"] = json.loads(row["resolution"]) if row["resolution"] else None
            row["instructions"] = self.instructions(row["slug"])
            row["timeline"] = [dict(r) for r in self.db.execute(
                "SELECT wall,kind,data FROM timeline WHERE slug=? AND kind NOT IN ('INPUT_REJECTED','SIGNAL_INVALIDATED') ORDER BY wall,seq", (row["slug"],))]
            for event in row["timeline"]:
                event["data"] = json.loads(event["data"])
            result["markets"].append(row)
        return result

    def close(self):
        self.db.close()
