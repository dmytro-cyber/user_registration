"""Persistence for manual upserts. The application service owns the transaction."""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle import (
    _model_to_dict,
    get_vehicle_by_vin_for_upsert,
)
from domain.vehicle_recommendations import normalize_manual_value, recommend_for_manual_upsert
from domain.vehicle_upsert import PARSER_RELEVANT_FIELDS, needs_enrichment
from models.vehicle import CarModel, ConditionAssessmentModel, PhotoModel, RelevanceStatus
from schemas.vehicle import CarUpsertSchema
from services.car_audit import log_car_update
from services.makes_and_models import MAKES_AND_MODELS
from services.vehicle_recommendations import apply_recommendation, recommendation_facts, recommendation_state


async def write_vehicle_upsert(
    vehicle_data: CarUpsertSchema,
    db: AsyncSession,
) -> bool:
    if vehicle_data.auction:
        vehicle_data.auction = vehicle_data.auction.lower()

    existing_vehicle = await get_vehicle_by_vin_for_upsert(
        db,
        vehicle_data.vin,
    )

    # =====================================================
    # NORMALIZE MAKE / MODEL
    # =====================================================

    make_data = None
    incoming_make = vehicle_data.make
    incoming_model = vehicle_data.model

    if incoming_make:
        make_key = incoming_make.strip().lower()
        make_data = MAKES_AND_MODELS.get(make_key)

        if make_data:
            incoming_make = make_data.get(
                "original",
                incoming_make,
            )

            if incoming_model:
                model_key = incoming_model.strip().lower()
                model_original = make_data.get(
                    "models",
                    {},
                ).get(model_key)

                if model_original:
                    incoming_model = model_original

    if existing_vehicle:
        if not incoming_make:
            incoming_make = existing_vehicle.make

        if not incoming_model:
            incoming_model = existing_vehicle.model

    vehicle_data.make = incoming_make
    vehicle_data.model = incoming_model

    # =====================================================
    # NORMALIZE OTHER FIELDS
    # =====================================================

    if vehicle_data.fuel_type:
        vehicle_data.fuel_type = normalize_manual_value(vehicle_data.fuel_type)

    if vehicle_data.transmision:
        vehicle_data.transmision = normalize_manual_value(vehicle_data.transmision)

    # =====================================================
    # UPDATE EXISTING
    # =====================================================

    if existing_vehicle:
        before_snapshot = _model_to_dict(existing_vehicle)
        valid_assessments = [a for a in vehicle_data.condition_assessments if a.type_of_damage and a.issue_description]
        decision = recommend_for_manual_upsert(
            recommendation_state(existing_vehicle),
            recommendation_facts(existing_vehicle),
            recommendation_facts(vehicle_data, assessments=valid_assessments),
        )

        incoming_data = vehicle_data.dict(
            exclude={
                "photos",
                "photos_hd",
                "condition_assessments",
            }
        )

        should_parse = needs_enrichment(
            checked=bool(existing_vehicle.is_checked),
            previous={field: getattr(existing_vehicle, field, None) for field in PARSER_RELEVANT_FIELDS},
            incoming=incoming_data,
        )

        existing_vehicle.relevance = RelevanceStatus.ACTIVE

        existing_vehicle.is_manually_upserted = True

        # Скидаємо parser state ТІЛЬКИ якщо
        # реально буде повторний parsing.
        if should_parse:
            existing_vehicle.is_checked = False
            existing_vehicle.attempts = 0

        for field, value in incoming_data.items():
            if value is not None or field == "date":
                setattr(
                    existing_vehicle,
                    field,
                    value,
                )

        await db.execute(
            delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == existing_vehicle.id)
        )

        await db.flush()

        if vehicle_data.condition_assessments:
            for assessment in vehicle_data.condition_assessments:
                if assessment.type_of_damage and assessment.issue_description:
                    db.add(
                        ConditionAssessmentModel(
                            type_of_damage=(assessment.type_of_damage),
                            issue_description=(assessment.issue_description),
                            car_id=existing_vehicle.id,
                        )
                    )

        apply_recommendation(existing_vehicle, decision)

        await db.flush()

        await log_car_update(
            before_snapshot,
            existing_vehicle,
        )

        return should_parse

    # =====================================================
    # CREATE NEW
    # =====================================================

    vehicle = CarModel(
        **vehicle_data.dict(
            exclude={
                "photos",
                "photos_hd",
                "condition_assessments",
            }
        )
    )

    vehicle.is_manually_upserted = True
    vehicle.relevance = RelevanceStatus.ACTIVE

    vehicle.is_checked = False
    vehicle.attempts = 0

    db.add(vehicle)

    await db.flush()

    valid_assessments = [a for a in vehicle_data.condition_assessments if a.type_of_damage and a.issue_description]
    decision = recommend_for_manual_upsert(
        recommendation_state(vehicle),
        None,
        recommendation_facts(vehicle_data, assessments=valid_assessments),
    )

    if vehicle_data.condition_assessments:
        for assessment in vehicle_data.condition_assessments:
            if assessment.issue_description and assessment.type_of_damage:
                db.add(
                    ConditionAssessmentModel(
                        type_of_damage=(assessment.type_of_damage),
                        issue_description=(assessment.issue_description),
                        car_id=vehicle.id,
                    )
                )

    apply_recommendation(vehicle, decision)

    if vehicle_data.photos:
        db.add_all(
            [
                PhotoModel(
                    url=p.url,
                    car_id=vehicle.id,
                    is_hd=False,
                )
                for p in vehicle_data.photos
            ]
        )

    if vehicle_data.photos_hd:
        db.add_all(
            [
                PhotoModel(
                    url=p.url,
                    car_id=vehicle.id,
                    is_hd=True,
                )
                for p in vehicle_data.photos_hd
            ]
        )

    return True
