"""Meaningful coverage boundaries and accounting tests, with local-only SQLite/journals."""
import hashlib
import json
import os
import sqlite3

import pytest

from polymaker.m4_taker_coverage import FUNDER, rolling_coverage

EPOCH = 1800000000
MARKET = "0x"+"a"*64
TOKEN = "owned-token"


def event(kind, payload, wall=30):
    return {"ts": EPOCH+wall, "kind": kind, "data": {"payload": payload}}


def identified(sid="signal", wall=30):
    return event("sm_m4_signal", {"event": "OPPORTUNITY_IDENTIFIED", "opportunity": {
        "episode_id": sid, "root_id": sid, "token_id": TOKEN,
        "outcome": "YES", "anchor_bid_price": ".50"}}, wall)


def fixture(tmp_path, *, state="TERMINAL", filled="3.0", normal=None, attempts=1):
    root=tmp_path/"research_outputs/m4_live"
    path=root/"ledgers"/f"btc-updown-5m-{EPOCH}.sqlite3"
    path.parent.mkdir(parents=True)
    records=[identified()]
    with sqlite3.connect(path) as db:
        db.executescript('''
          CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE opportunities(id TEXT PRIMARY KEY,state TEXT,reason TEXT,reserved TEXT);
          CREATE TABLE taker_attempts(seq INT,order_id TEXT,task_id TEXT,state TEXT,started INT,filled TEXT,spent TEXT,proof TEXT);
          CREATE TABLE taker_trade_evidence(trade_id TEXT,order_id TEXT,status TEXT);
        ''')
        meta={"identity":{"mode":"LIVE","funder":FUNDER,"market_id":MARKET},
              "settings":{"execution_revision":"m4-taker-budget-v1","start_sec":15},"complete":True}
        db.executemany("INSERT INTO metadata VALUES (?,?)",[(k,json.dumps(v)) for k,v in meta.items()])
        db.execute("INSERT INTO opportunities VALUES (?,?,?,?)",("signal","SKIPPED" if normal else "CUTOFF" if state=="TERMINAL" else state,normal or "M4_T240_RESIDUAL","0" if state=="TERMINAL" else "5.5"))
        if normal:
            attempts=0
        for i in range(attempts):
            oid="order-"+str(i)
            positive=filled!="0" and i==attempts-1
            qty,cost=(filled,"1.53") if positive else ("0","0")
            proof={"kind":"EXACT_FAK_KILLED"}
            if positive:
                proof={"kind":"OWNED_TERMINAL_ORDER_AND_TRADES","trades":[{
                    "trade_id":"trade","status":"CONFIRMED","maker_order_id":oid,
                    "market_id":MARKET,"token_id":TOKEN,"our_side":"BUY",
                    "size":float(filled),"principal_usdc":"1.50","fee_usdc":".03"}]}
                db.execute("INSERT INTO taker_trade_evidence VALUES (?,?,?)",("trade",oid,"CONFIRMED"))
                records.append(event("sm_m4_owned_order_resolved",{"order_id":oid},60))
            db.execute("INSERT INTO taker_attempts VALUES (?,?,?,?,?,?,?,?)",(i+1,oid,"signal",state,1,qty,cost,json.dumps(proof)))
            records.extend([event("sm_prepared",{"order_id":oid,"body_hash":"hash"+str(i)}),
                            event("sm_network_request",{"method":"POST","operation_id":"post:hash"+str(i),"request_id":"request"+str(i)})])
    journal=root/"journal"/f"rdp_sm_{FUNDER}_m4_live_one.jsonl"
    journal.parent.mkdir()
    journal.write_text("".join(json.dumps(x)+"\n" for x in records))
    os.utime(journal,(EPOCH+400,EPOCH+400))
    return path,journal


def collect(tmp_path, state=None, **kwargs):
    return rolling_coverage(tmp_path,state or {},EPOCH+300,**kwargs)


def append(journal,*records):
    with journal.open("a") as stream:
        stream.write("".join(json.dumps(x)+"\n" for x in records))
    os.utime(journal,(EPOCH+400,EPOCH+400))


