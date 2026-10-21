"""Build the existing vehicle filter response from persisted option values."""

from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle_filter_options import get_vehicle_filter_option_values
from schemas.vehicle import CarFilterOptionsSchema


async def get_vehicle_filter_options(db: AsyncSession) -> CarFilterOptionsSchema:
    values = await get_vehicle_filter_option_values(db)
    make_models = defaultdict(list)
    for make, model in values.make_models:
        make_models[make].append(model)
    # Preserve the endpoint's truthiness policy: zero bounds produce null ranges.
    ranges = {key: {"min": low, "max": high} if low and high else None
              for key, (low, high) in values.ranges.items()}
    return CarFilterOptionsSchema(
        **values.distinct_values, makes_and_models=dict(make_models), **ranges,
    )
