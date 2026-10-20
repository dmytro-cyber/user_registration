"""PostgreSQL bulk import persistence. The caller owns commit and rollback."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.vehicle_ingestion import VehicleBatchPlan
from models.admin import FilterModel
from models.vehicle import CarModel, ConditionAssessmentModel, PhotoModel

CAR_COLUMNS = tuple(c.name for c in CarModel.__table__.columns if c.name != "id")
REQUIRED_CAR_COLUMNS = frozenset(c.name for c in CarModel.__table__.columns if c.name != "id" and not c.nullable)

# Enrichment and user-owned values must survive feed refreshes.
EXCLUDED_FROM_BULK_UPDATE = {
    "car_status",
    "actual_bid",
    "roi",
    "profit_margin",
    "parts_cost",
    "maintenance",
    "transportation",
    "labor",
    "parts_needed",
    "is_manually_upserted",
    "attempts",
    "is_checked",
    "has_correct_vin",
    "has_correct_owners",
    "has_correct_accidents",
    "has_correct_mileage",
    "owners",
    "accident_count",
    "avg_market_price",
    "market_price_sources",
    "predicted_total_investments",
    "predicted_profit_margin_percent",
    "predicted_profit_margin",
    "predicted_roi",
    "auction_fee",
    "suggested_bid",
}


async def apply_tx_timeouts(session: AsyncSession) -> None:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(text("SET LOCAL lock_timeout = '2s'"))
        await session.execute(text("SET LOCAL statement_timeout = '30s'"))


async def load_vehicle_batch_context(
    db: AsyncSession,
    vins: list[str],
) -> tuple[dict[str, CarModel], list[FilterModel]]:
    await apply_tx_timeouts(db)
    existing_result = await db.execute(
        select(CarModel)
        .where(CarModel.vin.in_(vins))
        .order_by(CarModel.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .options(
            selectinload(CarModel.photos),
            selectinload(CarModel.sales_history),
            selectinload(CarModel.condition_assessments),
        )
    )
    existing_map: dict[str, CarModel] = {v.vin: v for v in existing_result.scalars()}

    filters = (await db.execute(select(FilterModel))).scalars().all()

    return existing_map, list(filters)


async def persist_vehicle_batch(
    db: AsyncSession,
    plan: VehicleBatchPlan,
    existing_map: dict[str, CarModel],
) -> None:
    if not plan.car_rows:
        return
    car_rows = plan.car_rows
    cond_insert = plan.conditions
    photos_insert = plan.photos

    # ============================================================
    # BULK UPSERT CARS — retain locks until child data is saved.
    # ============================================================
    await apply_tx_timeouts(db)

    stmt = insert(CarModel).values(car_rows)

    update_dict = {
        c.name: getattr(stmt.excluded, c.name)
        for c in CarModel.__table__.columns
        if c.name
        not in (
            "id",
            "vin",
            "created_at",
            *EXCLUDED_FROM_BULK_UPDATE,
        )
    }

    stmt = stmt.on_conflict_do_update(
        index_elements=[CarModel.vin],
        set_=update_dict,
        # A row created concurrently was not part of our locked snapshot.
        # Leave its user-owned state and child records to its creator.
        where=CarModel.id.in_([car.id for car in existing_map.values()]),
    ).returning(CarModel.id, CarModel.vin)

    result = await db.execute(stmt)
    rows = result.fetchall()
    vin_to_id: Dict[str, int] = {r.vin: r.id for r in rows}

    # ============================================================
    # REPLACE CONDITION ASSESSMENTS — same transaction as parent rows.
    # ============================================================
    car_ids = [
        car_id
        for vin, car_id in vin_to_id.items()
        if vin not in existing_map or not existing_map[vin].is_manually_upserted
    ]

    if car_ids:
        await apply_tx_timeouts(db)

        await db.execute(delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id.in_(car_ids)))

        if cond_insert:
            cond_rows: List[Dict[str, Any]] = []
            for c in cond_insert:
                car_id = vin_to_id.get(c["vin"])
                if not car_id:
                    continue
                cond_rows.append(
                    {
                        "car_id": car_id,
                        "type_of_damage": c["type_of_damage"],
                        "issue_description": c["issue_description"],
                    }
                )

            if cond_rows:
                await db.execute(insert(ConditionAssessmentModel), cond_rows)

    # ============================================================
    # INSERT PHOTOS
    # Insert only new photos that we detected earlier.
    # ============================================================
    if photos_insert:
        await apply_tx_timeouts(db)

        photo_rows: List[Dict[str, Any]] = []
        seen_photo_keys: set[tuple[int, str, bool]] = set()

        for p in photos_insert:
            car_id = vin_to_id.get(p["vin"])
            if not car_id:
                continue

            key = (car_id, p["url"], bool(p["is_hd"]))
            if key in seen_photo_keys:
                continue
            seen_photo_keys.add(key)

            photo_rows.append(
                {
                    "car_id": car_id,
                    "url": p["url"],
                    "is_hd": p["is_hd"],
                }
            )

        if photo_rows:
            await db.execute(insert(PhotoModel), photo_rows)
