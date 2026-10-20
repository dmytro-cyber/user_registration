import pytest

from domain.fee_import import FeeBand, copart_fee_bands, parse_amount, parse_price_range, unwrap_range_section


@pytest.mark.parametrize(
    "key,expected",
    [
        (None, (0, 1000000)),
        ("", (0, 1000000)),
        ("secured", (0, 1000000)),
        ("0.00-100.00", (0, 100)),
        ("$1,000-$2,000", (1000, 2000)),
        ("8000.00+", (8000, 1000000)),
        ("+15000", (15000, 1000000)),
        ("100-", (100, 1000000)),
        ("-100", (0, 100)),
        ("500", (500, 500)),
    ],
)
def test_existing_copart_range_formats(key, expected):
    assert parse_price_range(key) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (5, (5, False)),
        ("6.5", (6.5, False)),
        ({"amount": "7", "percent": True}, (7, True)),
        ({"percent": True}, (0, True)),
        ({"amount": 8, "percent": "false"}, (8, True)),
    ],
)
def test_existing_amount_conversion_and_percent_truthiness(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["FREE", "$500", "6%", None])
def test_copart_does_not_silently_adopt_iaai_amount_formats(raw):
    with pytest.raises((TypeError, ValueError)):
        parse_amount(raw)


def test_unwraps_only_a_single_known_section():
    assert unwrap_range_section({"cash": {"0-100": 5}}) == {"0-100": 5}
    assert unwrap_range_section({"unknown": {"0-100": 5}}) == {"unknown": {"0-100": 5}}
    assert unwrap_range_section([]) == {}


def test_copart_selection_order_wrappers_and_fixed_percent_policy():
    fees = {
        "bidding_fees": {
            "secured": {"cash": {"online": {"0-100": {"amount": 5, "percent": True}}}},
            "unsecured": {"0-100": 99},
        },
        "virtual_bid_fee": {"live_bid": {"online": {"100+": 20}}, "pre_bid": {"100+": 99}},
        "gate_fee": {"amount": 95, "percent": True},
        "environmental_fee": 15,
    }
    assert list(copart_fee_bands(fees)) == [
        FeeBand("bidding_fees", 5, True, 0, 100),
        FeeBand("virtual_bid_fee", 20, False, 100, 1000000),
        FeeBand("gate_fee", 95, False, 0, 1000000),
    ]


def test_invalid_bidding_value_is_reported_before_later_section_errors():
    fees = {"bidding_fees": {"secured": {"0-100": "invalid"}}, "virtual_bid_fee": None}
    with pytest.raises(ValueError):
        list(copart_fee_bands(fees))
