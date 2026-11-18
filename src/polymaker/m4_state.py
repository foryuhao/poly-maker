"""Durable M4 opportunity targets and gross-risk ledger.

This database is an account-owned trading record, never a research log. Every
transition commits before returning; a prepared identity is never sent twice.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from threading import RLock

from polymaker.m4_config import CURRENT_MINIMUM_PRICE, quantity_for_budget, validated_minimum_price
from polymaker.m4_types import M4_LEDGER

BASELINE_MAX_INSTRUCTIONS = 2


def decimal(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("M4_NONFINITE_AMOUNT")
    return number


class M4Ledger:
    ledger_strategy = M4_LEDGER

    @staticmethod
    def metadata(path, *, strategy=M4_LEDGER):
        """Inspect identity without changing a foreign ledger or its journal mode."""
        path = Path(path).absolute()
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('M4_LEDGER_SYMLINK_FORBIDDEN')
        if not path.exists():
            return {}
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                return {}
            if 'metadata' not in tables:
                raise ValueError('M4_FOREIGN_DATABASE')
            result = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM metadata')}
            if result.get('identity', {}).get('strategy') != strategy:
                raise ValueError('M4_FOREIGN_DATABASE')
            return result

    @property
    def sizing_policy(self):
        return "M4_BUDGET_FLOOR_01_MAX5_V1"

    @property
    def minimum_price(self):
        # Existing markets retain their stored floor; fresh ledgers use the
        # current contract until atomic session initialization persists it.
        return validated_minimum_price(self.get('settings', {}).get('minimum_price', CURRENT_MINIMUM_PRICE))

    def order_quantity(self, price, minimum):
        """Default live sizing, also used to validate every durable quote."""
        return quantity_for_budget(price, minimum)

    def __init__(self, path, identity, *, limit=Decimal(10), order_budget=Decimal(5), wall=None):
        self.wall = wall or time.time
        if identity.get("strategy") != self.ledger_strategy:
            raise ValueError("M4_STRATEGY_IDENTITY_REQUIRED")
        candidate = Path(path).absolute()
        if candidate.resolve() != candidate or candidate.is_symlink():
            raise ValueError("M4_LEDGER_SYMLINK_FORBIDDEN")
        self.path = candidate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.limit, self.order_budget = decimal(limit), decimal(order_budget)
        if (self.order_budget, self.limit) != self.risk_limits:
            raise ValueError("M4_RISK_LIMIT_INVALID")
        expected = {**identity, "limit": str(self.limit), "order_budget": str(self.order_budget),
                    "prepost_policy": self.sizing_policy}
        existing = self.metadata(self.path, strategy=self.ledger_strategy).get('identity')
        if existing is not None and existing != expected:
            raise ValueError('M4_RECOVERY_IDENTITY_OR_SETTINGS_CHANGED')
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY, support TEXT NOT NULL, root TEXT UNIQUE NOT NULL,
                token TEXT NOT NULL, outcome TEXT NOT NULL, price TEXT NOT NULL,
                quantity TEXT NOT NULL, target TEXT NOT NULL, evidence TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT, order_id TEXT UNIQUE, quote TEXT,
                started INTEGER NOT NULL DEFAULT 0, posts INTEGER NOT NULL DEFAULT 0,
                closed INTEGER NOT NULL DEFAULT 0,
                filled TEXT NOT NULL DEFAULT '0', spent TEXT NOT NULL DEFAULT '0',
                reserved TEXT NOT NULL DEFAULT '0');
            CREATE TABLE IF NOT EXISTS facts (
                kind TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (kind, identity));
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                wall REAL NOT NULL);
        """)
        # Additive migration: keep old signals and signed identities unchanged.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(opportunities)")}
        for name, spec in (("uncertain_retries", "INTEGER NOT NULL DEFAULT 0"),
                           ("retry_pricing", "TEXT NOT NULL DEFAULT 'SIGNAL'"),
                           ("repriced_from", "TEXT"), ("failure_proof", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE opportunities ADD COLUMN {name} {spec}")
        self.db.execute("""CREATE TABLE IF NOT EXISTS uncertain_posts (
            order_id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL,
            quote TEXT NOT NULL, cost TEXT NOT NULL)""")
        self.db.commit()
        conflict = False
        with self.lock, self.db:
            existing = self.get("identity")
            expected = {**identity, "limit": str(self.limit), "order_budget": str(self.order_budget),
                        "prepost_policy": self.sizing_policy}
            if existing is None:
                self.set("identity", expected)
            elif existing != expected:
                conflict = True
        if conflict:
            self.db.close()
            raise ValueError("M4_RECOVERY_IDENTITY_OR_SETTINGS_CHANGED")
        try:
            self.set('fill_tracking', 'NOT_TRACKED')
            for row in self.rows():
                price, quantity, target = (decimal(row[k]) for k in ("price", "quantity", "target"))
                current_quantity = decimal(json.loads(row["quote"])["size"]) if row["quote"] else quantity
                if (not self.minimum_price <= price < 1 or quantity < 5 or quantity*price != target
                        or quantity != self.order_quantity(price, 5)
                        or decimal(row["reserved"]) < 0
                        or (row["state"] in {"RESERVED", "PREPARED", "UNKNOWN", "ACK"}
                            and decimal(row["reserved"]) < 1)
                        or (row["state"] in {"PREPARED", "UNKNOWN", "ACK"}
                            and (not row["quote"] or decimal(row["reserved"]) < self.quote_cost(row)))
                        or current_quantity <= 0 or row["closed"] != 0
                        or decimal(row["filled"]) != 0 or decimal(row["spent"]) != 0
                        or row["state"] not in {"OBSERVED", "RESERVED", "PREPARED", "UNKNOWN", "ACK", "SKIPPED", "ABORTED", "REJECTED"}
                        or row["uncertain_retries"] not in {0, 1}
                        or row["retry_pricing"] not in {"SIGNAL", "BEST_BID_ON_REJECTION"}
                        or row["started"] and not row["order_id"]):
                    raise ValueError("M4_DURABLE_LEDGER_INVALID")
            archived = self.uncertain_posts()
            for row in self.rows():
                if sum(o["opportunity_id"] == row["id"] for o in archived) != row["uncertain_retries"]:
                    raise ValueError("M4_UNCERTAIN_POST_INVALID")
            for old in archived:
                quote = json.loads(old["quote"])
                row = self.row(old["opportunity_id"])
                price, size = decimal(quote["price"]), decimal(quote["size"])
                if (re.fullmatch(r"0x[0-9a-fA-F]{64}", old["order_id"]) is None
                        or old["order_id"] == row["order_id"]
                        or quote["token_id"] != row["token"] or quote["side"] != "BUY"
                        or not self.minimum_price <= price <= decimal(row["price"]) or size != self.order_quantity(price, 5)
                        or decimal(old["cost"]) != price*size):
                    raise ValueError("M4_UNCERTAIN_POST_INVALID")
            if self.committed() > self.limit:
                raise ValueError("M4_DURABLE_LEDGER_OVER_LIMIT")
        except BaseException:
            self.db.close()
            raise

    @property
    def risk_limits(self):
        return Decimal(5), Decimal(10)

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                            (key, json.dumps(value, sort_keys=True, default=str, allow_nan=False)))

    def initialize_session(self, session_id, settings, market, *, baseline_fill_model=None):
        values = {'session_id': session_id, 'settings': settings, 'market': market}
        if baseline_fill_model is not None:
            if self.get('identity')['mode'] != 'VIRTUAL':
                raise ValueError('M4_BASELINE_FILL_MODEL_REQUIRES_VIRTUAL')
            values['baseline_fill_model'] = baseline_fill_model
        with self.lock, self.db:
            existing = {k: self.get(k) for k in values}
            if any(v is not None for v in existing.values()) and existing != values:
                raise ValueError('M4_SESSION_METADATA_CONFLICT')
            self.db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                [(k, json.dumps(v, sort_keys=True, default=str, allow_nan=False)) for k, v in values.items()])

    def rows(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM opportunities ORDER BY rowid")]

    def row(self, key):
        with self.lock:
            result = self.db.execute("SELECT * FROM opportunities WHERE id=?", (key,)).fetchone()
            if result is None:
                raise ValueError("M4_OPPORTUNITY_UNKNOWN")
            return dict(result)

    def committed(self):
        # Gross reservations survive ACK and UNKNOWN, regardless of modeled
        # baseline settlement. Explicit rejection releases only its own attempt.
        return (sum((decimal(o["cost"]) for o in self.uncertain_posts()), Decimal(0))
                + sum((decimal(r["reserved"])
                       for r in self.rows() if r["state"] in {"RESERVED", "PREPARED", "UNKNOWN", "ACK"}), Decimal(0)))

    @staticmethod
    def quote_cost(row):
        quote = json.loads(row["quote"]) if row["quote"] else None
        return decimal(quote["price"])*decimal(quote["size"]) if quote else decimal(row["target"])

    def uncertain_posts(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM uncertain_posts ORDER BY rowid")]

    def instruction_slots(self):
        retained = {r["opportunity_id"] for r in self.uncertain_posts()}
        return sum(
            r["id"] in retained or r["state"] in {"RESERVED", "PREPARED", "UNKNOWN", "ACK"}
            for r in self.rows())

    def signal_price_allowed(self, price):
        return self.minimum_price <= price < 1

    def observe(self, opportunity, *, minimum, elapsed):
        """Fix every eligible signal's denominator, even if no money is left."""
        quantity = self.order_quantity(opportunity.anchor_bid_price, minimum)
        price = decimal(opportunity.anchor_bid_price)
        if not self.signal_price_allowed(price):
            raise ValueError('M4_PRICE_OUTSIDE_BAND')
        with self.lock, self.db:
            duplicate = self.db.execute("SELECT id FROM opportunities WHERE root=?",
                                        (opportunity.root_id,)).fetchone()
            if duplicate:
                return None
            evidence = {**asdict(opportunity), "elapsed_s": elapsed}
            if evidence.get("taker_strategy") is None:
                evidence.pop("taker_strategy", None)
            if evidence.get("market_id") is None:
                evidence.pop("market_id", None)
            self.db.execute("""INSERT INTO opportunities
                (id,support,root,token,outcome,price,quantity,target,evidence,state)
                VALUES (?,?,?,?,?,?,?,?,?, 'OBSERVED')""",
                (opportunity.episode_id, opportunity.support_id, opportunity.root_id,
                 opportunity.token_id, opportunity.outcome, str(price), str(quantity),
                 str(price*quantity), json.dumps(evidence, default=str)))
            return self.row(opportunity.episode_id)

    def direction_slots(self, key):
        """Other roots claiming this outcome, including every previously sent root.

        Failed unsent preparations release the direction. A sent root retains
        it even after a definitive rejection; its own retries are excluded.
        """
        with self.lock:
            row = self.row(key)
            roots = {r['id'] for r in self.rows() if r['id'] != key
                     and r['outcome'] == row['outcome']
                     and (r['posts'] > 0 or r['state'] in {'RESERVED', 'PREPARED', 'UNKNOWN', 'ACK'})}
            return len(roots)

    def reserve(self, key, *, spendable, direction_limit=1):
        with self.lock, self.db:
            row = self.row(key)
            if row["state"] != "OBSERVED":
                return False
            amount = decimal(row["target"])
            reason = ("SESSION_INSTRUCTION_LIMIT" if self.instruction_slots() >= BASELINE_MAX_INSTRUCTIONS else
                      "M4_DIRECTION_OPPORTUNITY_LIMIT" if direction_limit is not None
                          and self.direction_slots(key) >= direction_limit else
                      "M4_SESSION_BUDGET" if self.committed()+amount > self.limit else
                      "M4_ACCOUNT_BUDGET" if amount > decimal(spendable) else None)
            self.db.execute("UPDATE opportunities SET state=?, reason=?,reserved=? WHERE id=?",
                            ("SKIPPED" if reason else "RESERVED", reason, str(amount if reason is None else 0), key))
            return reason is None

    def authorized_price(self, row, price):
        if not self.minimum_price <= price < 1:
            return False
        if row['retry_pricing'] == 'BEST_BID_ON_REJECTION':
            return (row['repriced_from'] is not None and
                    price < min(decimal(row['price']), decimal(row['repriced_from'])))
        return price == decimal(row['price'])

    def reserve_quote(self, key, *, price, quantity, spendable):
        """Reprice a proven failed task without losing its original signal target."""
        price, quantity = decimal(price), decimal(quantity)
        with self.lock, self.db:
            row = self.row(key)
            if row["state"] != "RESERVED" or quantity != self.order_quantity(price, 5):
                raise ValueError("M4_RESERVATION_QUOTE_INVALID")
            if not self.authorized_price(row, price):
                raise ValueError("M4_UNAUTHORIZED_REPRICE")
            amount = price*quantity
            delta = amount-decimal(row["reserved"])
            if self.committed()+delta > self.limit or delta > decimal(spendable):
                return False
            self.db.execute("UPDATE opportunities SET reserved=? WHERE id=?", (str(amount), key))
            return True

    def prepare(self, key, oid, quote):
        with self.lock, self.db:
            row = self.row(key)
            if row["state"] != "RESERVED" or not isinstance(oid, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", oid) is None:
                raise ValueError("M4_PREPARE_IDENTITY_INVALID")
            price, quantity = decimal(quote["price"]), decimal(quote["size"])
            if (quote["token_id"] != row["token"] or quote["side"] != "BUY"
                    or not self.minimum_price <= price <= decimal(row["price"])
                    or not self.authorized_price(row, price)
                    or self.db.execute("SELECT 1 FROM uncertain_posts WHERE order_id=?", (oid,)).fetchone()
                    or quantity != self.order_quantity(price, 5)
                    or price*quantity > decimal(row["reserved"])):
                raise ValueError("M4_PREPARE_ECONOMICS_CHANGED")
            self.db.execute("UPDATE opportunities SET order_id=?, quote=?, state='PREPARED' WHERE id=?",
                            (oid, json.dumps(quote), key))
            self.fact("signed_attempt", oid, {"opportunity_id": key, "quote": quote})

    def post_start(self, key, *, baseline_post=None):
        with self.lock, self.db:
            row = self.row(key)
            if row["state"] != "PREPARED" or row["started"]:
                raise ValueError("M4_DUPLICATE_POST")
            if baseline_post is not None:
                if (self.get('identity')['mode'] != 'VIRTUAL'
                        or baseline_post['opportunity_id'] != key
                        or baseline_post['order_id'] != row['order_id']
                        or baseline_post['quote'] != json.loads(row['quote'])):
                    raise ValueError('M4_BASELINE_POST_IDENTITY')
                self.db.execute("INSERT INTO facts VALUES ('virtual_post',?,?)",
                    (row['order_id'], json.dumps(baseline_post, sort_keys=True, default=str, allow_nan=False)))
            self.db.execute("UPDATE opportunities SET started=1,posts=posts+1,state='UNKNOWN' WHERE id=?", (key,))

    def retry(self, key, *, proof, spendable=0, direction_limit=1):
        """One uncertain resend per opportunity; retain its possible first BUY."""
        uncertain = proof.get("kind") in {"UNCERTAIN_RESPONSE", "UNCERTAIN_TIMEOUT", "UNCERTAIN_RESTART"}
        if not uncertain and not (proof.get("kind") == "NOT_SENT" or
                proof.get("kind") == "REJECTED_RESPONSE" and proof.get("reprice") is True):
            raise ValueError("M4_RETRY_REQUIRES_FAILURE_EVIDENCE")
        with self.lock, self.db:
            row = self.row(key)
            if direction_limit is not None and self.direction_slots(key) >= direction_limit:
                return False
            if uncertain:
                if row["state"] != "UNKNOWN" or not row["started"] or row["closed"]:
                    raise ValueError("M4_UNCERTAIN_RETRY_STATE_INVALID")
                if row["uncertain_retries"] >= 1:
                    return False
            elif (row["state"] not in {"REJECTED", "ABORTED"} or row["closed"]
                    or decimal(row["spent"]) or decimal(row["filled"])):
                raise ValueError("M4_RETRY_EXPOSURE_NOT_RESOLVED")
            amount = decimal(row["target"])
            # UNKNOWN already owns its first reservation. A resend must add
            # another full target; never transfer or erase the original risk.
            if (self.committed()+amount > self.limit or uncertain and amount > decimal(spendable)
                    or not uncertain and row["id"] not in {o["opportunity_id"] for o in self.uncertain_posts()}
                       and self.instruction_slots() >= BASELINE_MAX_INSTRUCTIONS):
                return False
            if uncertain:
                self.db.execute("INSERT INTO uncertain_posts VALUES(?,?,?,?)",
                    (row["order_id"], key, row["quote"], str(row["reserved"])))
            pricing = "BEST_BID_ON_REJECTION" if not uncertain and proof.get("reprice") else "SIGNAL"
            previous_price = str(json.loads(row["quote"])["price"]) if row["quote"] else None
            if proof.get("kind") == "NOT_SENT":
                # An unsent preparation did not consume the earlier explicit
                # rejection's repricing authorization.
                pricing, previous_price = row["retry_pricing"], row["repriced_from"]
            self.db.execute("""UPDATE opportunities SET state='RESERVED',started=0,order_id=NULL,
                quote=NULL,reserved=?,reason='M4_RETRY_WAIT',uncertain_retries=uncertain_retries+?,
                retry_pricing=?,repriced_from=?,failure_proof=NULL WHERE id=?""",
                (str(amount), int(uncertain), pricing, previous_price, key))
            self.event("m4_retry_authorized", {"opportunity_id": key, "order_id": row["order_id"],
                       "post_number": row["posts"], "proof": proof, "retry_pricing": pricing}, self.wall())
            return True

    def outcome(self, key, state, reason=None, *, proof=None):
        with self.lock, self.db:
            row = self.row(key)
            if state in {"ACK", "UNKNOWN", "REJECTED"} and not row["started"]:
                raise ValueError("M4_NATIVE_POST_NOT_STARTED")
            if state == "SKIPPED" and row["state"] != "OBSERVED":
                raise ValueError("M4_CANNOT_SKIP_RESERVED_ORDER")
            if state in {"ACK", "REJECTED"} and row["state"] not in {"UNKNOWN", state}:
                raise ValueError("M4_PLACEMENT_RESULT_CONFLICT")
            if state == "ABORTED" and row["started"]:
                raise ValueError("M4_POST_ALREADY_STARTED")
            if state == "REJECTED" and (decimal(row["filled"]) or row["closed"]):
                raise ValueError("M4_REJECTION_CONTRADICTS_FILL")
            if state not in {"ABORTED", "REJECTED", "ACK", "UNKNOWN", "SKIPPED"}:
                raise ValueError("M4_INVALID_OUTCOME")
            self.db.execute("UPDATE opportunities SET state=?,reason=?,failure_proof=? WHERE id=?",
                (state, reason, json.dumps(proof, allow_nan=False) if proof is not None else None, key))

    def modeled_settlement(self, slug, result):
        """Commit model fact and completion marker together, including crash repair."""
        with self.lock, self.db:
            prior = self.db.execute("SELECT payload FROM facts WHERE kind='modeled_settlement' AND identity=?",
                                    (slug,)).fetchone()
            if prior is not None:
                saved = json.loads(prior[0])
                # A repeated public observation can have a new timestamp. The
                # market, winner and model economics must remain identical.
                def comparable(value):
                    value = {**value, 'resolution': dict(value['resolution'])}
                    value['resolution'].pop('observed_at', None)
                    return value
                if comparable(saved) != comparable(result):
                    raise ValueError('M4_DURABLE_FACT_CONFLICT')
                result = saved
            marker = self.get('modeled_settlement')
            if marker is not None and marker != result:
                raise ValueError('M4_DURABLE_FACT_CONFLICT')
            payload = json.dumps(result, sort_keys=True, default=str, allow_nan=False)
            self.db.execute("INSERT OR IGNORE INTO facts VALUES ('modeled_settlement',?,?)", (slug, payload))
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('modeled_settlement',?)", (payload,))
            return result

    def fact(self, kind, identity, payload):
        self.fact_batch([(kind, identity, payload)])

    def fact_batch(self, facts):
        """All-or-nothing related model facts, without touching trading exposure."""
        with self.lock, self.db:
            for kind, identity, payload in facts:
                text = json.dumps(payload, default=str, sort_keys=True, allow_nan=False)
                prior = self.db.execute("SELECT payload FROM facts WHERE kind=? AND identity=?",
                                        (kind, identity)).fetchone()
                if prior is not None and prior[0] != text:
                    raise ValueError("M4_DURABLE_FACT_CONFLICT")
                self.db.execute("INSERT OR IGNORE INTO facts VALUES (?,?,?)", (kind, identity, text))

    def facts(self, kind):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM facts WHERE kind=?", (kind,))]

    def event(self, kind, payload, wall):
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(kind,payload,wall) VALUES (?,?,?)",
                            (kind, json.dumps(payload, default=str, allow_nan=False), wall))

    def events(self, prefix):
        with self.lock:
            return [(r[0], json.loads(r[1])) for r in self.db.execute(
                "SELECT kind,payload FROM events WHERE kind LIKE ? ORDER BY seq", (prefix+"%",))]

    def metric(self, *, winner=None):
        rows = [r for r in self.rows() if winner is None or r["outcome"] == winner]
        if self.get("fill_tracking") == "NOT_TRACKED":
            return {"signals": len(rows), "native_posts": sum(r["posts"] for r in rows),
                    "fill_tracking": "NOT_TRACKED", "attainment": None, "quantity_attainment": None,
                    "target_risk_usdc": str(self.order_budget),
                    "by_signal": [{"id": r["id"], "outcome": r["outcome"], "state": r["state"],
                        "reason": r["reason"], "posts": r["posts"], "uncertain_retries": r["uncertain_retries"],
                        "target_usdc": str(self.quote_cost(r)), "filled": None,
                        "spent_usdc": None, "attainment": None} for r in rows],
                    "pending": sum(r["state"] in {"OBSERVED", "RESERVED", "PREPARED"} for r in rows),
                    "uncertain_retries": sum(r["uncertain_retries"] for r in rows),
                    "submitted_unverified": sum(o["opportunity_id"] in {r["id"] for r in rows} for o in self.uncertain_posts())
                        + sum(bool(r["started"]) and r["state"] != "REJECTED" for r in rows),
                    "committed_usdc": str(self.committed())}
        raise ValueError('M4_ACTUAL_FILL_TRACKING_FORBIDDEN')

    def close(self):
        with self.lock:
            self.db.close()
