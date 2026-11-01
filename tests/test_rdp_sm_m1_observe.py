"""Public observer isolation and real detector-to-marker boundary."""

import asyncio
import gzip
import json
import subprocess
import sys
import time
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from polymaker.config import Config, RDPBAConfig, RDPM1Config
from polymaker.rdp_sm_fence import AccountLease
from polymaker.rdp_sm_m1_observe import (
    PublicClob,
    PublicProposal,
    load_settings,
    observe,
    observer_lock,
    prepare_output,
    public_market,
)
from polymaker.rdp_sm_m1_writer import M1ObserverWriter
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_m1 import SLUG, START, Venue
from test_rdp_sm_m1_strategy import advance, formation, m1_feed

PROJECT = Path(__file__).resolve().parents[1]


def test_observation_writer_preserves_raw_rejections_and_signal_facts(tmp_path):
    writer = M1ObserverWriter(tmp_path/"events.jsonl.gz")
    rows = [{"event": "RAW_FRAME", "raw": {"book": [1, 2, 3]}},
            {"event": "EVENT_DECISION", "accepted": True},
            {"event": "EVENT_DECISION", "accepted": False, "reason": "M1_SOURCE_AGE"},
            {"event": "RF_CONFIRMED", "rf_id": "rf:1"}]
    for row in rows:
        assert writer.submit(row)
    writer.close()
    with gzip.open(writer.path, "rt") as file:
        actual = [json.loads(line) for line in file]
    assert [{k: v for k, v in row.items() if k != "written_wall"} for row in actual] == [rows[0], rows[2], rows[3]]
    assert writer.status()["filtered_accepted_decisions"] == 1
    assert writer.written == writer.accepted == 3 and not writer.failure and not writer.dropped


