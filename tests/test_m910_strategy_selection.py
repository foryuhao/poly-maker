"""Retired strategies cannot form candidates or obtain simulation accounts."""
import asyncio
from copy import deepcopy

import pytest
from m510_trade_through import SCENARIO_SETTINGS
from m510_frequency_grid import attach_profiles
from m511_sim import NEXT_RESEARCH_GROUPS, ResearchOnlyEngine, definition
from m511_sim_instant_fill import ResearchInstantFill
from m511_sim_metrics import collect
from maker_signals.strategies import M5_M10_LOCAL_AP, Strategies

from m1234_fixtures import fixture_events
from test_m511_strategies import H


def test_disabled_m7_and_m8_cannot_start_or_create_private_ap_observers():
    h = H(M5_M10_LOCAL_AP)
    h.s = Strategies(h.c, signal_variant=M5_M10_LOCAL_AP,
                     enabled_strategies=NEXT_RESEARCH_GROUPS,
                     frequency_parameters={'M7': {'ap_window_seconds': .25}})
    h.ap()
    assert h.s.active('M7', 'up') is None
    assert not any(s == 'M7' for s, token in h.s.private_ap)
    assert h.s.start('M8', 'up', 'disabled-root', 20) is None
    assert not any(p.get('payload', {}).get('strategy') in {'M7', 'M8'}
                   for k, p in h.events if k == 'RESEARCH_EVIDENCE'
                   and p.get('event') == 'CAMPAIGN_STARTED')
    assert h.s.active('M10', 'up') is not None


def test_requested_scope_is_bound_into_definition_and_simulation_accounts(tmp_path):
    async def run():
        events = deepcopy(fixture_events())
        cfg = events[0]['definition']['settings']
        cfg.update(profiles={}, scenarios=SCENARIO_SETTINGS,
                   m5_m11=definition(M5_M10_LOCAL_AP, only_m5_m10=True,
                                    enabled_strategies=NEXT_RESEARCH_GROUPS))
        source = tmp_path/'selected'
        source.mkdir()
        import json
        (source/'plan.json').write_text(json.dumps({'settings': cfg,
                         'run_identity': 'reduced-scope-fixture', 'mode': 'SIMULATION_ONLY',
                         'real_order_transport': 'ABSENT'}))
        engine = ResearchOnlyEngine(source/events[0]['definition']['market']['slug'], signal_variant=M5_M10_LOCAL_AP,
                                    enabled_strategies=NEXT_RESEARCH_GROUPS)
        engine.store.run_owner = 'reduced-scope-fixture'
        for event in events:
            await engine.ingest(event)
        expected = set(NEXT_RESEARCH_GROUPS)
        assert set(engine.groups) == expected
        assert set(engine.brokers) == {'research/trade-through'}
        assert set(engine.brokers['research/trade-through'].accounts) == expected
        assert set(cfg['m5_m11']['strategy_versions']) == expected
        assert engine.research_extension.research_signals.enabled_strategies == expected
        await engine.close()
        metrics = collect(source, tmp_path/'metrics-cache.json')
        assert set(metrics['modes']['research']['strategies']) == expected
        ledger = ResearchInstantFill(source, tmp_path/'assumed', 'research/trade-through')
        try:
            report = ledger.refresh()
            assert set(report['strategies']) == expected
            assert report['status'] == 'OK'
        finally:
            ledger.close()
        engine.retire_runtime()
    asyncio.run(run())


@pytest.mark.parametrize('groups', [[], ['M5', 'M5'], ['M11'], ['M4'], ['M5', 'bogus']])
def test_bad_scope_is_rejected_before_creating_output(tmp_path, groups):
    with pytest.raises(ValueError, match='STRATEGY_SELECTION'):
        ResearchOnlyEngine(tmp_path/'invalid', enabled_strategies=groups)
    assert not (tmp_path/'invalid').exists()


def test_reduced_scope_cannot_be_falsely_used_with_a_six_strategy_definition(tmp_path):
    async def run():
        event = deepcopy(fixture_events()[0])
        event['definition']['settings']['m5_m11'] = definition(only_m5_m10=True)
        engine = ResearchOnlyEngine(tmp_path/'mismatch', enabled_strategies=NEXT_RESEARCH_GROUPS)
        try:
            with pytest.raises(ValueError, match='DEFINITION_MISSING_OR_CHANGED'):
                await engine.ingest(event)
        finally:
            await engine.close()
    asyncio.run(run())


def test_shadow_inherits_actual_baseline_and_cannot_reenable_retired_strategies():
    h = H(M5_M10_LOCAL_AP)
    primary = Strategies(h.c, signal_variant=M5_M10_LOCAL_AP,
                         frequency_parameters={'M10': {'ap_window_seconds': .5,
                                                       'round_seconds': .25,
                                                       'hold_seconds': .01}},
                         enabled_strategies=NEXT_RESEARCH_GROUPS)
    managers = attach_profiles(h.c, primary, {'baseline': {}, 'candidate': {
        'M10': {'stable_recovery': '.40'}}}, 'baseline',
        profile_targets={'candidate': ['M10']})
    shadow = managers['candidate']
    assert shadow.frequency_parameters['M10']['ap_window_seconds'] == .5
    assert shadow.frequency_parameters['M10']['round_seconds'] == .25
    assert shadow.frequency_parameters['M10']['hold_seconds'] == .01
    assert shadow.enabled_strategies == {'M10'}
    assert not any(s == 'M7' for s, t in shadow.private_ap)
    assert primary.frequency_parameters['M10']['stable_recovery'] == '.50'
