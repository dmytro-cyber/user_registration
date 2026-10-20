"""Decide how a prepared enrichment result changes the current locked state."""

from dataclasses import dataclass
from typing import Literal

from domain.enrichment_planning import EnrichmentCompletion
from domain.vehicle_enrichment import append_enrichment_reason, remove_enrichment_reason
from domain.vehicle_recommendations import RecommendationState


@dataclass(frozen=True)
class EnrichmentLiveState:
    mileage: int | None
    attempts: int | None
    recommendation: RecommendationState
    has_sales_history: bool
    market_price: int | None
    market_sources: dict[str, int] | None
    expenses: tuple[float | None, ...]


@dataclass(frozen=True)
class EnrichmentDecision:
    owners: int | None
    accident_count: int | None
    has_correct_mileage: bool
    recommendation: RecommendationState
    insert_sales_history: bool
    update_market_sources: bool
    update_market_fields: bool
    suggested_bid: int | None
    market_skip_reason: Literal["no_prices", "partial", "concurrent"] | None
    next_attempts: int


def decide_vehicle_enrichment(live: EnrichmentLiveState, completion: EnrichmentCompletion) -> EnrichmentDecision:
    preparation = completion.preparation
    market = preparation.market
    state = live.recommendation
    reasons = state.reasons
    insert_history = (
        completion.business_attempts == 0 and bool(completion.sales_history_rows) and not live.has_sales_history
    )
    if insert_history and len(completion.sales_history_rows) >= 4:
        state = RecommendationState(False, reasons, False)
        reasons = append_enrichment_reason(reasons, completion.sales_history_reason or "")
    reasons = remove_enrichment_reason(reasons, "upstream parser error;")
    reasons = remove_enrichment_reason(reasons, "invalid parser json response;")
    expected_mileage = completion.expected_mileage if completion.expected_mileage is not None else live.mileage
    correct_mileage = False
    if preparation.mileage is not None and expected_mileage is not None:
        try:
            correct_mileage = int(expected_mileage) == int(preparation.mileage)
            if correct_mileage:
                reasons = remove_enrichment_reason(reasons, "mileage mismatch;")
            else:
                reasons = append_enrichment_reason(
                    reasons, f"mileage mismatch: expected {expected_mileage}, parsed {preparation.mileage};"
                )
        except Exception:
            correct_mileage = False
    recommendation = RecommendationState(state.recommended, reasons, state.manually_set)
    snapshot_is_current = live.market_price == market.previous_price and live.market_sources == market.previous_sources
    update_market = market.should_refresh and snapshot_is_current
    suggested_bid = None
    skip_reason = None
    if update_market:
        expenses = sum(value for value in (completion.auction_fee, *live.expenses) if value is not None)
        suggested_bid = int((completion.projection.total_investments or 0.0) - (expenses or 0.0))
    elif market.candidate_price is None:
        skip_reason = "no_prices"
    elif not market.is_complete and market.previous_price is not None:
        skip_reason = "partial"
    elif not snapshot_is_current:
        skip_reason = "concurrent"
    return EnrichmentDecision(
        owners=preparation.owners,
        accident_count=preparation.accident_count,
        has_correct_mileage=correct_mileage,
        recommendation=recommendation,
        insert_sales_history=insert_history,
        update_market_sources=bool(snapshot_is_current and market.merged_sources),
        update_market_fields=update_market,
        suggested_bid=suggested_bid,
        market_skip_reason=skip_reason,
        next_attempts=(live.attempts or 0) + 1,
    )
