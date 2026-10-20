"""Prepare import decisions and rows without executing SQL or publishing tasks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from domain.vehicle_ingestion import EnrichmentTaskPayload, VehicleBatchPlan
from domain.vehicle_recommendations import recommend_for_bulk
from models.vehicle import CarStatus, RelevanceStatus
from services.vehicle_recommendations import recommendation_facts, recommendation_fields, recommendation_state

if TYPE_CHECKING:
    from models.admin import FilterModel
    from models.vehicle import CarModel
    from schemas.vehicle import CarCreateSchema


def deduplicate_vehicles(vehicles: Iterable[CarCreateSchema]) -> list[CarCreateSchema]:
    """Keep each VIN's last value in its original first-appearance position."""
    by_vin = {}
    for vehicle in vehicles:
        if not getattr(vehicle, "vin", None):
            continue
        by_vin[vehicle.vin] = vehicle
    return list(by_vin.values())


def _matches_filter(v: Any, filters: Sequence[FilterModel]) -> bool:
    for f in filters:
        if (
            f.make == v.make
            and (f.model == v.model or f.model is None)
            and f.year_from <= v.year <= f.year_to
            and v.mileage <= f.odometer_max
        ):
            return True
    return False


def _build_car_row(
    data: dict[str, Any],
    existing: CarModel | None,
    car_columns: Sequence[str],
    not_null_cols: frozenset[str],
) -> dict[str, Any]:
    """
    Build a DB row for bulk insert/upsert.

    Rules:
    - For existing rows: do not overwrite with NULL.
    - For new rows: ensure required NOT NULL defaults exist.
    """
    row = {col: None for col in car_columns}
    row.update(data)

    if existing is not None:
        for col in car_columns:
            if col == "date":
                continue
            if row.get(col) is None:
                row[col] = getattr(existing, col)
        return row

    if row.get("car_status") is None:
        row["car_status"] = CarStatus.NEW
    if row.get("attempts") is None:
        row["attempts"] = 0
    if row.get("is_checked") is None:
        row["is_checked"] = False
    if row.get("is_manually_upserted") is None:
        row["is_manually_upserted"] = False
    if row.get("recommendation_manually_set") is None:
        row["recommendation_manually_set"] = False

    if row.get("has_correct_vin") is None:
        row["has_correct_vin"] = True
    if row.get("has_correct_owners") is None:
        row["has_correct_owners"] = True
    if row.get("has_correct_accidents") is None:
        row["has_correct_accidents"] = True
    if row.get("has_correct_mileage") is None:
        row["has_correct_mileage"] = True

    for col in not_null_cols:
        if row.get(col) is None:
            if col in ("vin", "vehicle"):
                row[col] = row.get(col) or ""
            elif col == "attempts":
                row[col] = 0
            elif col in {
                "has_correct_vin",
                "has_correct_owners",
                "has_correct_accidents",
                "has_correct_mileage",
            }:
                row[col] = True

    return row


