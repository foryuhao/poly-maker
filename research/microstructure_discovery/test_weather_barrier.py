import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from weather_barrier import rounded, temperature, upper_bin


def test_remark_temperature_precedes_rounded_body():
    assert temperature("METAR EGLC 311050Z 14/13 Q1032 RMK T01390133") == Decimal("13.9")
    assert temperature("METAR TEST 311050Z M02/M02 RMK T10171022") == Decimal("-1.7")


def test_speci_never_counts_but_unlabelled_routine_does():
    assert temperature("SPECI EGLC 311050Z 19/12 Q1032") is None
    assert temperature("EGLC 311050Z 19/12 Q1032") == Decimal(19)
    assert temperature("METAR COR EGLC 311050Z 10/09 Q1032") == Decimal(10)


def test_half_up_rules_including_negative_fahrenheit():
    assert rounded(Decimal("16.5"), "C") == 17
    assert rounded(Decimal("-8.5"), "C") == -8
    assert rounded(Decimal("-22.5"), "F") == -8
    assert rounded(Decimal("18.3"), "F") == 65


def test_bracket_maximum_does_not_treat_range_separator_as_minus():
    assert upper_bin("68-69°F") == 69
    assert upper_bin("-1-0°C") == 0
    assert upper_bin("-1°C or below") == -1
    assert upper_bin("20°C or higher") is None
