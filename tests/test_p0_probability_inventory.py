"""Behavior checks for the read-only P0 execution primitives."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('p0',Path(__file__).resolve().parents[1]/'research_tools/p0_probability_inventory.py')
p0=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p0)


def test_probability_uses_both_midpoints():
    y,n=p0.Book(),p0.Book()
    y.update('BOOK',{'bids':[[.6,10]],'asks':[[.62,10]],'tick_size':.01},1)
    n.update('BOOK',{'bids':[[.38,10]],'asks':[[.4,10]],'tick_size':.01},1)
    assert abs(p0.price_prob({'YES':y,'NO':n})-.61)<1e-10


def test_taker_respects_limit_depth_and_cash():
    b={'YES':p0.Book(),'NO':p0.Book()}
    b['YES'].update('BOOK',{'bids':[[.4,10]],'asks':[[.5,3],[.51,4],[.52,8]],'tick_size':.01},1)
    run=p0.Run('C')
    q,c,f=run.execute('YES',10,b,5100)
    assert q==7 and abs(c-(3*.5+4*.51))<1e-10
    assert abs(f-(p0.fee(3,.5)+p0.fee(4,.51)))<1e-10
    run.cash=1.0
    q,c,f=run.execute('YES',5,b,5100)
    assert 0<q<3 and c+f<=1+1e-10


def test_stale_book_blocks_decision():
    run=p0.Run('C');b={'YES':p0.Book(),'NO':p0.Book()}
    for x in b.values():x.update('BOOK',{'bids':[[.4,10]],'asks':[[.5,10]]},1)
    assert run.book_ok(b,1+p0.PARAM['fresh_us'])==(True,'')
    assert run.book_ok(b,2+p0.PARAM['fresh_us'])[0] is False
