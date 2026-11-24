"""Durable real-order ownership: one task per strategy/market/direction.

Full-sync transactions precede native POST. No UNKNOWN task can be resent.
"""

import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from threading import RLock
from uuid import uuid4

from polymaker.m2_store import encode, quantity_for_risk
from polymaker.userstream.parse import normalize_order, parse_trade

D = Decimal
EPS = D('.000001')


class LiveLedger:
    def __init__(self, path, funder):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS markets(slug TEXT PRIMARY KEY,metadata TEXT NOT NULL,
            version TEXT NOT NULL,started REAL NOT NULL,ended REAL,winner TEXT,resolution TEXT);
          CREATE TABLE IF NOT EXISTS entries(
            id TEXT PRIMARY KEY,slug TEXT NOT NULL,strategy TEXT NOT NULL,outcome TEXT NOT NULL,
            token TEXT NOT NULL,price TEXT NOT NULL,quantity TEXT NOT NULL,cost TEXT NOT NULL,
            candidate TEXT NOT NULL,state TEXT NOT NULL,order_id TEXT UNIQUE,
            started INTEGER NOT NULL DEFAULT 0,reserved TEXT NOT NULL,
            matched TEXT NOT NULL DEFAULT '0',filled TEXT NOT NULL DEFAULT '0',
            spent TEXT NOT NULL DEFAULT '0',terminal INTEGER NOT NULL DEFAULT 0,
            reason TEXT,created REAL NOT NULL,updated REAL NOT NULL,
            UNIQUE(slug,strategy,outcome));
          CREATE TABLE IF NOT EXISTS trades(trade_id TEXT NOT NULL,order_id TEXT NOT NULL,
            quantity TEXT NOT NULL,price TEXT NOT NULL,status TEXT NOT NULL,
            PRIMARY KEY(trade_id,order_id));
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,wall REAL NOT NULL,
            kind TEXT NOT NULL,payload TEXT NOT NULL);
        ''')
        with self.lock, self.db:
            old = self.db.execute("SELECT value FROM metadata WHERE key='funder'").fetchone()
            if old and old[0] != funder.lower():
                raise ValueError('M234_LEDGER_ACCOUNT_MISMATCH')
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('funder',?)", (funder.lower(),))

    def event(self, kind, payload):
        with self.lock, self.db:
            self.db.execute('INSERT INTO events(wall,kind,payload) VALUES(?,?,?)',
                            (time.time(), kind, encode(payload)))

    def event_batch(self, events):
        with self.lock, self.db:
            self.db.executemany('INSERT INTO events(wall,kind,payload) VALUES(?,?,?)',
                [(r['wall'], r['strategy']+':'+r['event'], encode(r)) for r in events])

    def start_market(self, meta, version):
        with self.lock, self.db:
            old = self.db.execute('SELECT * FROM markets WHERE slug=?', (meta['slug'],)).fetchone()
            if old:
                return False
            self.db.execute('INSERT INTO markets(slug,metadata,version,started) VALUES(?,?,?,?)',
                            (meta['slug'], encode(meta), version, time.time()))
            return True

    def row(self, key):
        with self.lock:
            r = self.db.execute('SELECT * FROM entries WHERE id=?', (key,)).fetchone()
            return dict(r) if r else None

    def rows(self, slug=None):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM entries'+
                (' WHERE slug=?' if slug else '')+' ORDER BY created', (slug,) if slug else ())]

    def reserved(self):
        return sum((D(r['reserved']) for r in self.rows()), D(0))

    def uncertain(self):
        return any(r['state'] in {'SUBMITTING', 'UNKNOWN', 'RECONCILE_UNKNOWN'} for r in self.rows())

    def reserve(self, slug, candidate, minimum, spendable):
        p = D(str(candidate['price']))
        qty = quantity_for_risk(p, minimum, D(5))
        cost = p*qty
        if candidate['strategy'] not in {'M2', 'M3', 'M4'} or candidate['outcome'] not in {'YES', 'NO'}:
            raise ValueError('M234_ENTRY_IDENTITY_INVALID')
        with self.lock, self.db:
            prior = self.db.execute('SELECT id FROM entries WHERE slug=? AND strategy=? AND outcome=?',
                (slug, candidate['strategy'], candidate['outcome'])).fetchone()
            if prior or self.uncertain():
                return None
            market_cost = sum((D(r['cost']) for r in self.rows(slug)), D(0))
            if cost > 5 or market_cost+cost > 30 or cost+self.reserved() > D(str(spendable)):
                return None
            key, now = uuid4().hex, time.time()
            self.db.execute('''INSERT INTO entries(id,slug,strategy,outcome,token,price,quantity,cost,
                candidate,state,reserved,created,updated) VALUES(?,?,?,?,?,?,?,?,?,'RESERVED',?,?,?)''',
                (key, slug, candidate['strategy'], candidate['outcome'], candidate['token'],
                 str(p), str(qty), str(cost), encode(candidate), str(cost), now, now))
            self.event('ENTRY_RESERVED', {'id': key, 'candidate': candidate, 'cost': cost})
            return self.row(key)

    def prepare(self, key, oid, quote):
        with self.lock, self.db:
            row = self.row(key)
            if (row['state'] != 'RESERVED' or row['started'] or quote['token_id'] != row['token']
                    or D(str(quote['price'])) != D(row['price'])
                    or D(str(quote['size'])) != D(row['quantity']) or quote['side'] != 'BUY'):
                raise ValueError('M234_PREPARE_CONFLICT')
            self.db.execute("UPDATE entries SET order_id=?,state='PREPARED',updated=? WHERE id=?",
                            (oid, time.time(), key))
            self.event('ORDER_PREPARED', {'id': key, 'order_id': oid, 'quote': quote})

    def post_start(self, key):
        with self.lock, self.db:
            row = self.row(key)
            if row['state'] != 'PREPARED' or row['started']:
                raise ValueError('M234_DUPLICATE_POST')
            self.db.execute("UPDATE entries SET started=1,state='SUBMITTING',updated=? WHERE id=?",
                            (time.time(), key))
            self.event('NATIVE_POST_STARTED', {'id': key, 'order_id': row['order_id']})

    def outcome(self, key, state, reason=None):
        with self.lock, self.db:
            row = self.row(key)
            if state not in {'ACK', 'UNKNOWN', 'RECONCILE_UNKNOWN', 'ABORTED', 'REJECTED'}:
                raise ValueError('M234_OUTCOME_INVALID')
            if state == 'ABORTED' and row['started']:
                raise ValueError('M234_SENT_ORDER_CANNOT_ABORT')
            release = state in {'ABORTED', 'REJECTED'}
            self.db.execute('UPDATE entries SET state=?,reason=?,reserved=?,terminal=?,updated=? WHERE id=?',
                (state, reason, '0' if release else row['cost'], int(release), time.time(), key))
            self.event('ORDER_OUTCOME', {'id': key, 'state': state, 'reason': reason})

    def reconcile(self, key, order, raw_trades):
        """Terminal order AND complete confirmed trade history close the risk."""
        event = normalize_order(order)
        with self.lock, self.db:
            row = self.row(key)
            if (event is None or event.order_id != row['order_id'] or event.token_id != row['token']
                    or event.side.value != 'BUY' or D(str(event.price)) != D(row['price'])
                    or D(str(event.original_size)) != D(row['quantity'])
                    or event.market_id is None):
                raise ValueError('M234_ORDER_IDENTITY_CONFLICT')
            market = json.loads(self.db.execute('SELECT metadata FROM markets WHERE slug=?',
                                               (row['slug'],)).fetchone()[0])
            if event.market_id != market['condition_id']:
                raise ValueError('M234_ORDER_MARKET_CONFLICT')
            matched = D(str(event.size_matched))
            if matched+EPS < D(row['matched']) or matched > D(row['quantity'])+EPS:
                raise ValueError('M234_ORDER_MATCHED_CONFLICT')
            for raw in raw_trades:
                trade = parse_trade(raw)
                for leg in trade.maker_legs:
                    if leg.order_id != row['order_id']:
                        continue
                    if (trade.market != market['condition_id'] or leg.asset_id != row['token']
                            or leg.side.value != 'BUY' or D(str(leg.price)) > D(row['price'])):
                        raise ValueError('M234_TRADE_IDENTITY_CONFLICT')
                    q, price, status = str(leg.matched_amount), str(leg.price), trade.status.value
                    prior = self.db.execute('SELECT * FROM trades WHERE trade_id=? AND order_id=?',
                                            (trade.trade_id, leg.order_id)).fetchone()
                    if prior and (D(prior['quantity']) != D(q) or D(prior['price']) != D(price)
                                  or prior['status'] == 'CONFIRMED' and status == 'FAILED'):
                        raise ValueError('M234_TRADE_REVISION_CONFLICT')
                    if prior and prior['status'] == 'CONFIRMED':
                        status = 'CONFIRMED'
                    self.db.execute('INSERT OR REPLACE INTO trades VALUES(?,?,?,?,?)',
                                    (trade.trade_id, leg.order_id, q, price, status))
            legs = self.db.execute('SELECT * FROM trades WHERE order_id=?', (row['order_id'],)).fetchall()
            confirmed = [t for t in legs if t['status'] == 'CONFIRMED']
            filled = sum((D(t['quantity']) for t in confirmed), D(0))
            spent = sum((D(t['quantity'])*D(t['price']) for t in confirmed), D(0))
            if filled > D(row['quantity'])+EPS or spent > D(row['cost'])+EPS:
                raise ValueError('M234_CONFIRMED_RISK_EXCEEDED')
            refs = set(event.associate_trades)
            observed_refs = {t['trade_id'] for t in legs}
            terminal_status = event.status in {'MATCHED', 'FILLED', 'CANCELED', 'CANCELLED', 'EXPIRED'}
            proven = (terminal_status and abs(matched-filled) <= EPS and refs <= observed_refs
                      and all(t['status'] in {'CONFIRMED', 'FAILED'} for t in legs))
            if event.status in {'MATCHED', 'FILLED'} and matched+EPS < D(row['quantity']):
                proven = False
            self.db.execute('''UPDATE entries SET state=?,matched=?,filled=?,spent=?,terminal=?,
                reserved=?,reason=NULL,updated=? WHERE id=?''',
                ('CLOSED' if proven else 'ACK', str(matched), str(filled), str(spent), int(proven),
                 '0' if proven else row['cost'], time.time(), key))
            self.event('ORDER_RECONCILED', {'id': key, 'status': event.status, 'matched': matched,
                'confirmed_qty': filled, 'confirmed_cost': spent, 'terminal_proven': proven})
            return proven

    def close(self):
        self.db.close()
