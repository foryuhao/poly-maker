import inspect
import pytest
import polymaker.rdp_runtime as runtime

@pytest.fixture(autouse=True)
def observe_actual_capital_calls(request, monkeypatch):
    if 'iter0030_independent_account_oracle_crosses' not in request.node.name:
        yield
        return
    original = runtime.project_rdp_capital
    rows = []
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshot = result.snapshot
        rows.append((inspect.currentframe().f_back.f_code.co_name,
                     result.certified, result.reason,
                     None if snapshot is None else str(snapshot.inventory_cost),
                     None if snapshot is None else str(snapshot.reserved_cash),
                     None if snapshot is None else str(snapshot.free_cash)))
        return result
    monkeypatch.setattr(runtime, 'project_rdp_capital', observe)
    yield
    print('ACTUAL_CAPITAL_OBSERVATION', request.node.name, rows)
    print('MONITOR_CAPITAL_CALLS', [row for row in rows if row[0] == '_monitor_rebalance_attempt'])
