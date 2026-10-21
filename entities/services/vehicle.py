from logging import getLogger
from typing import List

from fastapi import HTTPException

from models.vehicle import CarModel
from schemas.vehicle import (
    CarBaseSchema,
    CarDetailResponseSchema,
    CarListResponseSchema,
    ConditionAssessmentResponseSchema,
    SalesHistoryBaseSchema,
)

logger = getLogger(__name__)


def car_to_dict(vehicle: "CarModel") -> dict:
    """Return plain dict using only eagerly-loaded attributes."""
    photos = getattr(vehicle, "photos", []) or []
    return {
        "id": vehicle.id,
        "vin": vehicle.vin,
        "vehicle": vehicle.vehicle,
        "year": vehicle.year,
        "mileage": vehicle.mileage,
        "auction": vehicle.auction,
        "auction_name": vehicle.auction_name,
        "date": vehicle.date,
        "lot": vehicle.lot,
        "seller": vehicle.seller,
        "owners": vehicle.owners,
        "accident_count": vehicle.accident_count,
        "engine": vehicle.engine,
        "has_keys": vehicle.has_keys,
        "predicted_roi": vehicle.predicted_roi,
        "predicted_profit_margin": vehicle.predicted_profit_margin,
        "roi": vehicle.roi,
        "profit_margin": vehicle.profit_margin,
        "current_bid": vehicle.current_bid,
        "suggested_bid": vehicle.suggested_bid,
        "location": vehicle.location,
        "has_correct_mileage": vehicle.has_correct_mileage,
        "has_correct_vin": vehicle.has_correct_vin,
        "has_correct_accidents": vehicle.has_correct_accidents,
        "liked": bool(getattr(vehicle, "liked", False)),
        "recommendation_status": vehicle.recommendation_status,
        "recommendation_status_reasons": vehicle.recommendation_status_reasons,
        "photos": [getattr(p, "url", None) for p in photos],
    }


async def prepare_response(
    vehicles: List[CarModel], total_pages: int, page: int, base_url: str, bid_info: dict
) -> CarListResponseSchema:
    """Prepare the response with validated cars and pagination links."""
    validated_cars = []
    for car in vehicles:
        try:
            car_data = car_to_dict(car)
            car_data["liked"] = car.liked
            validated_car = CarBaseSchema.model_validate(car_data)
            validated_cars.append(validated_car)
        except Exception as e:
            logger.error(f"Failed to validate car VIN {car.vin}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Validation error for car VIN {car.vin}: {str(e)}")

    page_links = {i: f"{base_url}&page={i}" for i in range(1, total_pages + 1) if i != page}
    return CarListResponseSchema(
        cars=validated_cars, page_links=page_links, last=(total_pages == page), bid_info=bid_info
    )


async def prepare_car_detail_response(car: CarModel) -> CarDetailResponseSchema:
    """Prepare the detailed response for a car."""
    return CarDetailResponseSchema(
        id=car.id,
        auction=car.auction,
        vehicle=car.vehicle,
        vin=car.vin,
        mileage=car.mileage,
        has_keys=car.has_keys,
        engine_and_cylinder=car.engine_and_cylinder,
        drive_type=car.drive_type,
        transmision=car.transmision,
        vehicle_type=car.vehicle_type,
        exterior_color=car.exterior_color,
        body_style=car.body_style,
        interior_color=car.interior_color,
        style_id=car.style_id,
        seller=car.seller,
        lot=car.lot,
        actual_bid=car.actual_bid,
        owners=car.owners,
        accident_count=car.accident_count,
        date=car.date,
        recommendation_status=car.recommendation_status.value,
        link=car.link,
        location=car.location,
        auction_fee=car.auction_fee,
        suggested_bid=car.suggested_bid,
        auction_name=car.auction_name,
        liked=car.liked,
        has_correct_mileage=car.has_correct_mileage,
        has_correct_vin=car.has_correct_vin,
        has_correct_accidents=car.has_correct_accidents,
        labor=car.labor,
        transportation=car.transportation,
        maintenance=car.maintenance,
        condition=car.condition,
        photos=[photo.url for photo in car.photos_hd] if car.photos_hd else [],
        condition_assessments=[
            ConditionAssessmentResponseSchema(
                type_of_damage=condition.type_of_damage, issue_description=condition.issue_description
            )
            for condition in car.condition_assessments
        ],
        sales_history=(
            [SalesHistoryBaseSchema(**sales_history.__dict__) for sales_history in car.sales_history]
            if car.sales_history
            else []
        ),
        additional_info={
            "avg_price": car.avg_market_price or None,
            "predicted_total_investment": car.predicted_total_investments or None,
            "predicted_profit_margin": car.predicted_profit_margin or None,
            "predicted_roi": car.predicted_roi or None,
            "current_bid": car.current_bid,
        },
    )
