from unittest.mock import AsyncMock

import pytest

from crud.vehicle_filter_options import get_vehicle_filter_option_values
from models.vehicle import CarModel, ConditionAssessmentModel, RelevanceStatus
from services.vehicle_filter_options import get_vehicle_filter_options

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_options_preserve_distinct_active_values_and_global_assessments(postgres_sessions):
    async with postgres_sessions() as db:
        shared = dict(relevance=RelevanceStatus.ACTIVE, auction="copart", auction_name="Live", make="Toyota",
                      model="Camry", transmision="Auto", body_style="Sedan", vehicle_type="Car", fuel_type="Gas",
                      drive_type="FWD", condition="Used", engine_cylinder=4, location="Place")
        active = CarModel(vin="options-a", vehicle="a", year=2010, mileage=100, accident_count=1, owners=1, **shared)
        duplicate = CarModel(vin="options-b", vehicle="b", year=2020, mileage=200, accident_count=2, owners=3, **shared)
        inactive = CarModel(vin="options-c", vehicle="c", relevance=RelevanceStatus.ARCHIVAL,
                            auction="excluded", make="Other", model="Other", year=1900, mileage=9999)
        nulls = CarModel(vin="options-d", vehicle="d", relevance=RelevanceStatus.ACTIVE, make="Toyota")
        db.add_all([active, duplicate, inactive, nulls])
        await db.flush()
        db.add_all([
            ConditionAssessmentModel(car_id=active.id, issue_description="dent"),
            ConditionAssessmentModel(car_id=inactive.id, issue_description="inactive damage"),
            ConditionAssessmentModel(car_id=active.id, issue_description="dent"),
            ConditionAssessmentModel(car_id=active.id, issue_description=None),
        ])
        await db.commit()
    async with postgres_sessions() as db:
        result = (await get_vehicle_filter_options(db)).model_dump()
    expected = {
        "auctions": ["copart"], "auction_names": ["Live"], "makes_and_models": {"Toyota": ["Camry"]},
        "models": [], "locations": ["Place"], "transmissions": ["Auto"], "body_styles": ["Sedan"],
        "vehicle_types": ["Car"], "fuel_types": ["Gas"], "drive_types": ["FWD"], "conditions": ["Used"],
        "engine_cylinders": [4], "years": {"min": 2010, "max": 2020},
        "mileage_range": {"min": 100, "max": 200}, "accident_count_range": {"min": 1, "max": 2},
        "owners_range": {"min": 1, "max": 3},
    }
    assert set(result.pop("condition_assesstments")) == {"dent", "inactive damage"}
    assert result == expected


@pytest.mark.parametrize("attribute,key", [("year", "years"), ("mileage", "mileage_range"),
    ("accident_count", "accident_count_range"), ("owners", "owners_range")])
async def test_zero_lower_bound_remains_null(postgres_sessions, attribute, key):
    async with postgres_sessions() as db:
        db.add_all([CarModel(vin=f"zero-{value}", vehicle="car", relevance=RelevanceStatus.ACTIVE,
                            **{attribute: value}) for value in [0, 10]])
        await db.commit()
    async with postgres_sessions() as db:
        values = await get_vehicle_filter_option_values(db)
        assert values.ranges[key] == (0, 10)
        assert (await get_vehicle_filter_options(db)).model_dump()[key] is None


async def test_empty_options_do_not_mutate_or_close_session(postgres_sessions):
    async with postgres_sessions() as db:
        db.commit = AsyncMock(side_effect=AssertionError("read committed"))
        db.rollback = AsyncMock(side_effect=AssertionError("read rolled back"))
        original_close = db.close
        db.close = AsyncMock(side_effect=AssertionError("read closed caller session"))
        result = (await get_vehicle_filter_options(db)).model_dump()
        assert result["makes_and_models"] == {}
        assert result["condition_assesstments"] == []
        assert all(result[key] is None for key in ["years", "mileage_range", "owners_range", "accident_count_range"])
        assert result["auctions"] == result["models"] == []
        db.close = original_close
