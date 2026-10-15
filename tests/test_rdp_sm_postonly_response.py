"""Actual post-only error with success=true: narrow rejection, no UNKNOWN bypass."""

import httpx
import pytest

from test_rdp_sm_entry import boundary
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows, runner


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def rejection():
    return {
        "success": False,
        "errorMsg": "invalid post-only order: order crosses book",
        "orderID": "",
        "status": "",
        "makingAmount": "",
        "takingAmount": "",
    }


def ambiguous_success(monkeypatch, v, index, patch=None):
    def response(request):
        raw_response = v.response(request)
        if request.method == "POST":
            rows = raw_response.json()
            if rows[index].get("errorMsg") == rejection()["errorMsg"]:
                rows[index]["success"] = True
                if patch:
                    patch(rows[index])
                return httpx.Response(200, json=rows, request=request)
        return raw_response

    return boundary(monkeypatch, v, response)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("filled", [0, 2])
async def test_exact_postonly_rejection_keeps_other_ack_cancel_and_confirmed_cost(
    env, monkeypatch, index, filled
):
    v, executor = await open_executor(env)
    v.replies = [
        rejection() if i == index else {"success": True, "orderID": f"o-{i + 1}"} for i in range(2)
    ]
    accepted = f"o-{2 if index == 0 else 1}"
    if filled:
        v.on_cancel = lambda venue: venue.fill(accepted, "accepted-fill", filled)
    with ambiguous_success(monkeypatch, v, index):
        result = await executor.execute_window("w1", quotes())
    assert result.placement_results[index]["outcome"] == "REJECTED"
    assert result.known_order_ids == [accepted] and v.cancels == [[accepted]]
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert result.capital_committed == pytest.approx(filled * (0.48 if index == 0 else 0.49))
    assert len(v.post_requests) == 1
    assert result.placement_results[index]["raw_response"][index]["success"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "orderID",
        "status",
        "makingAmount",
        "takingAmount",
        "tradeIDs",
        "transactionsHashes",
        "other_error",
        "missing_status",
    ],
)
async def test_contradictory_or_incomplete_error_remains_unknown(env, monkeypatch, fault):
    v, executor = await open_executor(env)
    v.replies = [rejection(), {"success": True, "orderID": "o-2"}]

    def patch(row):
        if fault == "other_error":
            row["errorMsg"] = "internal processing error"
        elif fault == "missing_status":
            row.pop("status")
        else:
            row[fault] = ["evidence"] if fault in {"tradeIDs", "transactionsHashes"} else "evidence"

    with ambiguous_success(monkeypatch, v, 0, patch):
        result = await executor.execute_window("w1", quotes())
        await executor.execute_window("w2", quotes("w2"))
    assert result.placement_results[0]["outcome"] == "UNKNOWN"
    assert result.close_barrier == "BLOCKED" and result.capital_committed == 2.45
    assert result.known_order_ids == ["o-2"] and len(v.post_requests) == 1


async def test_postonly_rejection_continues_next_normal_window(env, monkeypatch):
    v, _ = env
    v.replies = [rejection(), {"success": True, "orderID": "o-2"}]
    on_windows(v, lambda *_: None, stop_after=2)
    with ambiguous_success(monkeypatch, v, 0):
        result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(result.windows) == 2 and len(v.post_requests) == 2
    assert result.windows[0]["result"]["placement_results"][0]["outcome"] == "REJECTED"
    assert result.final_close_barrier == "SAFE_TO_CONTINUE" and result.confirmed_fills == 0


@pytest.mark.parametrize("fault", [None, "other_error", "missing_status", "tradeIDs", "short"])
def test_prior_rejection_evidence_does_not_gate_new_session(tmp_path, fault):
    from polymaker.rdp_sm_execution import RiskJournal
    from test_rdp_sm_historical_expiry import traded_records
    from test_rdp_sm_historical_risk import write_records
    from test_rdp_sm_m1 import ADDRESS

    rows = traded_records()
    reply = {**rejection(), "success": True}
    if fault == "other_error":
        reply["errorMsg"] = "internal processing error"
    elif fault == "missing_status":
        reply.pop("status")
    elif fault == "tradeIDs":
        reply["tradeIDs"] = ["possible-fill"]
    for i in (2, 3):
        payload = rows[i]["data"]["payload"]
        payload.update(
            outcome="UNKNOWN", order_id=None, raw_response=[] if fault == "short" else [reply]
        )
    rows[2]["kind"] = "sm_placement_unknown"
    path = write_records(tmp_path, rows)
    before = path.read_bytes()
    if fault is None:
        journal = RiskJournal(tmp_path, ADDRESS)
        assert journal.sequence == 0
        journal.close()
    else:
        journal = RiskJournal(tmp_path, ADDRESS)
        assert journal.sequence == 0
        journal.close()
    assert path.read_bytes() == before
