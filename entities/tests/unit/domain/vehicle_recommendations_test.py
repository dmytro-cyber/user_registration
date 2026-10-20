from dataclasses import replace

import pytest

from domain.vehicle_recommendations import (
    RecommendationFacts,
    RecommendationState,
    normalize_manual_value,
    recommend_after_bid_sync,
    recommend_after_sales_history,
    recommend_for_bulk,
    recommend_for_manual_upsert,
    recommend_for_snapshot,
)

HEALTHY = RecommendationFacts(fuel_type="Gasoline", transmission="Automatic")
MANUAL = RecommendationState(True, "Reviewed;", True)


@pytest.mark.parametrize(
    "fuel,expected",
    [
        (None, (True, False)),
        ("", (True, False)),
        ("Gasoline", (True, True)),
        ("gasoline", (False, False)),
        ("Gas", (False, False)),
        ("Diesel", (False, False)),
    ],
)
def test_import_profiles_preserve_case_and_missing_fuel_differences(fuel, expected):
    incoming = replace(HEALTHY, fuel_type=fuel)
    assert (recommend_for_bulk(incoming).recommended, recommend_for_snapshot(incoming).recommended) == expected
    if fuel is None:
        assert recommend_for_snapshot(incoming).reasons == "None;"
    elif fuel == "":
        assert recommend_for_snapshot(incoming).reasons == ";"


@pytest.mark.parametrize("fuel", ["gasoline", "flexible fuel", "unknown", "gas"])
def test_manual_upsert_accepts_its_normalized_fuel_values(fuel):
    incoming = RecommendationFacts(fuel_type=fuel, transmission="automatic")
    # A new manual upsert retains its default NOT_RECOMMENDED unless a caller sets otherwise.
    state = RecommendationState(False)
    assert recommend_for_manual_upsert(state, None, incoming) == state


@pytest.mark.parametrize("policy", [recommend_for_bulk, recommend_for_snapshot])
def test_unchanged_disqualifying_data_keeps_manual_decision(policy):
    previous = replace(
        HEALTHY, fuel_type="Diesel", damage_issues=("Mechanical",), current_bid=6000, suggested_bid=5000
    )
    assert policy(previous, previous=previous, state=MANUAL) == MANUAL


@pytest.mark.parametrize("policy", [recommend_for_bulk, recommend_for_snapshot])
@pytest.mark.parametrize(
    "change,reason",
    [
        ({"fuel_type": "Diesel"}, "Diesel;"),
        ({"transmission": "Manual"}, "Manual;"),
        ({"damage_issues": ("Water/Flood",)}, "Water/Flood;"),
        ({"current_bid": 6000}, "BID_GT_SUGGESTED;"),
    ],
)
def test_new_disqualifying_data_clears_manual_recommendation(policy, change, reason):
    previous = replace(HEALTHY, current_bid=1000, suggested_bid=5000)
    decision = policy(replace(previous, **change), previous=previous, state=MANUAL)
    assert decision == RecommendationState(False, "Reviewed;" + reason, False)


def test_bulk_and_snapshot_keep_different_bid_limits():
    previous = replace(HEALTHY, current_bid=1000, actual_bid=1500, suggested_bid=5000)
    incoming = replace(HEALTHY, current_bid=2000)
    assert recommend_for_bulk(incoming, previous=previous, state=MANUAL) == RecommendationState(
        False,
        "Reviewed;BID_GT_SUGGESTED;",
    )
    assert recommend_for_snapshot(incoming, previous=previous, state=MANUAL) == MANUAL


def test_snapshot_bid_comparison_uses_new_suggested_bid():
    previous = replace(HEALTHY, suggested_bid=5000)
    incoming = replace(HEALTHY, current_bid=2000, suggested_bid=1500)
    assert recommend_for_snapshot(incoming, previous=previous).reasons == "BID_GT_SUGGESTED;"
    assert recommend_for_bulk(incoming, previous=previous).recommended


def test_duplicate_damage_reasons_preserve_each_profile_format():
    incoming = replace(HEALTHY, damage_issues=("Mechanical", "Mechanical"))
    assert recommend_for_bulk(incoming).reasons == "Mechanical;"
    assert recommend_for_snapshot(incoming).reasons == "Mechanical;Mechanical;"
    assert recommend_for_manual_upsert(RecommendationState(True), None, incoming).reasons == "Mechanical;"


def test_bid_only_sync_preserves_snapshot_separators_and_bulk_canonicalizes():
    previous = replace(HEALTHY, current_bid=1000, actual_bid=0, suggested_bid=5000)
    state = RecommendationState(True, ";;Reviewed;;", True)
    assert recommend_after_bid_sync(state, previous, 1).reasons == ";;Reviewed;;BID_GT_SUGGESTED;"
    assert (
        recommend_for_bulk(
            replace(HEALTHY, current_bid=1), previous=previous, state=state, manually_upserted=True
        ).reasons
        == "Reviewed;BID_GT_SUGGESTED;"
    )
    assert recommend_after_bid_sync(state, previous, None) == state
    assert recommend_after_bid_sync(state, previous, 1000) == state


def test_manual_upsert_keeps_unchanged_damage_but_overrides_new_damage():
    old = replace(HEALTHY, fuel_type="diesel", transmission="automatic", damage_issues=("Mechanical",))
    assert recommend_for_manual_upsert(MANUAL, old, old) == MANUAL
    decision = recommend_for_manual_upsert(MANUAL, old, replace(old, damage_issues=("Mechanical", "Burn")))
    assert decision == RecommendationState(False, "Reviewed;diesel;Mechanical;Burn;", False)


def test_manual_upsert_retains_nonmanual_previous_rejection_without_new_reasons():
    state = RecommendationState(False, "Previous rejection;")
    assert recommend_for_manual_upsert(state, HEALTHY, HEALTHY) == state
    assert recommend_for_bulk(HEALTHY, previous=HEALTHY, state=state) == RecommendationState(True)
    assert recommend_for_snapshot(HEALTHY, previous=HEALTHY, state=state) == RecommendationState(True)


@pytest.mark.parametrize("count", [0, 3, 4, 5])
def test_sales_history_threshold_and_manual_flag(count):
    decision = recommend_after_sales_history(MANUAL, count)
    snapshot = recommend_for_snapshot(replace(HEALTHY, sales_count=count), previous=HEALTHY, state=MANUAL)
    if count < 4:
        assert decision == snapshot == MANUAL
    else:
        reasons = f"Reviewed;sales at auction in the last 3 years: {count};"
        assert decision == RecommendationState(False, reasons, True)
        assert snapshot == RecommendationState(False, reasons, False)
    assert recommend_for_bulk(replace(HEALTHY, sales_count=count)).recommended


def test_manual_normalization_preserves_existing_unicode_and_none_semantics():
    assert normalize_manual_value("\u00a0GAS\u200b ") == "gas"
    assert normalize_manual_value(None) == "none"
