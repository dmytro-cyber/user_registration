from types import SimpleNamespace

import pytest

from domain.enrichment_planning import (
    RoiProjection,
    apply_enrichment_fees,
    calculate_roi_projection,
    prepare_enrichment,
)


@pytest.mark.parametrize(
    "value,owners,mileage",
    [
        (None, None, None),
        (True, 1, 1),
        (False, 0, 0),
        ("2 owners", 2, None),
        (" 1,234 ", 1234, 1234),
        ("1 234", 1234, None),
        ("-2", None, -2),
        ("2.5", 2, None),
        (2.9, 2, 2),
        ("", None, None),
        ("$12,500", 12500, None),
        ("unknown", None, None),
        ({}, None, None),
        ("1e3", 1, None),
    ],
)
def test_parser_numeric_policies_remain_distinct(value, owners, mileage):
    result = prepare_enrichment({"owners": value, "mileage": value, "accident_count": value}, None, None)
    assert (result.owners, result.accident_count, result.mileage) == (owners, owners, mileage)


@pytest.mark.parametrize("value,error", [(float("nan"), ValueError), (float("inf"), OverflowError)])
def test_non_finite_numeric_owners_still_raise(value, error):
    with pytest.raises(error):
        prepare_enrichment({"owners": value}, None, None)


def test_only_valid_positive_named_market_sources_are_used():
    market = prepare_enrichment({"jd": "$12,500", "d_max": "0", "manheim": -1, "other": 99999}, None, None).market
    assert market.incoming_sources == {"jd": 12500}
    assert market.candidate_price == 12500 and market.calculation_price == 12500
    assert market.should_refresh is True and market.is_complete is False


@pytest.mark.parametrize(
    "previous,sources,incoming,candidate,merged,refresh",
    [
        (20000, None, {"jd": 15000}, 20000, {}, False),
        (None, None, {}, None, {}, False),
        (0, None, {"jd": 15000}, 15000, {"jd": 15000}, True),
        (
            20000,
            {"jd": 20000, "d_max": 20000, "manheim": 20000},
            {"jd": 17000},
            19000,
            {"jd": 17000, "d_max": 20000, "manheim": 20000},
            True,
        ),
        (20000, {"jd": 20000}, {"d_max": 20000}, 20000, {"jd": 20000, "d_max": 20000}, False),
    ],
)
def test_market_plan_preserves_partial_baselines_and_source_merging(
    previous, sources, incoming, candidate, merged, refresh
):
    before = dict(sources) if sources is not None else None
    market = prepare_enrichment(incoming, previous, sources).market
    assert market.previous_price == previous and market.previous_sources == before
    assert market.candidate_price == candidate and market.merged_sources == merged
    assert market.should_refresh == refresh and sources == before


@pytest.mark.parametrize(
    "price,configuration,expected",
    [
        (20000, SimpleNamespace(roi=25, profit_margin=20), RoiProjection(16000, 20, 4000, 25)),
        (20000, SimpleNamespace(roi=0, profit_margin=0), RoiProjection(20000, 0, 0, 0)),
        (0, SimpleNamespace(roi=25, profit_margin=20), RoiProjection()),
        (None, SimpleNamespace(roi=25, profit_margin=20), RoiProjection()),
        (20000, None, RoiProjection()),
    ],
)
def test_roi_projection(price, configuration, expected):
    assert calculate_roi_projection(price, configuration) == expected


def test_invalid_roi_keeps_existing_failure_instead_of_clamping():
    with pytest.raises(ZeroDivisionError):
        calculate_roi_projection(20000, SimpleNamespace(roi=-100, profit_margin=20))


def test_fees_add_percent_and_fixed_charges_without_rounding():
    fees = [
        SimpleNamespace(percent=True, amount=5),
        SimpleNamespace(percent=False, amount=100),
        SimpleNamespace(percent=True, amount=2.5),
    ]
    assert apply_enrichment_fees(16001, fees) == 1300.075
    assert apply_enrichment_fees(0, fees) == 100
