"""Execution admission with a deterministic clock; no real orders or network."""

from types import SimpleNamespace

import pytest

from polymaker.config import RDPObiConfig
from polymaker.rdp_sm_network import NetworkHealth


def ready():
    clock = SimpleNamespace(now=0.0)
    network = NetworkHealth(RDPObiConfig(), lambda: clock.now)
    for _ in range(10):
        network.observe("GET", .1)
    assert network.snapshot().allowed
    return network, clock


def recover(network, clock, until):
    for offset in (-1, -.5, 0):
        clock.now = until + offset
        network.observe("GET", .1, endpoint="/time")
    return network.snapshot()


def test_replay_573ms_cancel_and_its_retries_do_not_block_later_entry():
    network, clock = ready()
    for end, duration in [(0.698, .225), (.697, .223), (1.522, .5731013),
                          (2.137, .363), (2.615, .227), (3.082, .216)]:
        clock.now = max(clock.now, end)
        network.observe("DELETE", duration, operation_id="same-order-pair", endpoint="/orders")
    for now in (4.862, 13.094, 25.961):
        recover(network, clock, now)
        health = network.snapshot()
        assert health.allowed and health.p95_ms == pytest.approx(573.1013)
    assert not any(kind == "sm_network_transition" for kind, *_ in network.drain_events())


def test_two_slow_distinct_operations_pause_despite_fast_gets():
    network, clock = ready()
    network.observe("POST", .6, operation_id="one")
    network.observe("POST", .1, operation_id="two")
    clock.now = .1
    network.observe("POST", .7, operation_id="three")
    for _ in range(20):
        network.observe("GET", .01)
    health = network.snapshot()
    assert not health.allowed and health.state == "PAUSED"
    assert health.pause_until == 2.1 and not health.close_active
    transitions = [v for kind, v, _ in network.drain_events() if kind == "sm_network_transition"]
    assert transitions[0]["operation_ids"] == ["three", "one"]
    assert transitions[0]["reason"] == "NETWORK_REPEATED_SLOW"


def test_same_operation_retries_and_different_methods_do_not_combine():
    network, _ = ready()
    for _ in range(10):
        network.observe("DELETE", .7, operation_id="cancel-one")
    network.observe("POST", .7, operation_id="post-one")
    assert network.snapshot().allowed


def test_old_slow_operation_expires_from_recent_evidence():
    network, clock = ready()
    network.observe("POST", .6, operation_id="old")
    clock.now = 6
    network.observe("POST", .6, operation_id="new")
    recover(network, clock, 6)
    assert network.snapshot().allowed


def test_cooldown_only_admits_one_probe_and_requires_safe_close_and_real_writes():
    network, clock = ready()
    network.observe("DELETE", .1, False)
    assert network.snapshot().state == "PAUSED"
    assert not recover(network, clock, 1.99).allowed
    health = recover(network, clock, 2.1)
    assert health.allowed and health.state == "PROBE"
    assert health.write_evidence["POST"]["status"] == "UNKNOWN"
    assert network.begin_batch("recovery-batch")
    assert not network.begin_batch("second-batch")
    assert not network.snapshot().allowed
    network.observe("POST", .1, batch_id="recovery-batch")
    network.observe("DELETE", .1, batch_id="recovery-batch")
    network.complete_batch("recovery-batch", safe=False)
    assert not network.snapshot().allowed
    network.complete_batch("recovery-batch", safe=True)
    assert network.snapshot().state == "NORMAL" and network.snapshot().allowed
    assert network.pause_duration == 0


def test_get_success_never_certifies_write_recovery():
    network, clock = ready()
    network.observe("POST", .1, False)
    assert recover(network, clock, 2.1).state == "PROBE"
    for _ in range(20):
        network.observe("GET", .01)
    assert network.snapshot().state == "PROBE"
    assert network.begin_batch("batch")
    network.observe("POST", .6, batch_id="batch")
    network.observe("DELETE", .1, batch_id="batch")
    network.complete_batch("batch", safe=True)
    assert network.snapshot().state == "PROBE"  # An isolated slow probe is inconclusive.


def test_failed_recovery_backoff_is_2_4_8_seconds_and_capped():
    network, clock = ready()
    for expected in (2, 4, 8, 8):
        network.observe("POST", .1, False)
        assert network.pause_duration == expected
        assert network.snapshot().state == "PAUSED"
        assert recover(network, clock, network.pause_until + .1).state == "PROBE"


