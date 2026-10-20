from datetime import datetime, timedelta, timezone

import pytest

from domain.expired_vehicles import should_archive_expired_vehicle
from domain.vehicle_recommendations import RecommendationFacts as Facts
from domain.vehicle_recommendations import RecommendationState as State
from domain.vehicle_recommendations import recommend_after_expired_refresh


@pytest.mark.parametrize(
    "state,previous,incoming,expected",
    [
        (State(False, "old;"), Facts(), Facts(), State(False, "old;")),
        (State(True), Facts(), Facts(fuel_type="diesel"), State(False, "diesel;")),
        (State(True), Facts(), Facts(fuel_type=""), State(False)),
        (State(True, "old;", True), Facts(fuel_type="diesel"), Facts(fuel_type="diesel"), State(True, "old;", True)),
        (
            State(True, "old;", True),
            Facts(fuel_type="Gasoline"),
            Facts(fuel_type="diesel"),
            State(False, "old;diesel;"),
        ),
        (State(True), Facts(), Facts(fuel_type="diesel;", transmission="Manual"), State(False, "diesel;Manual;")),
        (State(False, "old;diesel;"), Facts(), Facts(fuel_type="diesel"), State(False, "old;diesel;")),
        (
            State(True, None, True),
            Facts(damage_issues=("Burn",)),
            Facts(damage_issues=("Burn",)),
            State(True, None, True),
        ),
        (State(True, None, True), Facts(), Facts(damage_issues=("Burn", "Burn")), State(False, "Burn;")),
        (State(True), Facts(suggested_bid=1000, actual_bid=100), Facts(current_bid=500), State(True)),
        (
            State(True),
            Facts(suggested_bid=1000),
            Facts(current_bid=500, suggested_bid=400),
            State(False, "suggested bid < current bid;"),
        ),
        (
            State(True, None, True),
            Facts(current_bid=500, suggested_bid=100),
            Facts(current_bid=500),
            State(True, None, True),
        ),
        (
            State(True, None, True),
            Facts(),
            Facts(sales_count=4),
            State(False, "sales at auction in the last 3 years: 4;"),
        ),
        (State(True, None, True), Facts(sales_count=1), Facts(sales_count=5), State(True, None, True)),
    ],
)
def test_expired_refresh_recommendation_rules(state, previous, incoming, expected):
    assert recommend_after_expired_refresh(state, previous, incoming) == expected


@pytest.mark.parametrize(
    "auction,offset,expected",
    [
        (None, None, True),
        ("Copart", 0, True),
        ("Copart", -1, True),
        ("Copart", 1, False),
        ("BuyNow", None, False),
        ("BUYNOW", -1, False),
        (" BuyNow ", None, True),
    ],
)
def test_final_relevance(auction, offset, expected):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    date = None if offset is None else now + timedelta(seconds=offset)
    assert should_archive_expired_vehicle(auction, date, now) == expected
