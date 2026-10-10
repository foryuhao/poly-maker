import pytest
import final_guards_and_diagnostics as original
from polymaker.execution.gateway import ExecutionGateway
from test_rdp_placement import placement_scope, blocked_network

@pytest.mark.asyncio
async def test_real_executor_final_clock_history(placement_scope, blocked_network, monkeypatch):
    prior = original.setup
    async def threaded_setup(*args, **kwargs):
        c, g = await prior(*args, **kwargs)
        g._io = ExecutionGateway._io.__get__(g, ExecutionGateway)
        return c, g
    monkeypatch.setattr(original, "setup", threaded_setup)
    await original.test_clock_regression_between_sign_and_post_denies_new_risk(placement_scope, blocked_network, monkeypatch)
