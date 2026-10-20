from dataclasses import replace

import pytest

from domain.enrichment_decisions import EnrichmentLiveState, decide_vehicle_enrichment
from domain.enrichment_planning import EnrichmentCompletion, RoiProjection, prepare_enrichment
from domain.vehicle_recommendations import RecommendationState


def live(**changes):
    return replace(
        EnrichmentLiveState(100, 2, RecommendationState(True, None, True), False, None, None, (500, None, 100, 1000)),
        **changes,
    )


def completion(**changes):
    return replace(
        EnrichmentCompletion(
            prepare_enrichment({"owners": 1, "mileage": 100, "accident_count": 0}, None, None),
            RoiProjection(16000, 20, 4000, 25),
            900,
            None,
            0,
            [],
            None,
            None,
        ),
        **changes,
    )


@pytest.mark.parametrize(
    "expected,parsed,correct,reason",
    [
        (None, 100, True, ""),
        (101, 100, False, "mileage mismatch: expected 101, parsed 100;"),
        (0, 0, True, ""),
        (None, None, False, ""),
        ("bad", 100, False, ""),
    ],
)
def test_mileage_uses_expected_override_and_does_not_reject_manual_recommendation(expected, parsed, correct, reason):
    prep = replace(completion().preparation, mileage=parsed)
    decision = decide_vehicle_enrichment(live(), completion(expected_mileage=expected, preparation=prep))
    assert decision.has_correct_mileage == correct
    assert decision.recommendation == RecommendationState(True, reason, True)


def test_cleans_only_exact_parser_and_generic_mileage_markers():
    previous = RecommendationState(
        False,
        "upstream parser error;invalid parser json response;mileage mismatch;"
        "scraping error: old;mileage mismatch: expected 5, parsed 6;",
        True,
    )
    decision = decide_vehicle_enrichment(live(recommendation=previous), completion())
    assert decision.recommendation == RecommendationState(
        False, "scraping error: old;mileage mismatch: expected 5, parsed 6;", True
    )


@pytest.mark.parametrize(
    "business_attempts,has_history,count,insert,recommended,manual",
    [
        (0, False, 3, True, True, True),
        (0, False, 4, True, False, False),
        (0, True, 4, False, True, True),
        (1, False, 4, False, True, True),
        (0, False, 0, False, True, True),
    ],
)
def test_history_gate_and_manual_override(business_attempts, has_history, count, insert, recommended, manual):
    reason = f"sales at auction in the last 3 years: {count};"
    decision = decide_vehicle_enrichment(
        live(has_sales_history=has_history),
        completion(business_attempts=business_attempts, sales_history_rows=[{}] * count, sales_history_reason=reason),
    )
    assert decision.insert_sales_history == insert
    assert decision.recommendation == RecommendationState(recommended, reason if insert and count >= 4 else "", manual)
    assert decision.next_attempts == 3  # Always increment the live value, not the business-attempt snapshot.


def test_missing_history_reason_keeps_empty_text_but_still_rejects():
    decision = decide_vehicle_enrichment(live(), completion(sales_history_rows=[{}] * 4))
    assert decision.recommendation == RecommendationState(False, "", False)


def test_market_bid_uses_new_auction_fee_and_live_expenses_with_integer_truncation():
    prep = prepare_enrichment({"jd": 20000, "d_max": 20000, "manheim": 20000}, None, None)
    decision = decide_vehicle_enrichment(live(expenses=(500.25, None, 100, 1000)), completion(preparation=prep))
    assert decision.update_market_sources and decision.update_market_fields
    assert decision.suggested_bid == 13499
    assert decision.market_skip_reason is None


@pytest.mark.parametrize("price,sources", [(30000, None), (None, {"manual": 20000})])
def test_concurrent_price_or_sources_block_all_derived_updates(price, sources):
    prep = prepare_enrichment({"jd": 20000, "d_max": 20000, "manheim": 20000}, None, None)
    decision = decide_vehicle_enrichment(
        live(market_price=price, market_sources=sources), completion(preparation=prep)
    )
    assert not decision.update_market_sources and not decision.update_market_fields
    assert decision.suggested_bid is None and decision.market_skip_reason == "concurrent"


def test_source_composition_can_update_without_recalculating_unchanged_average():
    prep = prepare_enrichment({"d_max": 20000}, 20000, {"jd": 20000})
    decision = decide_vehicle_enrichment(
        live(market_price=20000, market_sources={"jd": 20000}), completion(preparation=prep)
    )
    assert decision.update_market_sources and not decision.update_market_fields
    assert decision.suggested_bid is None and decision.market_skip_reason == "partial"


def test_empty_live_attempts_start_at_one():
    assert decide_vehicle_enrichment(live(attempts=None), completion()).next_attempts == 1
