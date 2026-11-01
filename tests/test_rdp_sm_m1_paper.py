"""Paper cash, timing, queue and final-payout evidence; no private I/O."""

from copy import deepcopy
from decimal import Decimal as D

import pytest

from polymaker.config import RDPM1Config
from polymaker.dashboard.settlement import validate_resolution
from polymaker.rdp_sm_ca import plain
from polymaker.rdp_sm_m1_paper import PaperBroker, settle_paper
from polymaker.rdp_sm_m1_writer import M1ObserverWriter
from test_rdp_sm_ba import depth, trade
from test_rdp_sm_m1_observe import proposal_fixture
from test_rdp_sm_m1_strategy import advance, formation, mirror_feed


def broker_fixture():
    feed, clock, proposal = proposal_fixture()
    marker = proposal.poll(20, 21)
    records = []
    broker = PaperBroker(feed, RDPM1Config(), cutoff_mono=100,
                         emit=records.append, post_latency_ms=400, cancel_latency_ms=200)
    broker.post(marker)
    return broker, feed, clock, marker, records


def arrive(broker, feed, clock):
    advance(feed, clock, broker.active.arrival_due+.001)
    broker.advance()
    return broker.active


def sell(b, f, c, quantity, *, tx=1, source=None, received=None):
    b.trade("1", "SELL", ".50", str(quantity), f"0x{tx:064x}",
            str(f.wall()*1000 if source is None else source),
            c.now if received is None else received)


def test_post_latency_and_visible_queue_prevent_phantom_fills():
    b, f, c, _, _ = broker_fixture()
    sell(b, f, c, 1000)
    assert b.spent == 0
    order = arrive(b, f, c)
    assert order.queue_ahead == 80 and order.filled == 0
    sell(b, f, c, 85, tx=2)
    assert order.filled == 5 and b.spent == D("2.5") and b.positions["YES"] == 5
    sell(b, f, c, 1000, tx=2)
    assert order.filled == 5  # Same transaction cannot create another fill.
    sell(b, f, c, 5, tx=3)
    assert b.active is None and order.filled == 10 and b.spent == 5
    assert b.summary()["peak_committed_usdc"] == "5.0000"


def test_unexplained_depth_loss_is_not_queue_credit_or_fill():
    b, f, c, _, _ = broker_fixture()
    order = arrive(b, f, c)
    f.apply(depth(f, 10))
    assert order.queue_ahead == 80 and not order.filled
    sell(b, f, c, 20)
    assert order.queue_ahead == 60 and b.spent == 0


def test_delayed_prearrival_trade_and_opposite_flow_cannot_fill():
    b, f, c, _, _ = broker_fixture()
    order = arrive(b, f, c)
    sell(b, f, c, 1000, source=(order.arrived_wall-.01)*1000)
    sell(b, f, c, 1000, received=order.arrived-.01)
    b.trade("1", "BUY", ".50", "1000", "0x"+"ab"*32, str(f.wall()*1000), c.now)
    assert not order.filled and order.queue_ahead == 80


def test_cancel_in_flight_still_exposes_order_to_fills():
    b, f, c, _, _ = broker_fixture()
    order = arrive(b, f, c)
    b.cancel("TEST")
    advance(f, c, c.now+.05)
    sell(b, f, c, 85)
    assert order.filled == 5 and b.fills[0]["during_cancel"]
    advance(f, c, order.cancel_due+.001)
    b.advance()
    sell(b, f, c, 1000, tx=2)
    assert order.filled == 5 and b.active is None and b.committed() == D("2.5")


def test_signal_loss_during_post_cannot_retroactively_unsend_it():
    b, f, c, _, _ = broker_fixture()
    b.cancel("SIGNAL_ENDED")
    order = arrive(b, f, c)
    assert order.state == "CANCELING" and order.cancel_due > order.arrived
    sell(b, f, c, 90)
    assert b.spent == 5


def test_one_active_order_one_submission_and_gross_twenty_dollar_cap():
    b, f, c, marker, _ = broker_fixture()
    assert b.post(marker) is None
    assert b.signals[-1]["reason"] == "PAPER_ORDER_BUSY"
    arrive(b, f, c)
    sell(b, f, c, 90)
    assert b.post(marker) is None
    assert b.signals[-1]["reason"] == "PAPER_EPISODE_ALREADY_SUBMITTED"
    marker = deepcopy(marker)
    marker["opportunity"].update(rf_id="fresh-rf", ex_id="fresh-ex", episode_id="fresh")
    b.spent = D(16)
    b.positions = {"YES": D(100), "NO": D(100)}
    assert b.post(marker) is None
    assert b.signals[-1]["reason"] == "PAPER_SESSION_BUDGET"


