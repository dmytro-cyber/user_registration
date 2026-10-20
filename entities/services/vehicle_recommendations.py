"""Map schema/ORM values to the pure recommendation policies."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, TypedDict

from domain.vehicle_recommendations import RecommendationFacts, RecommendationState
from models.vehicle import RecommendationStatus

if TYPE_CHECKING:
    from models.vehicle import CarModel, ConditionAssessmentModel
    from schemas.vehicle import CarCreateSchema, CarUpsertSchema, ConditionAssessmentResponseSchema


class RecommendationFields(TypedDict):
    recommendation_status: RecommendationStatus
    recommendation_status_reasons: str | None
    recommendation_manually_set: bool


def recommendation_state(vehicle: CarModel) -> RecommendationState:
    return RecommendationState(
        vehicle.recommendation_status == RecommendationStatus.RECOMMENDED,
        vehicle.recommendation_status_reasons,
        vehicle.recommendation_manually_set,
    )


def recommendation_facts(
    vehicle: CarModel | CarCreateSchema | CarUpsertSchema,
    *,
    assessments: Iterable[ConditionAssessmentModel | ConditionAssessmentResponseSchema] | None = None,
    sales_count: int = 0,
) -> RecommendationFacts:
    return RecommendationFacts(
        fuel_type=vehicle.fuel_type,
        transmission=vehicle.transmision,
        damage_issues=tuple(
            assessment.issue_description
            for assessment in (vehicle.condition_assessments if assessments is None else assessments)
        ),
        current_bid=vehicle.current_bid,
        suggested_bid=getattr(vehicle, "suggested_bid", None),
        actual_bid=getattr(vehicle, "actual_bid", None),
        sales_count=sales_count,
    )


def recommendation_fields(state: RecommendationState) -> RecommendationFields:
    return {
        "recommendation_status": RecommendationStatus.RECOMMENDED
        if state.recommended
        else RecommendationStatus.NOT_RECOMMENDED,
        "recommendation_status_reasons": state.reasons,
        "recommendation_manually_set": state.manually_set,
    }


def apply_recommendation(vehicle: CarModel, state: RecommendationState) -> None:
    for field, value in recommendation_fields(state).items():
        setattr(vehicle, field, value)
