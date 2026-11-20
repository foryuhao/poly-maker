"""M18-specific source and time boundaries, independent of capture or execution."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from sop_signals.m18_path import Observation, RoundPath


def point(kind, t, quantity, frame, **changes):
    values = dict(kind=kind, token="UP", price=D(".50"), generation=1,
                  received=t, available=t, frame=frame, row=0, change=0,
                  source=100 + t, quantity=D(str(quantity)))
    values.update(changes)
    return Observation(**values)


def path(events=None):
    result = RoundPath(token="UP", price=D(".50"), generation=1, start=0, q0=100)
    for p in events or [point("DIRECT_SELL", 0, 10, 1),
                         point("BID_PRICE_CHANGE", .1, 80, 2),
                         point("BID_PRICE_CHANGE", .35, 90, 3)]:
        result.observe(p)
    return result


def finish(value, **changes):
    kwargs = dict(available=.51, watermark=.51, coverage_proven=True)
    kwargs.update(changes)
    return value.finish(**kwargs)


def test_original_strict_round_uses_terminal_not_per_print_100ms_or_speed_bracket():
    # A 350ms terminal delay and a 250ms crossing bracket are both allowed.
    result = finish(path())
    assert result["status"] == "ROUND_PROVEN"
    assert result["q0"] == 100 and result["qmin"] == 80
    assert result["hits"] == 10 and result["loss"] == 20
    assert result["recovery"] == D(".50")
    assert result["terminal_quantity_proven"] is True
    assert "receiver_clock_quantum" not in result


def test_late_in_window_sell_requires_a_new_terminal_quantity_event():
    p = path()
    p.observe(point("DIRECT_SELL", .49, 5, 4))
    result = finish(p)
    assert result["hits"] == 15
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "TERMINAL_ANCHOR_QUANTITY_UNAVAILABLE"


@pytest.mark.parametrize("source,expected", [(100.49, "UNKNOWN"), (100.4901, "ROUND_PROVEN")])
def test_terminal_source_must_be_strictly_after_all_counted_trades(source, expected):
    p = path()
    p.observe(point("DIRECT_SELL", .49, 5, 4))
    p.observe(point("BID_PRICE_CHANGE", .499, 90, 5, source=source))
    assert finish(p)["status"] == expected


def test_quantity_at_end_cannot_repair_missing_terminal_but_keeps_post_window_guard():
    p = path()
    p.observe(point("DIRECT_SELL", .49, 5, 4))
    p.observe(point("BID_BOOK", .5, 50, 5))
    result = finish(p)
    assert result["status"] == "UNKNOWN"
    assert result["post_window_min"] == 50
    assert result["qend"] == 90


def test_sell_at_end_is_not_part_of_formation_h():
    p = path()
    p.observe(point("DIRECT_SELL", .5, 100, 4))
    result = finish(p)
    assert result["status"] == "ROUND_PROVEN" and result["hits"] == 10


@pytest.mark.parametrize("recovery_at,expected", [(.4, True), (.400001, False)])
def test_continuous_hold_100ms_boundary(recovery_at, expected):
    p = path([point("DIRECT_SELL", 0, 10, 1), point("BID_BOOK", .1, 80, 2),
              point("BID_BOOK", recovery_at, 90, 3)])
    assert finish(p)["stable"] is expected


def test_new_trough_resets_stable_timer_and_cannot_be_hidden_by_final_recovery():
    p = path()
    p.observe(point("BID_PRICE_CHANGE", .43, 70, 4))
    p.observe(point("BID_PRICE_CHANGE", .44, 90, 5))
    result = finish(p)
    assert result["qmin"] == 70
    assert result["status"] == "NOT_STABLE" and result["stable_since"] == D(".44")


@pytest.mark.parametrize("field,value", [("token", "DOWN"), ("price", D(".49")), ("kind", "BBO")])
def test_foreign_token_level_and_bbo_cannot_supply_terminal_quantity(field, value):
    p = path()
    p.observe(point("DIRECT_SELL", .49, 5, 4))
    assert p.observe(replace(point("BID_BOOK", .499, 90, 5), **{field: value})) is False
    assert finish(p)["reason"] == "TERMINAL_ANCHOR_QUANTITY_UNAVAILABLE"


def test_duplicate_raw_key_cannot_inflate_sell_volume():
    p = path()
    p.observe(point("DIRECT_SELL", .36, 100, 3))
    result = finish(p)
    assert result["hits"] == 10 and result["status"] == "UNKNOWN"
    assert "DUPLICATE_OR_UNORDERABLE" in result["reason"]


@pytest.mark.parametrize("event,reason", [
    (point("BID_BOOK", .36, 90, 4, generation=2), "GENERATION_CHANGED"),
    (point("BID_BOOK", .34, 90, 4), "RECEIVED_CLOCK_REGRESSION"),
    (point("BID_BOOK", .36, 90, 4, source=100.3), "SOURCE_CLOCK_REGRESSION"),
    (point("BID_BOOK", .36, 90, 4, available=.35), "INVALID_OBSERVATION"),
])
def test_ambiguous_or_regressing_path_stays_unknown(event, reason):
    p = path()
    p.observe(event)
    result = finish(p)
    assert result["status"] == "UNKNOWN" and reason in result["reason"]


@pytest.mark.parametrize("changes,expected", [
    ({"available": .55, "watermark": .55}, "ROUND_PROVEN"),
    ({"available": .550001, "watermark": .550001}, "UNKNOWN"),
    ({"available": .5, "watermark": .5}, "UNKNOWN"),
    ({"coverage_proven": False}, "UNKNOWN"),
    ({"coverage_proven": None}, "UNKNOWN"),
])
def test_close_watermark_coverage_and_50ms_boundary(changes, expected):
    assert finish(path(), **changes)["status"] == expected


def test_level_disappearing_then_returning_does_not_restore_round():
    p = path()
    p.observe(point("BID_BOOK", .36, 0, 4))
    p.observe(point("BID_BOOK", .38, 100, 5))
    result = finish(p)
    assert result["status"] == "UNKNOWN" and result["reason"] == "ORIGINAL_LEVEL_MISSING"


def test_no_loss_is_unqualified_not_invented_stable_support():
    p = path([point("DIRECT_SELL", 0, 10, 1), point("BID_BOOK", .1, 100, 2)])
    result = finish(p)
    assert result["data_proven"] is True and result["status"] == "NOT_QUALIFIED"


def test_observation_storage_is_bounded_and_release_drops_event_references():
    p = path()
    for i in range(4, 1004):
        p.observe(point("BID_BOOK", .35 + (i-3)/10000, 90, i))
    assert p.observations == 1003
    assert not any(isinstance(value, (list, dict, set)) for value in vars(p).values())
    finish(p)
    p.release()
    assert p.last is p.last_trade is p.last_depth is None
    with pytest.raises(ValueError, match="ALREADY_CLOSED"):
        finish(p)


def short_path(recovered_at=.14):
    p = RoundPath(token="UP", price=D(".50"), generation=1, start=0, q0=100, round_seconds=.25)
    for observation in [point("DIRECT_SELL", 0, 10, 1), point("BID_BOOK", .02, 80, 2),
                        point("BID_PRICE_CHANGE", recovered_at, 90, 3)]:
        p.observe(observation)
    return p


@pytest.mark.parametrize("recovered_at,stable", [(.15, True), (.150001, False)])
def test_short_window_still_requires_the_full_last_100ms(recovered_at, stable):
    p = short_path(recovered_at)
    result = finish(p, available=.251, watermark=.251)
    assert result["end"] == D(".25") and result["stable"] is stable


def test_short_window_end_sell_is_excluded_and_depth_at_end_cannot_repair_a_terminal():
    p = short_path()
    p.observe(point("DIRECT_SELL", .249, 5, 4))
    p.observe(point("BID_BOOK", .25, 90, 5))
    p.observe(point("DIRECT_SELL", .25, 100, 6))
    result = finish(p, available=.251, watermark=.251)
    assert result["hits"] == 15 and result["qend"] == 90
    assert result["status"] == "UNKNOWN" and not result["terminal_quantity_proven"]


@pytest.mark.parametrize("changes,status", [({"coverage_proven":False}, "UNKNOWN"),
    ({"available":.30,"watermark":.30}, "ROUND_PROVEN"),
    ({"available":.300001,"watermark":.300001}, "UNKNOWN")])
def test_short_window_does_not_relax_coverage_or_the_50ms_publication_budget(changes, status):
    assert finish(short_path(), **{"available":.251,"watermark":.251,**changes})["status"] == status


def test_round_path_does_not_allow_arbitrary_time_scales():
    with pytest.raises(ValueError, match="250_OR_ORIGINAL_500MS"):
        RoundPath(token="UP", price=D(".50"), generation=1, start=0, q0=100, round_seconds=.1)