def test_missing_trade_identity_leaves_unknown_fill_exposure_reserved():
    b, f, c, _, _ = broker_fixture()
    order = arrive(b, f, c)
    b.trade("1", "SELL", ".50", "1000", None, str(f.wall()*1000), c.now)
    assert order.uncertain
    advance(f, c, order.cancel_due+.001)
    b.advance()
    assert b.committed() == 5 and b.spent == 0
    s = settle_paper(b.summary(), {"winner": "YES"})
    assert s["pnl_usdc"] is None and s["pnl_lower_usdc"] == "0" and D(s["pnl_upper_usdc"]) == 5


@pytest.mark.parametrize("winner,pnl", [("YES", "5.00000000"), ("NO", "-5.00000000")])
def test_only_estimated_fills_settle_and_pending_result_is_not_zero(winner, pnl):
    b, f, c, _, _ = broker_fixture()
    arrive(b, f, c)
    sell(b, f, c, 90)
    assert settle_paper(b.summary(), None)["pnl_usdc"] is None
    s = settle_paper(b.summary(), {"winner": winner})
    assert D(s["pnl_usdc"]) == D(pnl) and s["status"] == "RESOLVED"


def test_merge_identity_does_not_create_or_count_double_payout():
    b, _, _, _, _ = broker_fixture()
    b.finish()
    paper = b.summary()
    paper.update(positions={"YES": "10", "NO": "7"}, spent_usdc="8", uncertain_orders=0, orders=[])
    s = settle_paper(paper, {"winner": "YES"})
    assert D(s["simulated_merge_usdc"]) == 7 and D(s["residual_payout_usdc"]) == 3
    assert D(s["pnl_usdc"]) == 2


def test_existing_official_resolution_validator_is_reused():
    session = {"slug": "btc-updown-5m-1800000000", "condition_id": "0xabc", "token_ids": {"YES": "y", "NO": "n"}}
    raw = {"slug": session["slug"], "conditionId": "0xabc", "clobTokenIds": '["n","y"]',
           "closed": True, "umaResolutionStatus": "proposed", "outcomePrices": '["1","0"]'}
    assert validate_resolution(raw, session) is None
    raw["umaResolutionStatus"] = "resolved"
    assert validate_resolution(raw, session)["winner"] == "NO"


def test_real_feed_record_callbacks_drive_paper_without_private_clients(tmp_path):
    b, f, c, _, _ = broker_fixture()
    writer = M1ObserverWriter(tmp_path/"events.jsonl.gz", listener=b.on_record)
    f.m1_writer = writer
    b.emit = writer.submit
    try:
        arrive(b, f, c)
        advance(f, c, c.now+.01)
        f.apply(trade(f, token="1", price=".50", side="SELL", size="90",
                      transaction_hash="0x"+"fe"*32))
        assert b.spent == 5 and len(b.fills) == 1
    finally:
        b.finish()
        writer.close()
    assert not writer.failure and not writer.dropped


def test_paper_close_requires_new_evidence_and_keeps_entire_fill_frame_contaminated(tmp_path):
    from polymaker.config import RDPBAConfig
    from polymaker.rdp_sm_m1_observe import PublicProposal

    f, c = mirror_feed()
    formation(f, c)
    proposal = PublicProposal(f, RDPM1Config(observation_start_sec=15), RDPBAConfig())
    marker = proposal.poll(20, 21)
    writer = M1ObserverWriter(tmp_path/"events.jsonl.gz")
    f.m1_writer = writer
    f.m1.record = lambda row: writer.submit(plain(row))
    b = PaperBroker(f, RDPM1Config(), cutoff_mono=100, emit=writer.submit)
    writer.listener = b.on_record
    try:
        assert b.post(marker) is not None and f.m1.own_active
        arrive(b, f, c)
        advance(f, c, c.now+.01)
        before = f.m1.counts["AP_STARTED"]
        f.apply(trade(f, token="1", price=".50", side="SELL", size="90",
                      transaction_hash="0x"+"dd"*32))
        assert b.active is None and f.m1.own_active
        assert f.m1.counts["AP_STARTED"] == before
        b.advance()
        assert not f.m1.own_active and not f.m1.states
        assert proposal.poll(20, 21) is None
    finally:
        b.finish()
        writer.close()
    assert not writer.failure