def test_public_entry_does_not_import_account_or_execution_code():
    script = """
import importlib.abc, sys
blocked = ('polymaker.rdp_sm', 'polymaker.rdp_sm_execution',
           'polymaker.execution', 'polymaker.rdp_sm_fence',
           'polymaker.catalog.store', 'py_clob_client_v2', 'web3', 'eth_account')
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p+'.') for p in blocked):
            raise AssertionError('Private dependency: '+fullname)
sys.meta_path.insert(0, BlockPrivate())
from polymaker.rdp_sm_m1_observe import load_settings
cfg, *_ = load_settings('config-m1')
assert cfg.observation_only and cfg.entry_start_sec == 15
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=PROJECT,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_observer_config_never_loads_dotenv_or_wallet(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Account configuration accessed")
    monkeypatch.setattr(Config, "load", forbidden)
    cfg, _, _, _ = load_settings(PROJECT / "config-m1")
    assert cfg.observation_only and cfg.entry_start_sec == 15 and cfg.start_sec == 100


def test_public_metadata_maps_reversed_outcomes_by_label():
    meta = public_market(Venue().raw, SLUG)
    assert meta.yes.token_id == "1" and meta.no.token_id == "2"


@pytest.mark.parametrize("mutation", [
    {"outcomes": '["Up", "Up"]'}, {"clobTokenIds": '["1", "1"]'},
    {"closed": True}, {"active": False}, {"conditionId": "wrong"},
])
def test_public_metadata_rejects_ambiguous_identity(mutation):
    raw = {**deepcopy(Venue().raw), **mutation}
    with pytest.raises(ValueError):
        public_market(raw, SLUG)


async def test_public_rest_uses_only_unauthenticated_gets():
    requests = []
    def transport(request):
        requests.append(request)
        assert request.method == "GET"
        assert not any(k.lower().startswith("poly_") for k in request.headers)
        if request.url.path == "/time":
            return httpx.Response(200, json=START+20)
        assert request.url.path == "/tick-size"
        return httpx.Response(200, json={"minimum_tick_size": ".01"})
    public = PublicClob()
    await public.client.aclose()
    public.client = httpx.AsyncClient(base_url="https://clob.polymarket.com",
                                      transport=httpx.MockTransport(transport))
    try:
        assert (await public.clock())[0] == START+20
        assert await public.ticks(["1", "2"]) == {"1": Decimal(".01"), "2": Decimal(".01")}
    finally:
        await public.close()
    assert len(requests) == 3


def proposal_fixture():
    feed, clock = m1_feed()
    formation(feed, clock)
    return feed, clock, PublicProposal(feed, RDPM1Config(observation_start_sec=15), RDPBAConfig())


def test_real_rf_ex_marks_once_without_claiming_orders_or_fills():
    feed, _, proposal = proposal_fixture()
    marker = proposal.poll(20, 21)
    assert marker and marker["side"] == "BUY" and marker["size"] == "10.00"
    assert marker["account_admission"] == "NOT_CHECKED" and not marker["post_started"]
    assert marker["scope"] == "PUBLIC_SIGNAL_QUOTE_TIMING_ONLY"
    assert marker["remaining_ms"] >= 450
    assert proposal.poll(20, 21) is None
    assert feed.m1.claimed is None and not feed.m1.own_active and not feed.m1.used_rf
    assert feed.m1.counts["WOULD_POST"] == 1


@pytest.mark.parametrize("lower,upper", [(14.99, 15.99), (239, 240), (240, 241)])
def test_marker_respects_conservative_time_bounds(lower, upper):
    _, _, proposal = proposal_fixture()
    assert proposal.poll(lower, upper) is None
    assert proposal.reason == "M1_OBSERVATION_WINDOW"


def test_queued_input_and_expiring_evidence_cannot_mark():
    feed, clock, proposal = proposal_fixture()
    feed.m1_pending_received.append(clock.now)
    assert proposal.poll(20, 21) is None and proposal.reason == "M1_INPUT_PENDING"
    feed.m1_pending_received.clear()
    advance(feed, clock, clock.now+.3)
    assert proposal.poll(20, 21) is None
    assert proposal.reason == "M1_EXECUTION_BUDGET_INSUFFICIENT"


def test_output_cannot_enter_ca_journal_and_stops_on_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="M1_OUTPUT_MUST"):
        prepare_output(Path("journal"), 256, b"config")
    assert not Path("journal").exists()
    with pytest.raises(RuntimeError, match="M1_OUTPUT_BUDGET_REACHED"):
        prepare_output(Path("research_outputs/m1_isolated"), 0, b"config")


def test_only_one_public_observer_can_run_without_account_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with observer_lock(), pytest.raises(RuntimeError, match="M1_PUBLIC_OBSERVER_ALREADY_RUNNING"), observer_lock():
        pass
    with observer_lock():
        assert not (tmp_path / "journal").exists()


async def test_observer_runs_while_ca_account_lease_is_held(tmp_path, monkeypatch):
    import polymaker.rdp_sm_m1_observe as module

    monkeypatch.chdir(tmp_path)
    lease = AccountLease(SimpleNamespace(paths=SimpleNamespace(journal_dir=tmp_path/"journal")), "ca-account")
    lease.write({"status": "ACTIVE_CA_TEST"})
    before = lease.path.read_bytes(), lease.path.stat().st_mtime_ns
    public_calls = []
    class Public:
        async def clock(self):
            now = time.monotonic()
            return START+20, now, now
        async def ticks(self, tokens):
            public_calls.append("ticks")
            return dict.fromkeys(tokens, Decimal(".01"))
        async def close(self):
            public_calls.append("closed")
    class Gamma:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def market_by_slug(self, slug):
            assert slug == SLUG
            return Venue().raw
    async def fake_feed_run(self):
        assert self.ba is None and self.ca is None
        self.connect()
        self.apply([{"event_type": "book", "market": self.meta.condition_id,
                     "asset_id": t, "timestamp": str(time.time()*1000),
                     "bids": [{"price": ".50", "size": "80"}],
                     "asks": [{"price": ".52", "size": "20"}]}
                    for t in self.books])
        await self.stop_event.wait()
    def forbidden(*args, **kwargs):
        raise AssertionError("Account/config boundary crossed")
    monkeypatch.setattr(module, "PublicClob", Public)
    monkeypatch.setattr(module, "GammaClient", Gamma)
    monkeypatch.setattr(SessionFeed, "run", fake_feed_run)
    monkeypatch.setattr(AccountLease, "__init__", forbidden)
    monkeypatch.setattr(Config, "load", forbidden)
    try:
        result = await observe(PROJECT/"config-m1", max_seconds=.08)
        assert result["execution_status"] == "PUBLIC_OBSERVATION_ONLY"
        assert result["stop_reason"] == "M1_DURATION_LIMIT"
        assert result["writer"]["written"] > 0 and not result["writer"]["failure"]
        assert before == (lease.path.read_bytes(), lease.path.stat().st_mtime_ns)
        assert not (tmp_path / "state.db").exists()
        assert list((tmp_path / "journal").iterdir()) == [lease.path]
        assert public_calls == ["ticks", "closed"]
        assert (tmp_path / result["session_dir"] / "result.json").is_file()
        assert not [task for task in asyncio.all_tasks() if task.get_name() == "m1-public-feed"]
    finally:
        lease.close()
