"""Real SDK tick resolution, stale market-info cache and verified live updates."""

import asyncio
import json
from decimal import Decimal as D
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
from m4_helpers import close
from py_clob_client_v2.client import ClobClient
from test_m4_live_best_ask import fixture, move
from test_m4_live_task import evidence, serve


def real_sdk(client, monkeypatch, cached="0.01"):
    # The older generic fixtures replace get_tick_size on the instance. Remove
    # that replacement so this regression exercises the installed SDK resolver.
    monkeypatch.delattr(client, "get_tick_size", raising=False)
    client._ClobClient__tick_sizes["1"] = cached


async def test_reproduce_stale_sdk_rejection_then_same_intent_posts_with_verified_tick(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98")
    client = e.gateway._client
    ep = move(m, f, c, age=0, ask=".99", tick=".001")
    real_sdk(client, monkeypatch)
    try:
        # Reproduce the exact production failure using the SDK's original
        # cache reader. This fails before signing/POST, with no order ID.
        monkeypatch.setattr(client, "get_tick_size", MethodType(ClobClient.get_tick_size, client))
        await m.submit(ep)
        assert not calls and not m.ledger.attempts()
        await e.journal.flush()
        records = (await asyncio.to_thread(Path(e.journal.journal.path).read_text)).splitlines()
        errors = [json.loads(line)["data"]["payload"]
                  for line in records
                  if json.loads(line)["kind"] == "sm_m4_live_post_error"]
        assert any("invalid tick size (0.001), minimum for the market is 0.01"
                   in x["reason"] and x["order_id"] is None for x in errors)
        # Keep the stale private SDK cache untouched; live signing now reads
        # the same officially verified tick as the final native guard.
        monkeypatch.delattr(client, "get_tick_size")
        assert ClobClient.get_tick_size(client, "1") == "0.01"
        assert client.get_tick_size("1") == "0.001"
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.row(ep.episode_id)["state"] == "ACK"
        plan = json.loads(m.ledger.attempts()[0]["plan"])
        assert D(plan["limit_price"]) == D(".99") and D(plan["quantity"]) == 5
        assert D(plan["planned_cost_usdc"]) <= 5 and m.ledger.committed() <= 10
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("age,ask,tick,cached,expected", [
    (0, ".99", ".001", "0.01", ".99"),
    (10, ".998", ".001", "0.01", ".998"),
    (12, ".994", ".001", "0.01", ".994"),
    (10, ".9980", ".0001", "0.01", ".9980"),
    (10, ".99", ".01", "0.001", ".99"),
    (10, ".80", ".1", "0.001", ".80"),
])
async def test_signing_uses_verified_tick_in_both_pricing_phases(tmp_path, monkeypatch, age, ask, tick, cached, expected):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98", response="KILLED")
    ep = move(m, f, c, age=age, ask=ask, tick=tick)
    client = e.gateway._client
    real_sdk(client, monkeypatch, cached)
    try:
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1
        plan = json.loads(m.ledger.attempts()[0]["plan"])
        assert D(plan["limit_price"]) == D(expected)
        assert D(plan["principal_usdc"]) % D(".01") == 0
        assert D(plan["quantity"]) % D(".1") == 0
        assert D(plan["planned_cost_usdc"]) <= 5
        assert client._ClobClient__tick_sizes["1"] == cached
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("fault,reason", [
    ("unverified", "M4_TAKER_TICK_UNVERIFIED"),
    ("foreign", "M4_TAKER_TICK_TOKEN_FOREIGN"),
    ("unbound", "M4_TAKER_TICK_PROVIDER_UNBOUND"),
])
async def test_cache_cannot_override_missing_or_foreign_verification(tmp_path, monkeypatch, fault, reason):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    client = e.gateway._client
    real_sdk(client, monkeypatch)
    token = "1"
    try:
        if fault == "unverified":
            f.tick_verified.pop(token)
        elif fault == "foreign":
            token = "foreign"
            client._ClobClient__tick_sizes[token] = "0.01"
        else:
            monkeypatch.delattr(client, "m4_verified_tick_provider")
        with pytest.raises(ValueError, match=reason):
            client.get_tick_size(token)
        assert not calls
    finally:
        await close(m, e)
        t.close()


async def test_verified_coarser_tick_during_signing_rejects_old_fine_options(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98")
    client = e.gateway._client
    real_sdk(client, monkeypatch, "0.001")
    ep = move(m, f, c, age=12, ask=".998", tick=".001")
    await m.refresh_account()
    original = e.gateway.prepare

    async def coarsen(*args, **kwargs):
        move(m, f, c, age=12, ask=".99", tick=".01")
        return await original(*args, **kwargs)

    monkeypatch.setattr(e.gateway, "prepare", coarsen)
    try:
        await m.submit(ep)
        assert not calls and not m.ledger.attempts()
        monkeypatch.setattr(e.gateway, "prepare", original)
        await m.submit(ep)
        assert len(calls) == 1
        assert D(json.loads(m.ledger.attempts()[0]["plan"])["limit_price"]) == D(".99")
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("change", ["cutoff", "unverify"])
async def test_verified_sdk_tick_does_not_bypass_final_native_protection(tmp_path, monkeypatch, change):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98")
    real_sdk(e.gateway._client, monkeypatch)
    ep = move(m, f, c, age=12, ask=".998", tick=".001")
    await m.refresh_account()
    original = m.ledger.post_start

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "cutoff":
            e.official_time = int(e.slug.rsplit("-", 1)[1])+240
            e.time_anchor = c.now
        else:
            f.tick_verified.pop("1")
        return result

    monkeypatch.setattr(m.ledger, "post_start", changed)
    try:
        await m.submit(ep)
        assert not calls
        a = m.ledger.attempts()[0]
        assert a["state"] == "UNSENT" and a["started"] == 1
        assert not m.ledger.unresolved(ep.episode_id)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_restart_unknown_keeps_old_order_before_verified_fine_tick_retry(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98", response="TIMEOUT")
    real_sdk(e.gateway._client, monkeypatch)
    ep = move(m, f, c, age=12, ask=".998", tick=".001")
    await m.refresh_account()
    await m.submit(ep)
    oid = m.ledger.attempts()[0]["order_id"]
    assert len(calls) == 1 and m.ledger.unresolved(ep.episode_id)
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98", recover=True)
    real_sdk(e.gateway._client, monkeypatch)
    q = None
    try:
        move(m, f, c, age=15, ask=".998", tick=".001")
        await m.refresh_account()
        assert not m.guard(ep) and not calls
        order, trade = evidence(m, e, oid, "0")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.attempts()[-1]["order_id"] != oid
        assert D(json.loads(m.ledger.attempts()[-1]["plan"])["limit_price"]) == D(".998")
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


@pytest.mark.parametrize("status", ["CONFIRMED", "MINED"])
async def test_verified_fine_tick_partial_fill_only_retries_known_cash(tmp_path, monkeypatch, status):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98")
    real_sdk(e.gateway._client, monkeypatch)
    ep = move(m, f, c, age=0, ask=".99", tick=".001")
    q = None
    try:
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "2", status=status)
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        used = m.ledger.budget_used(ep.episode_id)
        move(m, f, c, age=12, ask=".996", tick=".001")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 2
        plan = json.loads(m.ledger.attempts()[-1]["plan"])
        assert D(plan["prior_spent_usdc"]) == used > 0
        assert D(plan["limit_price"]) == D(".996")
        assert D(plan["planned_cost_usdc"]) + used <= 5
        assert D(plan["principal_usdc"]) % D(".01") == 0
        assert D(plan["quantity"]) % D(".1") == 0
        assert not m.guard(ep)  # The new ACK cannot cause a duplicate POST.
        assert len(calls) == 2 and m.ledger.committed() <= 10
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_non_budget_client_retains_sdk_behavior(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    client = e.gateway._client
    real_sdk(client, monkeypatch, "0.01")
    monkeypatch.setattr(client, "m4_ledger", SimpleNamespace(cash_target=False))
    try:
        assert client.get_tick_size("1") == "0.01"
        assert not calls
    finally:
        await close(m, e)
        t.close()
