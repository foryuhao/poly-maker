import pytest
from m1223_dataset import validate_cohort


def cohort():
    epochs = list(range(1800000000, 1800000000 + 24 * 300, 300))
    return {
        "strategy": "M12",
        "stage": "DEVELOPMENT_CAPTURE",
        "market_epochs": epochs,
        "markets": [f"btc-updown-5m-{e}" for e in epochs],
    }


def test_fixed_contiguous_24_market_denominator():
    c = cohort()
    assert len(validate_cohort(c)) == 24


@pytest.mark.parametrize("change", ["remove", "duplicate", "replace", "wrong_role"])
def test_cannot_drop_duplicate_or_replace_unfavourable_or_unknown_markets(change):
    c = cohort()
    if change == "remove":
        c["market_epochs"].pop()
    elif change == "duplicate":
        c["market_epochs"][-1] = c["market_epochs"][-2]
    elif change == "replace":
        c["market_epochs"][-1] += 300
    else:
        c["stage"] = "INDEPENDENT_VALIDATION"
    with pytest.raises(ValueError, match="PREDECLARED"):
        validate_cohort(c)
