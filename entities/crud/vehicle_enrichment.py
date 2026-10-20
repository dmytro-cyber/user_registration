"""Enrichment row locking and writes; callers own commit and rollback."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from domain.enrichment_decisions import EnrichmentDecision
from domain.enrichment_planning import EnrichmentCompletion
from domain.vehicle_enrichment import EnrichmentSnapshot
from models.vehicle import AutoCheckModel, CarModel, CarSaleHistoryModel, RecommendationStatus, RelevanceStatus


def lock_vehicle_for_parser_failure(db: Session, vin: str) -> CarModel | None:
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    return db.execute(select(CarModel).where(CarModel.vin == vin).with_for_update(nowait=True)).scalars().first()


def lock_vehicle_for_enrichment(db: Session, vin: str) -> CarModel | None:
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    db.execute(text("SET LOCAL statement_timeout = '15s'"))
    return (
        db.execute(
            select(CarModel)
            .where(CarModel.vin == vin)
            .options(selectinload(CarModel.condition_assessments))
            .with_for_update(nowait=True)
        )
        .scalars()
        .first()
    )


def apply_vehicle_enrichment(
    db: Session,
    car: CarModel,
    completion: EnrichmentCompletion,
    decision: EnrichmentDecision,
) -> None:
    if decision.insert_sales_history:
        for row in completion.sales_history_rows:
            db.add(CarSaleHistoryModel(**row, car_id=car.id))
    car.has_correct_vin = True
    car.has_correct_owners = decision.owners is not None
    if decision.owners is not None:
        car.owners = decision.owners
    car.has_correct_mileage = decision.has_correct_mileage
    car.has_correct_accidents = decision.accident_count is not None
    if decision.accident_count is not None:
        car.accident_count = decision.accident_count
    if decision.update_market_sources:
        car.market_price_sources = completion.preparation.market.merged_sources
    if decision.update_market_fields:
        car.avg_market_price = completion.preparation.market.candidate_price
        car.predicted_total_investments = completion.projection.total_investments
        car.predicted_profit_margin_percent = completion.projection.profit_margin_percent
        car.predicted_profit_margin = completion.projection.profit_margin
        car.auction_fee = completion.auction_fee
        car.predicted_roi = completion.projection.roi
        car.suggested_bid = decision.suggested_bid
    if completion.screenshot_url:
        db.add(AutoCheckModel(car_id=car.id, screenshot_url=completion.screenshot_url))
    car.recommendation_status = (
        RecommendationStatus.RECOMMENDED
        if decision.recommendation.recommended
        else RecommendationStatus.NOT_RECOMMENDED
    )
    car.recommendation_status_reasons = decision.recommendation.reasons
    car.recommendation_manually_set = decision.recommendation.manually_set
    car.is_checked = True
    car.relevance = RelevanceStatus.ACTIVE
    car.attempts = decision.next_attempts
    db.add(car)


def load_enrichment_snapshot(db: Session, vin: str) -> EnrichmentSnapshot | None:
    car = db.execute(select(CarModel).where(CarModel.vin == vin).limit(1)).scalars().first()
    if car is None:
        return None
    return EnrichmentSnapshot(car.attempts or 0, car.auction, car.avg_market_price, car.market_price_sources)
