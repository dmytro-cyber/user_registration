import pytest

from domain.bid_refresh import normalize_auction, profit_from_current_bid, read_auction_bid
from domain.vehicle_recommendations import RecommendationFacts, RecommendationState, recommend_after_bid_refresh


@pytest.mark.parametrize(
    "value,expected",
    [(1, "copart"), (" Copart.COM ", "copart"), (2, "iaai"), ("IAA", "iaai"), (None, ""), ("Other", "other")],
)
def test_site_aliases(value, expected):
    assert normalize_auction(value) == expected


def test_lot_suffix_zero_bid_and_invalid_items():
    bid = read_auction_bid({"lot_id": " 123-1", "site": 1, "pre_bid": 0})
    assert (bid.lot, bid.auction, bid.amount) == (123, "copart", 0)
    assert read_auction_bid({"lot_id": "bad", "pre_bid": 500}) is None
    assert read_auction_bid({"lot_id": "123", "pre_bid": None}) is None


@pytest.mark.parametrize(
    "investment,bid,average,expected",
    [
        (1000, 3000, 10000, (6000, 150)),
        (None, 1000, 0, (-1000, -100)),
        (0, 0, 10000, None),
        (0, -1, 10000, None),
        (1000, 1000, None, None),
    ],
)
def test_profit_preserves_existing_formula_and_missing_market_data(investment, bid, average, expected):
    profit = profit_from_current_bid(investment, bid, average)
    assert ((profit.margin, profit.roi) if profit else None) == expected


@pytest.mark.parametrize(
    "manual,incoming,expected",
    [
        (True, 6000, RecommendationState(True, "Reviewed;", True)),
        (True, 6500, RecommendationState(False, "Reviewed;suggested bid < current bid;")),
        (True, 1000, RecommendationState(True, "Reviewed;", True)),
        (False, 6000, RecommendationState(False, "Reviewed;suggested bid < current bid;")),
    ],
)
def test_manual_decision_changes_only_with_changed_excess_bid(manual, incoming, expected):
    old = RecommendationFacts(current_bid=6000, suggested_bid=5000)
    assert recommend_after_bid_refresh(RecommendationState(True, "Reviewed;", manual), old, incoming) == expected


@pytest.mark.parametrize(
    "reasons,expected",
    [
        ("suggested bid < current bid;", RecommendationState(True, "")),
        ("Mechanical;suggested bid < current bid;", RecommendationState(False, "Mechanical;")),
    ],
)
def test_lower_bid_removes_only_its_own_rejection(reasons, expected):
    assert (
        recommend_after_bid_refresh(
            RecommendationState(False, reasons),
            RecommendationFacts(suggested_bid=5000),
            1000,
        )
        == expected
    )


def test_actual_bid_zero_takes_precedence_over_suggested_bid():
    state = recommend_after_bid_refresh(
        RecommendationState(True), RecommendationFacts(actual_bid=0, suggested_bid=5000), 1
    )
    assert state == RecommendationState(False, "suggested bid < current bid;")