def test_confirmed_partial_is_success_with_cash_residual_and_no_writes(tmp_path):
    path,journal=fixture(tmp_path)
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path,journal)}
    r=collect(tmp_path)
    assert r["identified_signals"]==r["admitted_signals"]==r["confirmed_signals"]==1
    assert r["confirmed_per_admitted_rate"]=="1"
    assert r["confirmed_quantity"]=="3.0" and r["confirmed_all_in_usdc"]=="1.53"
    assert r["signals"][0]["remaining_budget_usdc"]=="3.47"
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


@pytest.mark.parametrize("state",["ACK","UNKNOWN","SETTLING"])
def test_unconfirmed_states_never_count_as_success(tmp_path,state):
    fixture(tmp_path,state=state,filled="0")
    r=collect(tmp_path)
    assert r["confirmed_signals"]==r["failed_zero_fill_signals"]==0
    assert r["pending_signals"]==r["raw_unresolved_orders"]==1


@pytest.mark.parametrize("reason",["M4_PRICE_OUTSIDE_BAND","M4_DIRECTION_OPPORTUNITY_LIMIT",
                                   "M4_POST_PRICE_OUTSIDE_BAND"])
def test_normal_filters_are_not_failed_admissions(tmp_path,reason):
    fixture(tmp_path,normal=reason)
    r=collect(tmp_path)
    assert r["identified_signals"]==r["normal_filtered_signals"]==1
    assert r["admitted_signals"]==r["failed_zero_fill_signals"]==0
    assert r["confirmed_per_admitted_rate"] is None


def test_retry_count_is_not_new_signals(tmp_path):
    fixture(tmp_path,attempts=4)
    r=collect(tmp_path)
    assert r["identified_signals"]==r["confirmed_signals"]==1
    assert r["real_POST"]==4 and r["additional_POST"]==3


def test_cutoff_all_fak_killed_is_failure_not_pending_or_code_bug_claim(tmp_path):
    fixture(tmp_path,attempts=3,filled="0")
    r=collect(tmp_path)
    assert r["failed_zero_fill_signals"]==1 and r["pending_signals"]==0
    assert r["failure_reasons"]=={"FAK_NO_MATCH_UNTIL_CUTOFF":1}


def test_band_abandonment_after_zero_post_keeps_actual_stop_reason(tmp_path):
    from polymaker.m4_live_price_band import BEST_ASK_BAND_ABANDONED

    path, _ = fixture(tmp_path, attempts=2, filled="0")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE opportunities SET reason=?", (BEST_ASK_BAND_ABANDONED,))
    r = collect(tmp_path)
    assert r["admitted_signals"] == r["failed_zero_fill_signals"] == 1
    assert r["normal_filtered_signals"] == r["pending_signals"] == 0
    assert r["real_POST"] == 2 and r["failure_reasons"] == {BEST_ASK_BAND_ABANDONED: 1}


def test_unsigned_errors_are_not_http_posts(tmp_path):
    _,journal=fixture(tmp_path,attempts=0,filled="0")
    append(journal,*[event("sm_m4_live_post_error",{"opportunity_id":"signal","order_id":None,
        "reason":"invalid tick size (0.001), minimum for the market is 0.01"}) for _ in range(219)])
    r=collect(tmp_path)
    assert r["unsigned_failures"]==219 and r["real_POST"]==0
    assert r["failure_reasons"]=={"SDK_TICK_SIGNING_REJECTION":1}


def test_two_proven_unsent_intents_are_not_two_real_posts(tmp_path):
    path,journal=fixture(tmp_path,attempts=2,filled="0")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE taker_attempts SET state='UNSENT',proof=?",
                   (json.dumps({"kind":"LOCAL_NATIVE_POST_BLOCKED","native_post_sent":False}),))
    records=[json.loads(line) for line in journal.read_text().splitlines()]
    journal.write_text("".join(json.dumps(r)+"\n" for r in records if r["kind"]!="sm_network_request"))
    os.utime(journal,(EPOCH+400,EPOCH+400))
    r=collect(tmp_path)
    assert r["UNSENT_preparations"]==r["durable_POST_intents"]==2
    assert r["real_POST"]==r["HTTP_POST_invocations"]==r["posted_signals"]==0