def test_failed_requests_close_active_but_slow_admission_warning_does_not():
    network, _ = ready()
    assert network.begin_batch("live")
    network.observe("GET", .6, operation_id="read-one")
    network.observe("GET", .6, operation_id="read-two")
    assert network.snapshot().state == "PAUSED"
    assert not network.snapshot().close_active
    network.observe("GET", .1, False)
    assert network.snapshot().close_active


def test_only_recent_successful_time_probes_allow_recovery():
    network, clock = ready()
    clock.now = 3
    for _ in range(10):
        network.observe("GET", .01, endpoint="/tick-size")
    assert not network.snapshot().allowed
    recover(network, clock, 3)
    assert network.snapshot().allowed
    network.observe("GET", .01, False)
    clock.now = 6
    for _ in range(10):
        network.observe("GET", .01, endpoint="/tick-size")
    assert not network.snapshot().allowed


def test_recovery_needs_three_new_probes_after_failure():
    network, clock = ready()
    network.observe("DELETE", .1, False)
    clock.now = 2.01
    network.observe("GET", .1)
    assert not network.snapshot().allowed
    network.observe("GET", .1)
    assert not network.snapshot().allowed
    network.observe("GET", .1)
    assert network.snapshot().allowed and network.snapshot().state == "PROBE"


@pytest.mark.parametrize("outcome", ["HTTP_REJECTION", "AUTHENTICATION_ERROR", "LOCAL_ERROR"])
def test_non_transport_errors_are_logged_without_fabricating_network_outage(outcome):
    network, _ = ready()
    network.observe("POST", .01, False, outcome=outcome, endpoint="/orders")
    assert network.snapshot().allowed
    requests = [v for kind, v, _ in network.drain_events() if kind == "sm_network_request"]
    assert requests[-1]["outcome"] == outcome
    assert requests[-1]["completed_monotonic"] >= requests[-1]["started_monotonic"]


@pytest.mark.parametrize("kwargs", [
    {"network_recent_window_sec": 31},
    {"network_slow_trigger_count": 4},
    {"network_pause_sec": 9},
    {"network_probe_interval_sec": 0},
])
def test_invalid_circuit_configuration(kwargs):
    with pytest.raises(ValueError):
        RDPObiConfig(**kwargs)


@pytest.mark.parametrize('durations,scopes', [
    ((1.210428446, 1.210051783), ('cancel:B', 'cancel:A-B')),
    ((.518412679, .626544084), ('cancel:A-B-C', 'cancel:C')),
])
def test_overlapping_cancel_scopes_in_same_close_batch_count_once(durations, scopes):
    network, clock = ready()
    for index, (duration, scope) in enumerate(zip(durations, scopes, strict=True)):
        clock.now = .1 + index * .001
        network.observe('DELETE', duration, operation_id=scope, batch_id='window-2')
    assert network.snapshot().allowed and network.snapshot().state == 'NORMAL'
    assert network.snapshot().p95_ms > 500  # Raw request diagnostics are retained.
    events = network.drain_events()
    requests = [v for kind, v, _ in events if kind == 'sm_network_request' and v['method'] == 'DELETE']
    assert len(requests) == 2 and len({r['request_id'] for r in requests}) == 2
    assert {r['operation_id'] for r in requests} == {'cancel-batch:window-2'}
    assert {r['request_scope_id'] for r in requests} == set(scopes)
    assert not any(kind == 'sm_network_transition' for kind, *_ in events)


def test_different_close_batches_still_trigger_on_repeated_slow_even_with_same_scope():
    network, clock = ready()
    network.observe('DELETE', .6, operation_id='cancel:A-B', batch_id='window-1')
    clock.now = .1
    network.observe('DELETE', .7, operation_id='cancel:A-B', batch_id='window-2')
    assert network.snapshot().state == 'PAUSED'
    events = [v for kind, v, _ in network.drain_events() if kind == 'sm_network_transition']
    assert set(events[0]['operation_ids']) == {'cancel-batch:window-1', 'cancel-batch:window-2'}


def test_cancel_batch_dedup_never_suppresses_real_transport_failure():
    network, clock = ready()
    network.observe('DELETE', .7, operation_id='cancel:B', batch_id='window-2')
    clock.now = .1
    network.observe('DELETE', .1, False, operation_id='cancel:A-B', batch_id='window-2')
    assert network.snapshot().state == 'PAUSED' and network.snapshot().close_active
