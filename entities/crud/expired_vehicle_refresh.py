"""Persistence for the expired-auction snapshot policy; no commit or rollback."""

from datetime import datetime

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session, selectinload

from domain.vehicle_recommendations import recommend_after_expired_refresh
from models.vehicle import (
    CarModel,
    CarSaleHistoryModel,
    CarStatus,
    ConditionAssessmentModel,
    PhotoModel,
    RelevanceStatus,
)
from schemas.vehicle import CarCreateSchema
from services.vehicle_recommendations import apply_recommendation, recommendation_facts, recommendation_state


def load_expired_active_vins(db: Session, now: datetime) -> list[str]:
    return list(
        db.execute(
            select(CarModel.vin).where(
                and_(
                    CarModel.relevance == RelevanceStatus.ACTIVE,
                    CarModel.car_status != CarStatus.WON,
                    CarModel.date.is_not(None),
                    CarModel.date <= now,
                )
            )
        )
        .scalars()
        .all()
    )


def find_expired_vehicle(db: Session, vin: str, *, with_snapshot: bool = False) -> CarModel | None:
    stmt = select(CarModel).where(CarModel.vin == vin)
    if with_snapshot:
        stmt = stmt.options(
            selectinload(CarModel.photos),
            selectinload(CarModel.sales_history),
            selectinload(CarModel.condition_assessments),
        )
    return db.execute(stmt).scalars().first()


def apply_expired_vehicle_snapshot(
    db: Session,
    existing_vehicle: CarModel,
    vehicle_info: CarCreateSchema,
) -> None:
    state = recommendation_state(existing_vehicle)
    previous = recommendation_facts(existing_vehicle, sales_count=len(existing_vehicle.sales_history))
    incoming = recommendation_facts(vehicle_info, sales_count=len(vehicle_info.sales_history))

    # 1) scalar fields
    for field, value in vehicle_info.dict(
        exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}
    ).items():
        if value is not None or field == "date":
            setattr(existing_vehicle, field, value)

    # 2) photos — add only missing
    existing_photo_urls = {p.url for p in existing_vehicle.photos}
    new_photos = []

    if vehicle_info.photos:
        for p in vehicle_info.photos:
            if p.url not in existing_photo_urls:
                new_photos.append(PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=False))

    if vehicle_info.photos_hd:
        for p in vehicle_info.photos_hd:
            if p.url not in existing_photo_urls:
                new_photos.append(PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=True))

    if new_photos:
        db.add_all(new_photos)

    # 3) condition assessments — full replace
    db.execute(delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == existing_vehicle.id))

    if vehicle_info.condition_assessments:
        for a in vehicle_info.condition_assessments:
            db.add(
                ConditionAssessmentModel(
                    type_of_damage=a.type_of_damage,
                    issue_description=a.issue_description,
                    car_id=existing_vehicle.id,
                )
            )
    # 5) sales history — add only if not present in DB
    if not existing_vehicle.sales_history and vehicle_info.sales_history:
        history_rows = []
        for h in vehicle_info.sales_history:
            item = CarSaleHistoryModel(**h.dict(), car_id=existing_vehicle.id)
            if not item.source:
                item.source = "Unknown"
            history_rows.append(item)

        if history_rows:
            db.add_all(history_rows)

    apply_recommendation(existing_vehicle, recommend_after_expired_refresh(state, previous, incoming))

    db.add(existing_vehicle)