def prepare_vehicle_batch(
    vehicles: Sequence[CarCreateSchema],
    ivent: str,
    existing_map: dict[str, CarModel],
    filters: Sequence[FilterModel],
    car_columns: Sequence[str],
    not_null_cols: frozenset[str],
) -> VehicleBatchPlan:
    new_count = 0
    updated_count = 0

    car_rows: list[dict[str, Any]] = []
    cond_insert: list[dict[str, Any]] = []  # temp rows with VIN
    photos_insert: list[dict[str, Any]] = []  # temp rows with VIN
    celery_payload: list[EnrichmentTaskPayload] = []

    for v in vehicles:
        existing = existing_map.get(v.vin)
        decision = recommend_for_bulk(
            recommendation_facts(v),
            previous=recommendation_facts(existing) if existing else None,
            state=recommendation_state(existing) if existing else None,
            manually_upserted=bool(existing and existing.is_manually_upserted),
        )

        raw = v.dict(
            exclude={
                "photos",
                "photos_hd",
                "sales_history",
                "condition_assessments",
            }
        )

        data: dict[str, Any] = {}
        to_parse = False

        # keep explicit date even if None
        for field, value in raw.items():
            if value is not None or field == "date":
                data[field] = value

        # ========================================================
        # UPDATE
        # ========================================================
        if existing:
            updated_count += 1
            # manually upserted cars: only update safe minimal fields
            if existing.is_manually_upserted:
                minimal_data = {"vin": v.vin}

                if v.current_bid is not None:
                    minimal_data["current_bid"] = v.current_bid

                minimal_data.update(recommendation_fields(decision))

                car_rows.append(_build_car_row(minimal_data, existing, car_columns, not_null_cols))
                continue

            if existing.relevance == RelevanceStatus.ACTIVE:
                if not existing.is_checked and existing.attempts < 3:
                    to_parse = True

            elif existing.relevance == RelevanceStatus.ARCHIVAL and ivent == "update":
                if _matches_filter(v, filters):
                    data["relevance"] = RelevanceStatus.ACTIVE
                    data["attempts"] = 0
                    data["is_checked"] = False
                    to_parse = True
                else:
                    data["relevance"] = RelevanceStatus.IRRELEVANT

            if v.condition_assessments:
                for a in v.condition_assessments:
                    cond_insert.append(
                        {
                            "vin": v.vin,
                            "type_of_damage": a.type_of_damage,
                            "issue_description": a.issue_description,
                        }
                    )
            existing_urls = {p.url for p in existing.photos}

            if v.photos:
                for p in v.photos:
                    if p.url not in existing_urls:
                        photos_insert.append({"vin": v.vin, "url": p.url, "is_hd": False})

            if v.photos_hd:
                for p in v.photos_hd:
                    if p.url not in existing_urls:
                        photos_insert.append({"vin": v.vin, "url": p.url, "is_hd": True})

        # ========================================================
        # CREATE
        # ========================================================
        else:
            new_count += 1

            if _matches_filter(v, filters):
                data["relevance"] = RelevanceStatus.ACTIVE
                to_parse = True
            else:
                data["relevance"] = RelevanceStatus.IRRELEVANT

            data["car_status"] = CarStatus.NEW
            data["attempts"] = 0
            data["is_checked"] = False
            data["is_manually_upserted"] = False
            data["has_correct_vin"] = True
            data["has_correct_owners"] = True
            data["has_correct_accidents"] = True
            data["has_correct_mileage"] = True

            if v.condition_assessments:
                for a in v.condition_assessments:
                    cond_insert.append(
                        {
                            "vin": v.vin,
                            "type_of_damage": a.type_of_damage,
                            "issue_description": a.issue_description,
                        }
                    )
            if v.photos:
                for p in v.photos:
                    photos_insert.append({"vin": v.vin, "url": p.url, "is_hd": False})

            if v.photos_hd:
                for p in v.photos_hd:
                    photos_insert.append({"vin": v.vin, "url": p.url, "is_hd": True})

        data.update(recommendation_fields(decision))
        data["vin"] = v.vin
        car_rows.append(_build_car_row(data, existing, car_columns, not_null_cols))

        if to_parse:
            celery_payload.append(
                {
                    "vin": v.vin,
                    "car_name": v.vehicle,
                    "car_engine": v.engine_title,
                    "mileage": v.mileage,
                    "car_make": v.make,
                    "car_model": v.model,
                    "car_year": v.year,
                    "car_transmison": v.transmision,
                }
            )

    # Retain the original final VIN deduplication before persistence.
    dedup_rows: dict[str, dict[str, Any]] = {}
    for row in car_rows:
        dedup_rows[row["vin"]] = row
    car_rows = list(dedup_rows.values())

    return VehicleBatchPlan(
        car_rows=car_rows,
        conditions=cond_insert,
        photos=photos_insert,
        enrichment_tasks=celery_payload,
        new_count=new_count,
        updated_count=updated_count,
    )