def test_started_intent_without_http_evidence_needs_review(tmp_path):
    _,journal=fixture(tmp_path,filled="0")
    records=[json.loads(line) for line in journal.read_text().splitlines()]
    journal.write_text("".join(json.dumps(r)+"\n" for r in records if r["kind"]!="sm_network_request"))
    os.utime(journal,(EPOCH+400,EPOCH+400))
    r=collect(tmp_path)
    assert r["needs_review_signals"]==1 and r["failed_zero_fill_signals"]==0
    assert r["status"]=="PARTIAL_REVIEW_REQUIRED"


def test_missing_task_is_visible_alongside_success(tmp_path):
    _,journal=fixture(tmp_path)
    append(journal,identified("lost",40))
    r=collect(tmp_path)
    assert r["identified_signals"]==2 and r["confirmed_signals"]==1
    assert r["needs_review_signals"]==1
    assert next(s for s in r["signals"] if s["id"]=="lost")["reason"]=="IDENTIFIED_WITHOUT_TASK"


def test_window_uses_signal_birth_and_deduplicates_restarted_sessions(tmp_path):
    _,journal=fixture(tmp_path)
    append(journal,identified("before",19.999),identified("at-start",20),identified("at-end",120),
           event("sm_m4_signal",{"event":"CANDIDATE","id":"support1","root":"at-start"},20),
           event("sm_m4_signal",{"event":"CANDIDATE","id":"support2","root":"at-start"},21))
    restart=journal.with_name(f"rdp_sm_{FUNDER}_m4_live_restart.jsonl")
    restart.write_text(json.dumps(identified("at-start",25))+"\n")
    os.utime(restart,(EPOCH+400,EPOCH+400))
    r=rolling_coverage(tmp_path,{},EPOCH+120,100)
    assert {s["id"] for s in r["signals"]}=={"at-start","signal"}
    assert r["candidate_events"]==2 and r["candidate_roots"]==1
    assert next(s for s in r["signals"] if s["id"]=="at-start")["signal_wall"]==EPOCH+20


def test_ledger_positive_without_final_owned_trade_proof_is_not_success(tmp_path):
    path,_=fixture(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE taker_trade_evidence SET status='MATCHED'")
    r=collect(tmp_path)
    assert r["confirmed_signals"]==0 and r["needs_review_signals"]==1


def test_confirmation_after_report_cutoff_is_not_counted_early(tmp_path):
    fixture(tmp_path)
    r=rolling_coverage(tmp_path,{},EPOCH+50,50)
    assert r["identified_signals"]==1 and r["confirmed_signals"]==0


def test_absent_markets_and_orphan_task_remain_visible(tmp_path):
    path,journal=fixture(tmp_path)
    journal.write_text("")
    r=collect(tmp_path)
    assert r["identified_signals"]==0 and r["orphan_tasks"]==[{"id":"signal","epoch":EPOCH}]
    assert r["status"]=="PARTIAL_REVIEW_REQUIRED"
    assert any(g["reason"]=="NO_SESSION" for g in r["missing_markets"])


def test_reviewed_tls_zero_does_not_erase_raw_unknown_or_count_native_as_wire_post(tmp_path):
    fixture(tmp_path,state="UNKNOWN",filled="0")
    out=tmp_path/"research_outputs/m4_taker_watch/reviews/proven-zero"
    out.mkdir(parents=True)
    proof=out/"verification.json"
    proof.write_text(json.dumps({"status":"PASS","actual_order_id":"order-0",
        "real_trace_matches_proven_pre_http_TLS_failure":True}))
    result=out/"result.json"
    result.write_text(json.dumps({"order_id":"order-0","execution_outcome":"PROVEN_NO_HTTP_ORDER_SENT",
        "actual_quantity":"0","actual_all_in_usdc":"0","offline_verification":str(proof)}))
    state={"owned_order_investigations":{"order-0":{"status":"AUDIT_COMPLETE_ZERO_EXECUTION","result":str(result)}}}
    r=collect(tmp_path,state)
    assert r["raw_unresolved_orders"]==1 and r["unresolved_orders_after_reviewed_zero_proofs"]==0
    assert r["failed_zero_fill_signals"]==1 and r["pending_signals"]==0
    assert r["HTTP_POST_invocations"]==r["proven_pre_HTTP_attempts"]==1 and r["real_POST"]==0
    assert r["signals"][0]["raw_ledger_state"]=="UNKNOWN"
