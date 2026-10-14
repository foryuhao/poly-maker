"""Type2 capability/authority boundary; actual SM/FL readers, Fake HTTP only."""

import pytest
from py_builder_relayer_client.builder.derive import derive
from py_builder_relayer_client.config import get_contract_config

from polymaker.rdp_sm import SessionStatus, _Issue, run_preflight
from polymaker.rdp_sm_execution import SMWindowExecutor, _ExecutionReads
from test_rdp_sm_m1 import ADDRESS, SLUG
from test_rdp_sm_m2_execution import env as execution_env
from test_rdp_sm_m2_execution import journal_records, open_executor, quotes


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from execution_env.__wrapped__(monkeypatch, tmp_path)


def type2(env, funder=None):
    venue, cfg = env
    expected = derive(ADDRESS, get_contract_config(cfg.wallet.chain_id).safe_factory)
    cfg.wallet.signature_type = 2
    cfg.secrets.browser_address = funder or expected
    venue.funder = cfg.secrets.browser_address
    return venue, cfg, expected


async def test_type2_existing_sdk_and_readers_bind_signer_mode_and_funder(env):
    venue, cfg, expected = type2(env)
    reads = _ExecutionReads(cfg)
    try:
        await reads.connect_account()
        client = reads.gateway._client
        assert client.get_address() == reads.gateway.address == ADDRESS
        assert client.builder.funder == reads.gateway.funder == expected
        assert expected != ADDRESS and client.builder.signature_type == 2
        assert (await reads.gateway.balance_allowance())["balance"] == "100000000"
        assert await reads.gateway.positions() == {}
        assert await reads.gateway.open_orders() == []
    finally:
        reads.close()
    assert any(
        path == "/balance-allowance" and params["signature_type"] == "2"
        for _, path, params in venue.calls
    )
    assert any(
        path == "/v2/positions" and params["user"] == expected for _, path, params in venue.calls
    )
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.READY
    assert result.account["signer"] == ADDRESS and result.account["funder"] == expected
    assert result.account["signature_type"] == 2
    assert result.account["basic_spendable_budget_usd"] == 20
    assert not venue.posts and not venue.cancels


@pytest.mark.parametrize("wrong_funder", [False, True])
async def test_type2_verified_mapping_admitted_and_wrong_funder_blocked(
    env, wrong_funder, tmp_path
):
    venue, cfg, expected = type2(env, "0x" + "33" * 20 if wrong_funder else None)
    assert (cfg.secrets.browser_address != expected) is wrong_funder
    if wrong_funder:
        with pytest.raises(_Issue, match="M2_REQUIRES_PROVEN_EOA_OR_DERIVED_SAFE") as error:
            await SMWindowExecutor.open(cfg, SLUG, "wrong-type2")
        assert error.value.status is SessionStatus.BLOCKED
        assert not venue.posts and not venue.cancels
        assert not list(tmp_path.iterdir())
    else:
        _, executor = await open_executor(env)
        result = await executor.execute_window("w1", quotes())
        assert len(venue.posts) == 2 and result.close_barrier == "SAFE_TO_CONTINUE"
        for body in venue.posts:
            signed = body[0]["order"]
            assert signed["signatureType"] == 2
            assert signed["maker"] == expected and signed["signer"] == ADDRESS
        session = next(
            r for r in journal_records(executor.journal.path) if r["kind"] == "sm_session"
        )
        assert session["data"]["payload"]["live_authority"] == "NOT_VERIFIED_FOR_LIVE"


async def test_type0_supported_execution_has_no_regression(env):
    venue, executor = await open_executor(env)
    assert executor.cfg.wallet.signature_type == 0
    assert executor.gateway.address == executor.gateway.funder == ADDRESS
    result = await executor.execute_window("w1", quotes())
    assert len(venue.posts) == 2 and result.close_barrier == "SAFE_TO_CONTINUE"
    assert result.yes_inventory == result.no_inventory == result.capital_committed == 0


@pytest.mark.parametrize("mutation", ["key", "sdk_funder", "signature_type"])
async def test_type2_current_config_or_sdk_identity_change_blocks_submission(env, mutation):
    venue, cfg, _ = type2(env)
    _, executor = await open_executor(env)
    if mutation == "key":
        cfg.secrets.pk = "0x" + "02" * 32  # another public deterministic dummy key
    elif mutation == "sdk_funder":
        executor.gateway._client.builder.funder = "0x" + "33" * 20
    else:
        cfg.wallet.signature_type = 0
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.block_reason == "ACCOUNT_IDENTITY_MISMATCH"
    assert not venue.posts and not venue.cancels
