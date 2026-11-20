"""Fixed-market frequency gate must precede all economic inspection."""
import pytest

from m1223_variant_replay import frequency_result, evaluate_pnl


def markets(signal_markets=10, isolated=0):
    return [{'market':f'btc-updown-5m-{1800000000+i*300}',
             'signal_count':25 if i<signal_markets else 0,
             'primary_eligible':i>=isolated,'complete_window':i!=5,'has_unknown':True}
            for i in range(24)]


@pytest.mark.parametrize('n,met', [(0,False),(9,False),(10,True),(24,True)])
def test_distinct_market_coverage_not_signal_multiplicity(n,met):
    r=frequency_result(markets(n),{'run_id':'test'},{'revision_round':0})
    assert r['primary_distinct_signal_markets']==n
    assert r['frequency_target_met'] is met and r['denominator']==24
    assert r['primary_signal_count']==n*25 and len(r['unknown_markets'])==24
    assert not r['pnl_evaluated'] and not r['pnl_used_for_selection']


def test_isolation_retained_in_denominator_and_does_not_count_toward_primary_gate():
    r=frequency_result(markets(10,1),{},{});
    assert len(r['all_recorded_signal_markets'])==10
    assert r['primary_distinct_signal_markets']==9 and not r['frequency_target_met']
    assert r['denominator']==24 and r['complete_windows']==23


@pytest.mark.parametrize('change',['missing','duplicated'])
def test_no_subset_or_replacement_coverage(change):
    m=markets()
    if change=='missing':m.pop()
    else:m[-1]=m[0]
    with pytest.raises(ValueError,match='ALL_FIXED24'):frequency_result(m,{},{})


def test_pnl_reader_refuses_before_target_without_opening_any_database(tmp_path):
    r=frequency_result(markets(9),{},{})
    with pytest.raises(ValueError,match='PNL_REQUIRES'):evaluate_pnl(tmp_path,r)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.asyncio
async def test_frequency_only_never_reads_pnl_even_after_all24_pass(tmp_path,monkeypatch):
    import m1223_variant_replay as replay
    import m1223_sim
    import sop_signals.parameters as loader
    monkeypatch.setattr(replay,'validate_dataset',lambda _:markets())
    monkeypatch.setattr(loader,'load_revision',lambda _:(None,{'revision_round':0}))
    monkeypatch.setattr(m1223_sim,'definition',lambda _:{'variant_id':'M21_ENDPOINT_COFLOW','strategy':'M21'})
    async def simulate(*args):pass
    monkeypatch.setattr(replay,'replay_market',simulate)
    from types import SimpleNamespace
    monkeypatch.setattr(replay.shutil,'disk_usage',lambda _:SimpleNamespace(free=8*1024**3))
    facts={m['market']:m for m in markets()}
    monkeypatch.setattr(replay,'frequency_market',lambda p,owner:facts[p.parent.name])
    def forbidden(*_):raise AssertionError('PNL_READER_CALLED_DURING_FREQUENCY_ONLY_WORK')
    monkeypatch.setattr(replay,'evaluate_pnl',forbidden)
    out=tmp_path/'new-replay'
    result=await replay.run({'strategy':'M19','capture_sop_definition':{'strategy':'M19'}},{},out,{},frequency_only=True)
    assert result['frequency_target_met'] and not result['pnl_evaluated']
    assert (out/'frequency-decision.json').exists() and not (out/'pnl-report.json').exists()
