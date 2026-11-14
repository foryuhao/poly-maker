"""Frozen official supplements never enter signal formation or amend source data."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest
from m510_sim_replay import supplement_settlement
from m510_trade_through import SCENARIO_SETTINGS
from m511_sim import NEXT_RESEARCH_GROUPS, ResearchOnlyEngine, definition
from maker_signals.strategies import M910_DENSITY

from m1234_fixtures import fixture_events
from polymaker.m1234_sim import engine as engine_module


@pytest.mark.parametrize('case', ['valid','wrong_identity','before_end'])
def test_official_supplement_is_verified_after_end_without_new_signals(tmp_path, case):
    async def run():
        rows = deepcopy(fixture_events())
        # A pinned-code test must describe its actually loaded core, rather
        # than the mutable repository root used by the general fixture.
        core_root = Path(engine_module.__file__).resolve().parents[3]
        rows[0]['definition']['settings']['implementation'] = {
            str(p.relative_to(core_root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (core_root/'src/polymaker').rglob('*.py')}
        rows[0]['definition']['settings'].update(profiles={},scenarios=SCENARIO_SETTINGS,
            m5_m11=definition(M910_DENSITY,only_m5_m10=True,enabled_strategies=NEXT_RESEARCH_GROUPS))
        e = ResearchOnlyEngine(tmp_path/case,signal_variant=M910_DENSITY,
                               enabled_strategies=NEXT_RESEARCH_GROUPS)
        try:
            for row in rows:
                if row['kind']=='SETTLEMENT' or case=='before_end' and row['kind']=='END':
                    continue
                await e.ingest(row)
            assert e.resolution is None
            before = e.store.db.execute("SELECT count(*) FROM audit WHERE kind='SIGNAL'").fetchone()[0]
            proof = deepcopy(rows[-1]['raw'])
            if case=='wrong_identity':
                proof['conditionId'] = '0xwrong'
            observed = e.clock.wall+60
            member = {'winner':'YES','official_result':{'raw':proof,
                'observed_at':datetime.fromtimestamp(observed,timezone.utc).isoformat()}}
            if case=='valid':
                await supplement_settlement(e,member)
                assert e.resolution['winner']=='YES'
                assert e.clock.wall==observed
                assert e.store.db.execute("SELECT count(*) FROM audit WHERE kind='SIGNAL'").fetchone()[0]==before
                with pytest.raises(ValueError,match='CONFLICT'):
                    await supplement_settlement(e,{**member,'winner':'NO'})
            else:
                with pytest.raises(ValueError):
                    await supplement_settlement(e,member)
                assert e.resolution is None
        finally:
            await e.close()
    asyncio.run(run())
