"""Read available filter values without owning the caller's transaction."""

from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.vehicle import CarModel, ConditionAssessmentModel, RelevanceStatus


@dataclass(frozen=True)
class VehicleFilterOptionValues:
    distinct_values: dict[str, list]
    make_models: list[tuple[str, str]]
    ranges: dict[str, tuple[int | None, int | None]]


async def get_vehicle_filter_option_values(db: AsyncSession) -> VehicleFilterOptionValues:
    active = CarModel.relevance == RelevanceStatus.ACTIVE
    auctions = (await db.execute(
        select(distinct(CarModel.auction)).where(CarModel.auction.isnot(None), active)
    )).scalars().all()
    # Assessments deliberately include inactive vehicles, matching the existing endpoint.
    assessments = (await db.execute(
        select(distinct(ConditionAssessmentModel.issue_description))
        .where(ConditionAssessmentModel.issue_description.isnot(None))
    )).scalars().all()
    auction_names = (await db.execute(
        select(distinct(CarModel.auction_name)).where(CarModel.auction_name.isnot(None), active)
    )).scalars().all()
    make_models = (await db.execute(
        select(CarModel.make, CarModel.model)
        .where(CarModel.make.isnot(None), CarModel.model.isnot(None), active).distinct()
    )).all()
    values = {"auctions": list(auctions), "condition_assesstments": list(assessments),
              "auction_names": list(auction_names)}
    for key, column in (
        ("transmissions", CarModel.transmision),
        ("body_styles", CarModel.body_style),
        ("vehicle_types", CarModel.vehicle_type),
        ("fuel_types", CarModel.fuel_type),
        ("drive_types", CarModel.drive_type),
        ("conditions", CarModel.condition),
        ("engine_cylinders", CarModel.engine_cylinder),
        ("locations", CarModel.location),
    ):
        values[key] = list((await db.execute(
            select(distinct(column)).where(column.isnot(None), active)
        )).scalars().all())
    ranges = {}
    for key, column in (
        ("years", CarModel.year),
        ("mileage_range", CarModel.mileage),
        ("accident_count_range", CarModel.accident_count),
        ("owners_range", CarModel.owners),
    ):
        low, high = (await db.execute(select(func.min(column), func.max(column)).where(active))).one()
        ranges[key] = (low, high)
    return VehicleFilterOptionValues(values, [(make, model) for make, model in make_models], ranges)
