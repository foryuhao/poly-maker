"""Independent M2 research ledger; all fills here are explicit assumptions."""

import json
import sqlite3
import time
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from polymaker.rdp_sm_ca import plain

MODEL = "FULL_FILL_AT_OBSERVATION_PRICE_HOLD_TO_OFFICIAL_SETTLEMENT"


def quantity_for_risk(price, minimum, risk=Decimal(5)):
    price, minimum, risk = (Decimal(str(v)) for v in (price, minimum, risk))
    if (not all(v.is_finite() for v in (price, minimum, risk))
            or not 0 < price < 1 or minimum <= 0 or risk <= 0):
        raise ValueError("M2_QUOTE_INVALID")
    quantity = (risk/price).quantize(Decimal(".01"), rounding=ROUND_FLOOR)
    if quantity < max(Decimal(5), minimum):
        raise ValueError("M2_MINIMUM_EXCEEDS_RISK")
    return quantity


def encode(value):
    return json.dumps(plain(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class M2Store:
    def __init__(self, directory, settings, version):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings, self.version = settings, version
        self.db = sqlite3.connect(self.directory/"state.sqlite3", timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS markets(
            slug TEXT PRIMARY KEY,epoch INTEGER NOT NULL,metadata TEXT NOT NULL,
            state TEXT NOT NULL,full_window INTEGER NOT NULL DEFAULT 0,
            winner TEXT,resolution TEXT);
          CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY,slug TEXT NOT NULL,version TEXT NOT NULL,
            settings TEXT NOT NULL,started REAL NOT NULL,ended REAL,
            state TEXT NOT NULL,counts TEXT NOT NULL DEFAULT '{}');
          CREATE INDEX IF NOT EXISTS m2_pending_markets ON markets(epoch)
            WHERE state!='ACTIVE' AND winner IS NULL;
          CREATE TABLE IF NOT EXISTS events(
            seq INTEGER PRIMARY KEY,run_id TEXT NOT NULL,wall REAL NOT NULL,
            kind TEXT NOT NULL,payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS m2_events_run ON events(run_id,seq);
          CREATE TABLE IF NOT EXISTS candidates(
            id TEXT PRIMARY KEY,run_id TEXT NOT NULL,ap_id TEXT NOT NULL,
            rf_id TEXT NOT NULL,wall REAL NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observations(
            seq INTEGER PRIMARY KEY,run_id TEXT NOT NULL,candidate_id TEXT NOT NULL,
            wall REAL NOT NULL,valid INTEGER NOT NULL,ratio TEXT NOT NULL,
            payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS m2_observations_run ON observations(run_id,seq);
          CREATE TABLE IF NOT EXISTS samples(
            id INTEGER PRIMARY KEY,slug TEXT NOT NULL,run_id TEXT NOT NULL,
            version TEXT NOT NULL,rule TEXT NOT NULL,ap_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,wall REAL NOT NULL,outcome TEXT NOT NULL,
            price TEXT NOT NULL,quantity TEXT NOT NULL,cost TEXT NOT NULL,
            status TEXT NOT NULL,reason TEXT,ratio TEXT NOT NULL,
            elapsed REAL NOT NULL,sample_pnl TEXT,trade_pnl TEXT,
            UNIQUE(slug,version,rule,ap_id));
          CREATE INDEX IF NOT EXISTS m2_samples_version_rule ON samples(version,rule);
          CREATE TABLE IF NOT EXISTS strategy_observations(
            seq INTEGER PRIMARY KEY,run_id TEXT NOT NULL,strategy TEXT NOT NULL,
            candidate_id TEXT NOT NULL,wall REAL NOT NULL,valid INTEGER NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS strategy_samples(
            id INTEGER PRIMARY KEY,slug TEXT NOT NULL,run_id TEXT NOT NULL,
            version TEXT NOT NULL,strategy TEXT NOT NULL,root TEXT NOT NULL,
            candidate_id TEXT NOT NULL,wall REAL NOT NULL,outcome TEXT NOT NULL,
            price TEXT NOT NULL,quantity TEXT NOT NULL,cost TEXT NOT NULL,
            status TEXT NOT NULL,reason TEXT,elapsed REAL NOT NULL,sample_pnl TEXT,trade_pnl TEXT,
            UNIQUE(slug,version,strategy,root));
          CREATE INDEX IF NOT EXISTS strategy_samples_version ON strategy_samples(version,strategy);
        """)

    def recover(self):
        with self.db:
            self.db.execute("UPDATE runs SET state='INTERRUPTED',ended=? WHERE state='ACTIVE'", (time.time(),))
            self.db.execute("UPDATE markets SET state='INTERRUPTED' WHERE state='ACTIVE'")

    def has_market(self, slug):
        return self.db.execute("SELECT 1 FROM markets WHERE slug=?", (slug,)).fetchone() is not None

    def start(self, run_id, meta):
        slug = meta["slug"]
        with self.db:
            self.db.execute("INSERT INTO markets(slug,epoch,metadata,state) VALUES(?,?,?,'ACTIVE')",
                            (slug, int(slug.rsplit("-", 1)[1]), encode(meta)))
            self.db.execute("INSERT INTO runs(id,slug,version,settings,started,state) VALUES(?,?,?,?,?,'ACTIVE')",
                            (run_id, slug, self.version, encode(self.settings.model_dump(mode="json")), time.time()))

    def ingest(self, run_id, slug, rows, *, bounds, minimum):
        with self.db:
            for row in rows:
                kind, wall = row["event"], row["observed_wall"]
                if kind == "M2_OBSERVATION":
                    lower, upper = bounds(row["available_monotonic"])
                    row = {"elapsed_lower": lower, "elapsed_upper": upper, **row}
                    row["observation_valid"] = bool(row["observation_valid"] and row.get("clock_fresh", True))
                    self.db.execute("INSERT INTO observations(run_id,candidate_id,wall,valid,ratio,payload) VALUES(?,?,?,?,?,?)",
                                    (run_id, row["candidate_id"], wall, int(row["observation_valid"]),
                                     str(row["buy_ratio_upper"]), encode(row)))
                    self.sample(run_id, slug, row, minimum=minimum)
                else:
                    self.db.execute("INSERT INTO events(run_id,wall,kind,payload) VALUES(?,?,?,?)",
                                    (run_id, wall, kind, encode(row)))
                    if kind == "M2_CANDIDATE":
                        self.db.execute("INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?,?)",
                                        (row["candidate_id"], run_id, row["ap_id"], row["rf_id"], wall, encode(row)))

    def sample(self, run_id, slug, row, *, minimum):
        cfg = self.settings
        if (not row["observation_valid"] or row["visible_bid_quantity"] <= 0
                or row["elapsed_lower"] < cfg.start_sec or row["elapsed_upper"] >= cfg.cutoff_sec):
            return
        price = Decimal(str(row["anchor_bid_price"]))
        tick = Decimal(str(row["tick"]))
        if (not price.is_finite() or not tick.is_finite() or tick <= 0
                or price % tick or price >= Decimal(str(row["book_best_ask"]))):
            return
        ratio = Decimal(str(row["buy_ratio_upper"]))
        for rule, threshold in cfg.rules.items():
            if threshold is not None and ratio > threshold:
                continue
            if self.db.execute("SELECT 1 FROM samples WHERE slug=? AND version=? AND rule=? AND ap_id=?",
                               (slug, self.version, rule, row["ap_id"])).fetchone():
                continue
            reason = None
            try:
                quantity = quantity_for_risk(price, minimum, cfg.risk_usdc)
            except ValueError as exc:
                quantity, reason = Decimal(0), str(exc)
            cost = quantity*price
            direction_fills = self.db.execute(
                "SELECT count(*) FROM samples WHERE slug=? AND version=? AND rule=? "
                "AND outcome=? AND status='ASSUMED_FILL'",
                (slug, self.version, rule, row["outcome"])).fetchone()[0]
            if reason is None and direction_fills >= cfg.max_entries_per_direction:
                reason = "DIRECTION_ENTRY_LIMIT_REACHED"
            spent = sum((Decimal(r[0]) for r in self.db.execute(
                "SELECT cost FROM samples WHERE slug=? AND version=? AND rule=? AND status='ASSUMED_FILL'",
                (slug, self.version, rule))), Decimal(0))
            if reason is None and spent+cost > cfg.market_budget_usdc:
                reason = "MARKET_BUDGET_EXHAUSTED"
            self.db.execute("""INSERT INTO samples(
                slug,run_id,version,rule,ap_id,candidate_id,wall,outcome,price,quantity,cost,
                status,reason,ratio,elapsed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (slug, run_id, self.version, rule, row["ap_id"], row["candidate_id"],
                 row["observed_wall"], row["outcome"], str(price), str(quantity), str(cost),
                 "SKIPPED" if reason else "ASSUMED_FILL", reason, str(ratio), row["elapsed_lower"]))

    def finish(self, run_id, slug, *, full_window, complete, counts):
        state = "CLOSED" if complete else "INTERRUPTED"
        with self.db:
            self.db.execute("UPDATE runs SET ended=?,state=?,counts=? WHERE id=?",
                            (time.time(), state, encode(counts), run_id))
            self.db.execute("UPDATE markets SET state=?,full_window=? WHERE slug=?",
                            (state, int(full_window and complete), slug))

    def ingest_extra(self, run_id, slug, rows, *, minimum):
        """M3/M4 share M2's public ledger and official settlement, never an account."""
        with self.db:
            for event in rows:
                strategy, kind, wall = event['strategy'], event['event'], event['wall']
                if strategy not in {'M3', 'M4'}:
                    raise ValueError('UNSUPPORTED_RESEARCH_STRATEGY')
                row = event['payload']
                if kind != 'OBSERVATION':
                    self.db.execute('INSERT INTO events(run_id,wall,kind,payload) VALUES(?,?,?,?)',
                                    (run_id, wall, strategy+'_'+kind, encode(event)))
                    continue
                valid = bool(row['observation_valid'] and row['clock_fresh'])
                self.db.execute('''INSERT INTO strategy_observations(
                    run_id,strategy,candidate_id,wall,valid,payload) VALUES(?,?,?,?,?,?)''',
                    (run_id, strategy, row['id'], wall, int(valid), encode(row)))
                cfg = self.settings
                if (not valid or row.get('planned_shutdown', False) or Decimal(str(row['visible_bid_quantity'])) <= 0
                        or row['elapsed_lower'] < cfg.start_sec or row['elapsed_upper'] >= cfg.cutoff_sec):
                    continue
                price, tick, ask = (Decimal(str(row[k])) for k in ('price', 'tick', 'book_best_ask'))
                if (not all(v.is_finite() for v in (price, tick, ask)) or not 0 < price < 1
                        or tick <= 0 or price % tick or price >= ask):
                    continue
                key = (slug, self.version, strategy)
                if self.db.execute('''SELECT 1 FROM strategy_samples
                    WHERE slug=? AND version=? AND strategy=? AND root=?''', (*key, row['root'])).fetchone():
                    continue
                reason = None
                try:
                    quantity = quantity_for_risk(price, minimum, cfg.risk_usdc)
                except ValueError as exc:
                    quantity, reason = Decimal(0), str(exc)
                cost = quantity*price
                fills = self.db.execute('''SELECT outcome,cost FROM strategy_samples
                    WHERE slug=? AND version=? AND strategy=? AND status='ASSUMED_FILL' ''', key).fetchall()
                if reason is None and sum(r['outcome'] == row['outcome'] for r in fills) >= cfg.max_entries_per_direction:
                    reason = 'DIRECTION_ENTRY_LIMIT_REACHED'
                if reason is None and sum((Decimal(r['cost']) for r in fills), Decimal(0))+cost > cfg.market_budget_usdc:
                    reason = 'MARKET_BUDGET_EXHAUSTED'
                self.db.execute('''INSERT INTO strategy_samples(
                    slug,run_id,version,strategy,root,candidate_id,wall,outcome,price,quantity,cost,
                    status,reason,elapsed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (slug, run_id, self.version, strategy, row['root'], row['id'], wall, row['outcome'],
                     str(price), str(quantity), str(cost), 'SKIPPED' if reason else 'ASSUMED_FILL',
                     reason, row['elapsed_lower']))

    def pending(self):
        return [dict(r) for r in self.db.execute(
            "SELECT slug,metadata FROM markets WHERE state!='ACTIVE' AND winner IS NULL ORDER BY epoch LIMIT 32")]

    def settle(self, slug, result):
        winner = result["winner"]
        if winner not in {"YES", "NO"} or result["slug"] != slug:
            raise ValueError("M2_SETTLEMENT_IDENTITY_INVALID")
        prior = self.db.execute("SELECT winner FROM markets WHERE slug=?", (slug,)).fetchone()
        if prior is None or prior[0] not in (None, winner):
            raise ValueError("M2_SETTLEMENT_CONFLICT")
        with self.db:
            for table in ('samples', 'strategy_samples'):
                for row in self.db.execute(f"SELECT * FROM {table} WHERE slug=?", (slug,)).fetchall():
                    pnl = ((Decimal(row["quantity"]) if row["outcome"] == winner else Decimal(0))
                           -Decimal(row["cost"])) if Decimal(row["quantity"]) > 0 else None
                    trade_pnl = pnl if row["status"] == "ASSUMED_FILL" else None
                    self.db.execute(f"UPDATE {table} SET sample_pnl=?,trade_pnl=? WHERE id=?",
                                    (str(pnl) if pnl is not None else None,
                                     str(trade_pnl) if trade_pnl is not None else None, row["id"]))
            self.db.execute("UPDATE markets SET winner=?,resolution=? WHERE slug=?",
                            (winner, encode(result), slug))

    def report(self, runtime):
        # Bounded by configured rule/version count; detailed history remains in SQLite.
        summaries = []
        for group in self.db.execute("SELECT DISTINCT version,rule FROM samples"):
            rows = self.db.execute("""SELECT s.*,m.full_window,m.state AS market_state,m.winner
                FROM samples s JOIN markets m ON m.slug=s.slug WHERE s.version=? AND s.rule=?""", group)
            count = fills = settled = pending = wins = 0
            spent = pnl = sample_pnl = Decimal(0)
            incomplete = 0
            complete_pnl, complete_cost = Decimal(0), Decimal(0)
            complete_settled = 0
            for row in rows:
                count += 1
                incomplete += not row["full_window"]
                if row["sample_pnl"] is not None:
                    sample_pnl += Decimal(row["sample_pnl"])
                if row["status"] == "ASSUMED_FILL":
                    fills += 1
                    if row["trade_pnl"] is None:
                        pending += 1
                    else:
                        settled += 1
                        spent += Decimal(row["cost"])
                        pnl += Decimal(row["trade_pnl"])
                        wins += row["outcome"] == row["winner"]
                        if row["full_window"]:
                            complete_pnl += Decimal(row["trade_pnl"])
                            complete_cost += Decimal(row["cost"])
                            complete_settled += 1
            summaries.append({"version": group[0], "rule": group[1], "samples": count,
                              "assumed_fills": fills, "settled_fills": settled,
                              "pending_fills": pending, "incomplete_market_samples": incomplete,
                              "settled_cost": str(spent), "settled_pnl": str(pnl),
                              "settled_wins": wins, "settled_roi": str(pnl/spent) if spent else None,
                              "complete_market_settled_fills": complete_settled,
                              "complete_market_settled_pnl": str(complete_pnl),
                              "complete_market_settled_cost": str(complete_cost),
                              "all_sample_pnl": str(sample_pnl)})
        markets = [dict(r) for r in self.db.execute(
            "SELECT slug,state,full_window,winner FROM markets ORDER BY epoch DESC LIMIT 48")]
        report = {"mode": "M2_PUBLIC_RESEARCH", "phase": "EXPLORATION_NOT_HOLDOUT_VALIDATION",
                "real_posts": 0, "model": MODEL, "observed_at": time.time(),
                "runtime": runtime, "settings_hash": self.settings.fingerprint,
                "settings": self.settings.model_dump(mode="json"),
                "active_version": self.version, "rules": summaries, "recent_markets": markets,
                "market_count": self.db.execute("SELECT count(*) FROM markets").fetchone()[0],
                "candidate_count": self.db.execute("SELECT count(*) FROM candidates").fetchone()[0],
                "observation_count": self.db.execute("SELECT count(*) FROM observations").fetchone()[0]}
        if self.settings.include_m3_m4:
            strategies = {}
            for strategy in ('M2', 'M3', 'M4'):
                if strategy == 'M2':
                    rows = self.db.execute("""SELECT s.*,m.winner FROM samples s
                        JOIN markets m ON m.slug=s.slug WHERE s.version=? AND s.rule='all_candidates'""",
                        (self.version,)).fetchall()
                else:
                    rows = self.db.execute('''SELECT s.*,m.winner FROM strategy_samples s
                        JOIN markets m ON m.slug=s.slug WHERE s.version=? AND s.strategy=?''',
                        (self.version, strategy)).fetchall()
                fills = [r for r in rows if r['status'] == 'ASSUMED_FILL']
                settled = [r for r in fills if r['trade_pnl'] is not None]
                strategies[strategy] = {
                    'samples': len(rows), 'assumed_fills': len(fills), 'settled_fills': len(settled),
                    'pending_fills': len(fills)-len(settled),
                    'settled_cost': str(sum((Decimal(r['cost']) for r in settled), Decimal(0))),
                    'settled_pnl': str(sum((Decimal(r['trade_pnl']) for r in settled), Decimal(0))),
                    'settled_wins': sum(r['outcome'] == r['winner'] for r in settled)}
            report['strategies'] = strategies
            report['strategy_observation_count'] = dict(self.db.execute('''SELECT strategy,count(*)
                FROM strategy_observations o JOIN runs r ON r.id=o.run_id WHERE r.version=? GROUP BY strategy''',
                (self.version,)))
        return report

    @classmethod
    def read_report(cls, directory, settings, version, runtime):
        """Report from a separate, thread-owned, consistent read-only snapshot.

        Never initialize a writer/recover runs from the reporting worker, and
        never pass the collector's SQLite connection across threads.
        """
        path = (Path(directory)/"state.sqlite3").resolve()
        db = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA cache_size=-512")
            db.execute("BEGIN")
            reader = object.__new__(cls)
            reader.db, reader.settings, reader.version = db, settings, version
            return reader.report(runtime)
        finally:
            db.rollback()
            db.close()

    def close(self):
        self.db.close()
