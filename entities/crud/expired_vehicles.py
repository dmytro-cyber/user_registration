"""Expired irrelevant vehicle deletion; the caller owns its transaction."""

from datetime import datetime

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from models.user import user_likes
from models.vehicle import (
    AutoCheckModel,
    CarInventoryInvestmentsModel,
    CarInventoryModel,
    CarModel,
    CarSaleHistoryModel,
    CarStatus,
    ConditionAssessmentModel,
    HistoryModel,
    PartModel,
    PhotoModel,
    RelevanceStatus,
)


def delete_expired_irrelevant_vehicles(db: Session, now_utc: datetime) -> int:
    irrelevant_ids = (
        db.execute(
            select(CarModel.id).where(
                and_(
                    CarModel.relevance == RelevanceStatus.IRRELEVANT,
                    CarModel.car_status != CarStatus.WON,
                    CarModel.date.is_not(None),
                    CarModel.date <= now_utc,
                )
            )
        )
        .scalars()
        .all()
    )

    if irrelevant_ids:
        # 1) Знайти inventory, прив'язані до цих авто
        inventory_ids = (
            db.execute(select(CarInventoryModel.id).where(CarInventoryModel.car_id.in_(irrelevant_ids)))
            .scalars()
            .all()
        )

        # 2) Видалити залежності від car_inventory
        if inventory_ids:
            db.execute(
                delete(CarInventoryInvestmentsModel).where(
                    CarInventoryInvestmentsModel.car_inventory_id.in_(inventory_ids)
                )
            )

            db.execute(delete(HistoryModel).where(HistoryModel.car_inventory_id.in_(inventory_ids)))

            db.execute(delete(CarInventoryModel).where(CarInventoryModel.id.in_(inventory_ids)))

        # 3) Видалити залежності напряму від cars
        db.execute(delete(user_likes).where(user_likes.c.car_id.in_(irrelevant_ids)))

        db.execute(delete(AutoCheckModel).where(AutoCheckModel.car_id.in_(irrelevant_ids)))

        db.execute(delete(PhotoModel).where(PhotoModel.car_id.in_(irrelevant_ids)))

        db.execute(delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id.in_(irrelevant_ids)))

        db.execute(delete(CarSaleHistoryModel).where(CarSaleHistoryModel.car_id.in_(irrelevant_ids)))

        db.execute(delete(PartModel).where(PartModel.car_id.in_(irrelevant_ids)))

        db.execute(delete(HistoryModel).where(HistoryModel.car_id.in_(irrelevant_ids)))

        # 4) І тільки після цього видалити самі авто
        db.execute(delete(CarModel).where(CarModel.id.in_(irrelevant_ids)))
    return len(irrelevant_ids)
