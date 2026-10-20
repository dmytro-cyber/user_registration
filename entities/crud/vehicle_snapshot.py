"""Single snapshot persistence. The application service owns commit and rollback."""

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle import (
    get_vehicle_by_vin_for_upsert,
    persist_sale_history,
)
from domain.vehicle_recommendations import recommend_after_bid_sync, recommend_for_snapshot
from models.admin import FilterModel
from models.vehicle import CarModel, ConditionAssessmentModel, PhotoModel, RelevanceStatus
from schemas.vehicle import CarCreateSchema
from services.vehicle_recommendations import apply_recommendation, recommendation_facts, recommendation_state


async def persist_vehicle_snapshot(vehicle_data: CarCreateSchema, event: str, db: AsyncSession) -> bool:
    to_parse = False
    existing_vehicle = await get_vehicle_by_vin_for_upsert(db, vehicle_data.vin)

    # ============================================================
    # ======================== UPDATE ===========================
    # ============================================================
    if existing_vehicle:
        if existing_vehicle.is_manually_upserted:
            decision = recommend_after_bid_sync(
                recommendation_state(existing_vehicle),
                recommendation_facts(existing_vehicle),
                vehicle_data.current_bid,
            )
            if vehicle_data.current_bid is not None:
                existing_vehicle.current_bid = vehicle_data.current_bid
            apply_recommendation(existing_vehicle, decision)

            return False

        decision = recommend_for_snapshot(
            recommendation_facts(
                vehicle_data,
                sales_count=len(vehicle_data.sales_history) if not existing_vehicle.sales_history else 0,
            ),
            previous=recommendation_facts(existing_vehicle),
            state=recommendation_state(existing_vehicle),
        )

        if existing_vehicle.relevance == RelevanceStatus.ACTIVE:
            if not existing_vehicle.is_checked and existing_vehicle.attempts < 3:
                to_parse = True

        elif existing_vehicle.relevance == RelevanceStatus.ARCHIVAL and event == "update":
            query = select(FilterModel).where(
                FilterModel.make == vehicle_data.make,
                FilterModel.model == vehicle_data.model,
                FilterModel.year_from <= vehicle_data.year,
                FilterModel.year_to >= vehicle_data.year,
                FilterModel.odometer_max >= vehicle_data.mileage,
            )

            filter_res = (await db.execute(query)).scalars().one_or_none()

            if filter_res:
                existing_vehicle.relevance = RelevanceStatus.ACTIVE
                existing_vehicle.attempts = 0
                existing_vehicle.is_checked = False
                to_parse = True
            else:
                existing_vehicle.relevance = RelevanceStatus.IRRELEVANT

        # -------- update fields ----------
        for field, value in vehicle_data.dict(
            exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}
        ).items():
            if value is not None or field == "date":
                setattr(existing_vehicle, field, value)

        # -------- photos ----------
        existing_photo_urls = {p.url for p in existing_vehicle.photos}
        new_photos = []

        if vehicle_data.photos:
            for p in vehicle_data.photos:
                if p.url not in existing_photo_urls:
                    new_photos.append(PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=False))

        if vehicle_data.photos_hd:
            for p in vehicle_data.photos_hd:
                if p.url not in existing_photo_urls:
                    new_photos.append(PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=True))

        if new_photos:
            db.add_all(new_photos)

        # -------- condition assessments ----------
        await db.execute(
            delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == existing_vehicle.id)
        )

        if vehicle_data.condition_assessments:
            for a in vehicle_data.condition_assessments:
                db.add(
                    ConditionAssessmentModel(
                        type_of_damage=a.type_of_damage,
                        issue_description=a.issue_description,
                        car_id=existing_vehicle.id,
                    )
                )

        # -------- sales history ----------
        if not existing_vehicle.sales_history and vehicle_data.sales_history:
            await persist_sale_history(vehicle_data.sales_history, existing_vehicle.id, db)

        apply_recommendation(existing_vehicle, decision)
        return to_parse

    # ============================================================
    # ========================= CREATE ===========================
    # ============================================================

    vehicle = CarModel(**vehicle_data.dict(exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}))

    decision = recommend_for_snapshot(
        recommendation_facts(vehicle_data, sales_count=len(vehicle_data.sales_history)),
    )

    db.add(vehicle)
    await db.flush()

    # -------- relevance ----------
    query = select(FilterModel).where(
        FilterModel.make == vehicle_data.make,
        or_(FilterModel.model == vehicle_data.model, FilterModel.model.is_(None)),
        FilterModel.year_from <= vehicle_data.year,
        FilterModel.year_to >= vehicle_data.year,
        FilterModel.odometer_max >= vehicle_data.mileage,
    )

    filter_res = (await db.execute(query)).scalars().one_or_none()

    vehicle.relevance = RelevanceStatus.ACTIVE if filter_res else RelevanceStatus.IRRELEVANT
    to_parse = bool(filter_res)

    # -------- condition assessments ----------
    if vehicle_data.condition_assessments:
        for a in vehicle_data.condition_assessments:
            if a.issue_description != "Unknown":
                db.add(
                    ConditionAssessmentModel(
                        type_of_damage=a.type_of_damage,
                        issue_description=a.issue_description,
                        car_id=vehicle.id,
                    )
                )

    # -------- photos ----------
    if vehicle_data.photos:
        db.add_all([PhotoModel(url=p.url, car_id=vehicle.id, is_hd=False) for p in vehicle_data.photos])

    if vehicle_data.photos_hd:
        db.add_all([PhotoModel(url=p.url, car_id=vehicle.id, is_hd=True) for p in vehicle_data.photos_hd])

    # -------- sales history ----------
    if vehicle_data.sales_history:
        await persist_sale_history(vehicle_data.sales_history, vehicle.id, db)

    apply_recommendation(vehicle, decision)
    return to_parse
