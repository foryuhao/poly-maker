"""Refresh prices at M2 admission; preserve sides, size, capital and revision."""

import pytest

from test_rdp_sm_entry import boundary
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows, runner


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("expensive", [False, True])
async def test_normal_uses_final_snapshot_prices_and_rechecks_pair_budget(
    env, monkeypatch, expensive
):
    v, _ = env
    v.capital["balance"] = "6000000"
    reads = {"1": 0, "2": 0}
    on_windows(v, lambda *_: None, stop_after=1)

    def mutate(request):
        if request.url.path == "/book" and v.executor is not None:
            token = request.url.params["token_id"]
            reads[token] += 1
            # M3 decision is read1; M2 reads the final book once after account admission.
            if reads[token] >= 2:
                bid = (
                    ".80"
                    if expensive
                    else ".40"
                    if token == "1"
                    else ".29"
                )
                ask = (
                    ".90"
                    if expensive
                    else ".41"
                    if token == "1"
                    else ".30"
                )
                v.books[token]["bids"][0]["price"] = bid
                v.books[token]["asks"][0]["price"] = ask
        return None

    with boundary(monkeypatch, v, mutate):
        result = await runner(env).run(offline=True)
    if expensive:
        assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
        assert not v.post_requests and result.confirmed_fills == 0
        return
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 2
    window = result.windows[0]["result"]
    assert {q["outcome"]: (q["price"], q["size"]) for q in window["requested_quotes"]} == {
        "YES": (0.39, 5),
        "NO": (0.289, 5),
    }
    assert [p["quote"] for p in window["placement_results"]] == window["requested_quotes"]
    assert all(p["outcome"] == "ACK" for p in window["placement_results"])
    assert window["fill_deadline_monotonic"] - window["t0_monotonic"] == 3
    assert result.skipped_quotes == [] and result.final_close_barrier == "SAFE_TO_CONTINUE"


@pytest.mark.parametrize("fault", [None, "boolean_count", "unknown_summary"])
async def test_unquotable_final_book_skips_without_post_or_history_block(env, monkeypatch, fault):
    from polymaker.rdp_sm_execution import RiskJournal
    from test_rdp_sm_m1 import ADDRESS, START

    v, cfg = env
    v.time = START + 110
    reads = [0]

    def mutate(request):
        if (
            request.url.path == "/book"
            and v.executor is not None
            and request.url.params["token_id"] == "1"
        ):
            reads[0] += 1
            if reads[0] >= 2:
                v.books["1"]["bids"][0]["price"] = ".01"
                v.books["1"]["asks"][0]["price"] = ".02"
        return None

    with boundary(monkeypatch, v, mutate):
        result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.skipped_quotes and not v.post_requests
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    # Full, safely closed all-skipped history must not become a permanent block.
    if fault:
        corrupt_summary(v.executor.journal.path, fault)
    if fault:
        journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
        assert journal.sequence == 0
        journal.close()
        return
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    assert journal.sequence == 0
    journal.close()


def corrupt_summary(filename, fault):
    import json
    from pathlib import Path

    path = Path(filename)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    final = rows[-1]["data"]["payload"]
    if fault == "boolean_count":
        final["confirmed_fills"] = False
    else:
        final["windows"][0]["result"]["placement_results"] = [{"outcome": "UNKNOWN"}]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
