import pytest

from domain.fee_import import FeeBand
from domain.iaai_fees import iaai_fee_bands, parse_price_range, try_parse_amount


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("None", None),
        ("invalid", None),
        (95, (95, False)),
        ("$1,000.50", (1000.5, False)),
        ("FREE", (0, False)),
        ("No Fee", (0, False)),
        ("6% of sale price", (6, True)),
        ("-6%", (6, True)),
        ("-10", (-10, False)),
        ({"amount": 10}, None),
    ],
)
def test_iaai_amount_formats_and_existing_tolerance(raw, expected):
    assert try_parse_amount(raw) == expected


@pytest.mark.parametrize("key,expected", [("0-100", (0, 100)), ("15000+", (15000, 1000000)), ("500", (500, 1000000))])
def test_iaai_price_ranges_keep_open_single_values(key, expected):
    assert parse_price_range(key) == expected


@pytest.mark.parametrize("key", ["", "label", "$100-$200", "1,000+", "100-"])
def test_range_keys_do_not_gain_copart_format_support(key):
    with pytest.raises(ValueError):
        parse_price_range(key)


def test_defaults_remain_present_for_empty_or_error_json_payload():
    expected = [
        FeeBand("service_fee", 95, False, 0, 1000000),
        FeeBand("environmental_fee", 15, False, 0, 1000000),
        FeeBand("title_handling_fee", 20, False, 0, 1000000),
    ]
    assert list(iaai_fee_bands({})) == expected
    assert list(iaai_fee_bands({"detail": "source failed"})) == expected


def test_fixed_percent_and_tolerant_skipping_keep_valid_zero_rows():
    data = {
        "fees": {
            "service_fee": {"amount": "6%"},
            "environmental_fee": {"amount": None},
            "title_handling_fee": {"amount": "FREE"},
            "high_volume_buyer_fees": {"fees": {"0-100": "NO FEE", "bad": 10, "100-200": "bad"}},
            "internet_bid_buyer_fees": {"fees": {"500": "$20"}},
        }
    }
    assert list(iaai_fee_bands(data)) == [
        FeeBand("service_fee", 6, True, 0, 1000000),
        FeeBand("title_handling_fee", 0, False, 0, 1000000),
        FeeBand("high_volume_buyer_fees", 0, False, 0, 100),
        FeeBand("internet_bid_buyer_fees", 20, False, 500, 1000000),
    ]
