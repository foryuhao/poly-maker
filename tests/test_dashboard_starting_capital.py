"""Available Capital is a startup snapshot, distinct from allowance and budget."""

import json

import pytest

from polymaker.dashboard.collector import Collector, public_snapshot
from polymaker.dashboard.model import compact, summarize
from test_dashboard import NOW, SID, event, header, source, write_risk
from test_dashboard import files as files

FIELD = "starting_available_capital_usdc"


def test_exact_available_capital_wins_over_balance_allowance_and_capped_budget():
    h = {**header(), FIELD: "31.929546", "starting_official_balance_usdc": "100",
         "starting_spendable_usdc": "5", "test_capital_limit_usdc": "20"}
    s = summarize(SID, [event("sm_session", compact("sm_session", h))], [source()])
    assert s[FIELD] == "31.929546"
    public = public_snapshot({"sessions": [s], "current_session_id": "another-session"})["sessions"][0]
    assert public[FIELD] == "31.929546" and "header" not in public


@pytest.mark.parametrize("proof,expected", [
    ({"preflight_observed_order_ids": []}, "31.929546"),
    ({"preflight_observed_order_ids": ["old-order"]}, None),
    ({"capital_basis": "OFFICIAL_BALANCE_AT_SESSION_START"}, None),
    ({}, None),
])
def test_legacy_headers_need_proof_before_balance_is_used_as_available_capital(proof, expected):
    h = {**header(), "starting_official_balance_usdc": "31.929546",
         "starting_spendable_usdc": "5", **proof}
    s = summarize(SID, [event("sm_session", compact("sm_session", h))], [source()])
    assert s[FIELD] == expected


def test_restart_restores_capital_discarded_by_old_compaction(files):
    root, path, output = files
    h = {**header(), "starting_official_balance_usdc": "31.929546",
         "preflight_observed_order_ids": [], "api_key": "not-public"}
    write_risk(path, [event("sm_session", h)])
    c = Collector(root, output)
    try:
        s = c.poll(now=NOW + 10)["sessions"][0]
        s.pop(FIELD)
        s["header"].pop(FIELD)
        e = c.events(SID)[0]
        e["payload"].pop(FIELD)
        c.db.execute("UPDATE summaries SET snapshot=? WHERE sid=?", (json.dumps(s), SID))
        c.db.execute("UPDATE events SET event=? WHERE sid=?", (json.dumps(e), SID))
        c.db.commit()
    finally:
        c.close()
    c = Collector(root, output)
    try:
        restored = c.snapshots()[0]
        assert restored[FIELD] == "31.929546"
        assert c.events(SID)[0]["payload"][FIELD] == "31.929546"
        assert "not-public" not in json.dumps(restored)
        assert c.poll(now=NOW + 12)["sessions"][0][FIELD] == "31.929546"
    finally:
        c.close()
