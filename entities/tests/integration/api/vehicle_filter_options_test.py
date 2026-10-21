import pytest

from models.vehicle import CarModel, ConditionAssessmentModel, RelevanceStatus

pytestmark = pytest.mark.anyio


async def test_filter_options_remains_public_and_empty(client):
    response = await client.get("/api/v1/vehicles/filter-options/")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "auctions": [], "auction_names": [], "makes_and_models": {}, "models": [],
        "condition_assesstments": [], "years": None, "locations": [], "mileage_range": None,
        "accident_count_range": None, "owners_range": None, "transmissions": [], "body_styles": [],
        "vehicle_types": [], "drive_types": [], "engine_cylinders": [], "fuel_types": [], "conditions": [],
    }


async def test_filter_options_active_scope_empty_strings_and_global_damage(client, db_session):
    active = CarModel(vin="options-http-active", vehicle="car", relevance=RelevanceStatus.ACTIVE,
                      auction="", make="", model="", mileage=0, year=2020)
    inactive = CarModel(vin="options-http-inactive", vehicle="car", relevance=RelevanceStatus.IRRELEVANT,
                        auction="hidden", year=1900)
    db_session.add_all([active, inactive])
    await db_session.flush()
    db_session.add(ConditionAssessmentModel(car_id=inactive.id, issue_description="global damage"))
    await db_session.commit()
    response = await client.get("/api/v1/vehicles/filter-options/")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["auctions"] == [""] and data["makes_and_models"] == {"": [""]}
    assert data["condition_assesstments"] == ["global damage"]
    assert data["years"] == {"min": 2020, "max": 2020} and data["mileage_range"] is None
