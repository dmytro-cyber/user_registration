"""Transactions for recording failed attempts and completing enrichment."""

import logging

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from crud.vehicle_enrichment import (
    apply_vehicle_enrichment,
    lock_vehicle_for_enrichment,
    lock_vehicle_for_parser_failure,
)
from domain.enrichment_decisions import EnrichmentDecision, EnrichmentLiveState, decide_vehicle_enrichment
from domain.enrichment_planning import EnrichmentCompletion
from domain.vehicle_enrichment import ParserFailureResult, append_enrichment_reason
from models.vehicle import CarModel
from services.vehicle_recommendations import recommendation_state

logger = logging.getLogger(__name__)


def record_vehicle_parser_failure(db: Session, vin: str, reason: str) -> ParserFailureResult:
    try:
        car = lock_vehicle_for_parser_failure(db, vin)
        if not car:
            return {"status": "not_found", "vin": vin}
        car.recommendation_status_reasons = append_enrichment_reason(car.recommendation_status_reasons, reason)
        car.has_correct_vin = False
        car.attempts = (car.attempts or 0) + 1
        db.add(car)
        db.commit()
        return {"status": "exception", "vin": vin}
    except OperationalError as exc:
        db.rollback()
        logger.warning("VIN=%s: skip recording parser failure after OperationalError: %s", vin, exc)
        return {"status": "locked_skip", "vin": vin}
    except Exception:
        db.rollback()
        raise


def complete_vehicle_enrichment(db: Session, vin: str, completion: EnrichmentCompletion) -> dict[str, str]:
    try:
        car = lock_vehicle_for_enrichment(db, vin)
        if not car:
            raise ValueError(f"Car with VIN {vin} not found")
        decision = decide_vehicle_enrichment(enrichment_live_state(car, completion), completion)
        apply_vehicle_enrichment(db, car, completion, decision)
        _log_market_decision(vin, completion, decision)
        db.commit()
        logger.info(
            "parse_and_update_car: updated VIN=%s | owners=%s mileage=%s accidents=%s attempts=%s",
            vin,
            car.has_correct_owners,
            car.has_correct_mileage,
            car.has_correct_accidents,
            car.attempts,
        )
        return {"status": "success", "vin": vin}
    except OperationalError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("parse_and_update_car failed for VIN %s: %s", vin, exc, exc_info=True)
        raise


def enrichment_live_state(car: CarModel, completion: EnrichmentCompletion) -> EnrichmentLiveState:
    return EnrichmentLiveState(
        mileage=car.mileage,
        attempts=car.attempts,
        recommendation=recommendation_state(car),
        has_sales_history=(
            bool(car.sales_history) if completion.business_attempts == 0 and completion.sales_history_rows else False
        ),
        market_price=car.avg_market_price,
        market_sources=car.market_price_sources,
        expenses=(car.transportation, car.labor, car.maintenance, car.parts_cost),
    )


def _log_market_decision(vin: str, completion: EnrichmentCompletion, decision: EnrichmentDecision) -> None:
    if decision.market_skip_reason == "no_prices":
        logger.warning("VIN=%s returned no valid market prices; keeping established market fields", vin)
    elif decision.market_skip_reason == "partial":
        logger.warning(
            "VIN=%s returned partial market data (%s); keeping established market fields",
            vin,
            sorted(completion.preparation.market.incoming_sources),
        )
    elif decision.market_skip_reason == "concurrent":
        logger.info("VIN=%s market price changed concurrently; parser result was not applied", vin)
