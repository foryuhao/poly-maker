"""M4-only synthetic market, SDK and ledger helpers. No other test imports."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

from polymaker.config import RDPObiConfig
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.m4_config import STRATEGY, M4Config
from polymaker.m4_control import StopControl
from polymaker.m4_execution import M4Market
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_run import Executor
from polymaker.m4_signals import M4Feed
from polymaker.m4_state import M4Ledger
from polymaker.m4_types import Opportunity
from polymaker.m4_virtual import VirtualReads
from polymaker.rdp_sm_writer import AsyncRiskWriter


def feed_fixture():
    epoch=1800000000
    meta=MarketMeta(condition_id='0x'+'ab'*32, question='Synthetic M4 only',
        slug=f'btc-updown-5m-{epoch}',tokens=(TokenMeta('1','Up'),TokenMeta('2','Down')),
        tick_size=.01, neg_risk=False, min_order_size=5., rewards_min_size=0.,
        rewards_max_spread=0.,rewards_daily_rate=0.,maker_fee_bps=0,taker_fee_bps=0,
        fees_enabled=False,end_date_iso=datetime.fromtimestamp(epoch+300,UTC).isoformat(),event_id=None)
    clock=SimpleNamespace(now=0.)
    feed=M4Feed(meta,RDPObiConfig(),monotonic=lambda:clock.now,wall=lambda:epoch+20+clock.now)
    feed.verify_ticks({'1':D('.01'),'2':D('.001')})
    feed.connect()
    rows=[{'event_type':'book','market':meta.condition_id,'asset_id':t,
        'timestamp':str((epoch+20)*1000),'bids':[{'price':'.50','size':'100'}],
        'asks':[{'price':'.51','size':'100'}]} for t in ('1','2')]
    feed.apply(rows)
    return feed,clock,rows


def advance(feed,clock,target):
    while clock.now<target-1e-8:
        clock.now=round(min(target,clock.now+.025),6)
        feed.apply([{'event_type':'book','market':feed.meta.condition_id,'asset_id':t,
            'timestamp':str(feed.wall()*1000),
            'bids':[{'price':str(p),'size':str(q)} for p,q in b.bids],
            'asks':[{'price':str(p),'size':str(q)} for p,q in b.asks]} for t,b in feed.books.items()])


def queue_hit(feed,clock,at,side='ASK',recovery=D(1),qty=D(10)):
    advance(feed,clock,at)
    book=feed.books['1']
    price=book.best_ask if side=='ASK' else book.best_bid
    initial=book.best_ask_size if side=='ASK' else book.best_bid_size
    feed.apply({'event_type':'last_trade_price','market':feed.meta.condition_id,'asset_id':'1',
        'timestamp':str(feed.wall()*1000),'side':'BUY' if side=='ASK' else 'SELL',
        'price':str(price),'size':str(qty)})
    for quantity in (initial-qty,initial-qty+qty*recovery):
        feed.apply({'event_type':'price_change','market':feed.meta.condition_id,
            'timestamp':str(feed.wall()*1000),'price_changes':[{'asset_id':'1',
            'side':'SELL' if side=='ASK' else 'BUY','price':str(price),'size':str(quantity)}]})
    advance(feed,clock,at+.525)

def identity(mode='VIRTUAL'):
    return dict(strategy=STRATEGY, mode=mode, funder='synthetic', market_id='synthetic',
                settings_hash=M4Config().fingerprint)


def ledger(tmp_path, mode='VIRTUAL'):
    book = M4Ledger(tmp_path/'risk.sqlite3', identity(mode))
    book.set('fill_tracking', 'NOT_TRACKED')
    return book


def op(i, price='.70', token='1', support='support'):
    return Opportunity(f'df{i}', token, 'YES' if token == '1' else 'NO', D(price),
                       1., 6., support, f'df{i}', 1)


def send(book, i, price='.70', token='1', state='UNKNOWN'):
    row = book.observe(op(i, price, token), minimum=5, elapsed=20)
    assert book.reserve(row['id'], spendable=100)
    quote = {'market_id':'0x'+'ab'*32, 'window_id':row['id'], 'outcome':row['outcome'],
             'side': 'BUY', 'token_id': token, 'price': price, 'size': row['quantity']}
    book.prepare(row['id'], '0x'+f'{i:064x}', quote)
    book.post_start(row['id'])
    book.outcome(row['id'], state)


def fixture_feed(price='.70'):
    old, clock, rows = feed_fixture()
    feed = M4Feed(old.meta, old.cfg, monotonic=old.monotonic, wall=old.wall)
    feed.verify_ticks({t:b.tick for t,b in old.books.items()})
    feed.connect()
    for r in rows:
        r['bids'] = [{'price':price,'size':'100'}]
        r['asks'] = [{'price':str(D(price)+D('.01')),'size':'100'}]
    feed.apply(rows)
    return feed, clock


def form(feed,clock):
    queue_hit(feed,clock,.02)
    queue_hit(feed,clock,.60,side='BID')
    queue_hit(feed,clock,1.20,recovery=D('.2'),qty=D(25))
    feed.m1.evaluate(feed.snapshot())


async def setup(tmp_path, *, live=False, price='.70', touch_model=False, fill_policy=None):
    feed, clock = fixture_feed(price)
    form(feed,clock)
    b = ledger(tmp_path, 'LIVE' if live else 'VIRTUAL')
    if touch_model:
        from polymaker.m4_fill_model import POLICY
        b.set('baseline_fill_model', fill_policy or POLICY)
    reads = VirtualReads(feed,b,None)
    sid = ('m4_live_' if live else 'm4_virtual_')+uuid4().hex
    backing = M4RiskJournal(tmp_path/'journal','0x'+'1'*40,session_id=sid)
    journal = AsyncRiskWriter(backing)
    executor = Executor(None,feed.meta.slug,sid,reads,journal,ledger=b,
        control=StopControl(tmp_path,live=live),live=live,monotonic=feed.monotonic)
    executor.meta = feed.meta
    executor.official_time = int(feed.meta.slug.rsplit('-',1)[1])+20
    executor.time_anchor = clock.now
    executor.stop_event = asyncio.Event()
    async def cash():
        return {'official_balance_usd':'40','applicable_allowance_usd':'40','source':'MOCK'}
    executor.account_snapshot = cash
    market = M4Market(executor,M4Config(),executor.stop_event,display=lambda _:None)
    await market.refresh_account()
    assert market.account_reason is None
    market.collect()
    return market,executor,feed,clock


async def close(m,e):
    await m.drain()
    await e.journal.flush()
    e.close()
    m.ledger.close()
