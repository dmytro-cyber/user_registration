from datetime import datetime, timedelta
import asyncio
import pytest

from sqlalchemy import insert, desc, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from crud.vehicle import get_filtered_vehicles, save_vehicle_with_photos
from models import Base
from models.admin import FilterModel
from models.vehicle import (
    CarModel,
    PhotoModel,
    ConditionAssessmentModel,
    CarSaleHistoryModel,
    RelevanceStatus,
    RecommendationStatus,
    user_likes,
)
from schemas.vehicle import (
    CarCreateSchema,
    PhotoSchema,
    ConditionAssessmentResponseSchema,
    SalesHistoryBaseSchema,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture(scope="function")
async def db_session(async_engine):
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
async def seeded_cars(db_session):
    now = datetime.utcnow()

    def generate_vin(index: int) -> str:
        vin = f"TESTVIN{index:010d}"
        return (vin + "X" * 17)[:17]

    base_data = dict(
        vin=generate_vin(1),
        vehicle="2016 Honda CR-V",
        relevance=RelevanceStatus.ACTIVE,
        predicted_total_investments=1000,
        suggested_bid=500,
        date=now,
        auction_name="COPART",
        make="Honda",
        model="CR-V",
        body_style="SUV",
        vehicle_type="Truck",
        transmision="Automatic",
        drive_type="AWD",
        fuel_type="Gasoline",
        condition="Run & Drive",
        location="Los Angeles",
        engine_cylinder=4,
        mileage=120_000,
        profit_margin=15.0,
        roi=22.0,
        owners=2,
        accident_count=1,
        year=2016,
        recommendation_status=RecommendationStatus.RECOMMENDED,
        is_salvage=False,
        current_bid=1000,
        created_at=now - timedelta(days=1),
    )

    car_1 = CarModel(**base_data)
    car_2 = CarModel(**{**base_data, "vin": generate_vin(2), "vehicle": "2016 honda CR-V", "make": "honda", "fuel_type": "Hybrid", "created_at": now - timedelta(days=2)})
    car_3 = CarModel(
        **{
            **base_data,
            "vin": generate_vin(3),
            "vehicle": "2020 Toyota Camry",
            "make": "Toyota",
            "model": "Camry",
            "engine_cylinder": 6,
            "mileage": 80_000,
            "profit_margin": 30.0,
            "roi": 50.0,
            "owners": 1,
            "accident_count": 0,
            "year": 2020,
            "current_bid": 2000,
            "created_at": now - timedelta(hours=1),
            "date": now,
        }
    )
    car_4 = CarModel(
        **{
            **base_data,
            "vin": generate_vin(4),
            "vehicle": "2016 Ford F-150",
            "make": "Ford",
            "model": "F-150",
            "location": "Houston",
            "is_salvage": True,
            "created_at": now - timedelta(hours=2),
            "date": now,
        }
    )
    car_5 = CarModel(
        **{
            **base_data,
            "vin": generate_vin(5),
            "vehicle": "2016 Honda Pilot",
            "model": "Pilot",
            "created_at": now - timedelta(hours=3),
            "current_bid": 3000,
            "date": now - timedelta(days=15),
        }
    )

    db_session.add_all([car_1, car_2, car_3, car_4, car_5])
    await db_session.commit()
    for car in (car_1, car_2, car_3, car_4, car_5):
        await db_session.refresh(car)

    excluded_condition = ConditionAssessmentModel(car_id=car_1.id, issue_description="Water/Flood")
    minor_condition = ConditionAssessmentModel(car_id=car_5.id, issue_description="Minor Dents")
    db_session.add_all([excluded_condition, minor_condition])
    await db_session.commit()

    return dict(car_1=car_1, car_2=car_2, car_3=car_3, car_4=car_4, car_5=car_5)


@pytest.fixture
def patch_ordering(monkeypatch):
    def apply(module):
        setattr(module, "ORDERING_MAP", {"created_desc": desc(CarModel.created_at)})
    return apply


async def run_vehicle_query(db_session, filters=None, ordering="created_desc", page=1, page_size=50):
    filters = filters or {}
    return await get_filtered_vehicles(db_session, filters, ordering, page, page_size)


async def test_exclude_hybrid_and_flooded_cars(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    cars, total, pages, bids = await run_vehicle_query(db_session, {})
    ids = [car.id for car in cars]

    assert seeded_cars["car_2"].id not in ids
    assert seeded_cars["car_1"].id not in ids
    assert set(ids) >= {seeded_cars["car_3"].id, seeded_cars["car_4"].id, seeded_cars["car_5"].id}
    assert total == len(ids)
    assert pages >= 1
    assert {"min_bid", "max_bid", "avg_bid", "total_count"} <= bids.keys()


async def test_make_filter_case_insensitive(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    cars, *_ = await run_vehicle_query(db_session, {"make": ["HONDA"]})
    ids = [car.id for car in cars]

    assert seeded_cars["car_3"].id not in ids
    assert seeded_cars["car_2"].id not in ids
    assert set(ids) == {seeded_cars["car_5"].id}


async def test_filter_by_engine_cylinders(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    cars, *_ = await run_vehicle_query(db_session, {"engine_cylinder": ["6", 3, 6]})
    ids = [car.id for car in cars]
    assert set(ids) == {seeded_cars["car_3"].id}


async def test_filter_by_mileage_and_profit(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    cars, *_ = await run_vehicle_query(
        db_session,
        {"mileage_max": 90_000, "predicted_profit_margin_min": 20.0, "predicted_roi_min": 40.0},
    )
    ids = [car.id for car in cars]
    assert set(ids) == {seeded_cars["car_3"].id}


async def test_filter_by_date_range(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    target_day = seeded_cars["car_3"].date.date()
    cars_in, *_ = await run_vehicle_query(db_session, {"date_from": target_day.isoformat(), "date_to": target_day.isoformat()})
    ids_in = {car.id for car in cars_in}
    assert seeded_cars["car_3"].id in ids_in and seeded_cars["car_4"].id in ids_in

    cars_out, *_ = await run_vehicle_query(
        db_session,
        {"date_from": (target_day - timedelta(days=20)).isoformat(), "date_to": (target_day - timedelta(days=11)).isoformat()},
    )
    ids_out = {car.id for car in cars_out}
    assert seeded_cars["car_5"].id in ids_out
    assert seeded_cars["car_3"].id not in ids_out
    assert seeded_cars["car_4"].id not in ids_out


async def test_recommended_cars_only(db_session, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    cars, *_ = await run_vehicle_query(db_session, {"recommended_only": True})
    assert all(car.recommendation_status == RecommendationStatus.RECOMMENDED for car in cars)


async def test_liked_requires_user_id(db_session, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)
    with pytest.raises(ValueError):
        await run_vehicle_query(db_session, {"liked": True})


async def test_filter_liked_and_projection(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    user_id = 777
    await db_session.execute(insert(user_likes).values(user_id=user_id, car_id=seeded_cars["car_3"].id))
    await db_session.commit()

    cars, *_ = await run_vehicle_query(db_session, {"liked": True, "user_id": user_id})
    ids = [car.id for car in cars]
    assert set(ids) == {seeded_cars["car_3"].id}
    assert getattr(cars[0], "liked") is True


async def test_filter_by_salvage_title(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    cars, *_ = await run_vehicle_query(db_session, {"title": ["Salvage"]})
    ids = [car.id for car in cars]
    assert set(ids) == {seeded_cars["car_4"].id}

    cars, *_ = await run_vehicle_query(db_session, {"title": ["Clean"]})
    ids = [car.id for car in cars]
    assert seeded_cars["car_4"].id not in ids


async def test_pagination_and_aggregates(db_session, seeded_cars, patch_ordering):
    import crud.vehicle as filters_module
    patch_ordering(filters_module)

    page1_cars, total_count, total_pages, bids_page1 = await run_vehicle_query(
        db_session, {}, page=1, page_size=1
    )

    assert total_count >= 1
    assert total_pages >= 1
    assert len(page1_cars) == 1
    assert bids_page1 and bids_page1.get("total_count") == total_count

    page2_cars, _, _, bids_page2 = await run_vehicle_query(
        db_session, {}, page=2, page_size=1
    )

    if total_count >= 2:
        assert len(page2_cars) == 1
        assert page1_cars[0].id != page2_cars[0].id
    else:
        assert page2_cars == []

    assert bids_page2 == {}


def make_schema(
    vin: str,
    make="Honda",
    model="CR-V",
    year=2016,
    mileage=100_000,
    fuel_type="Gasoline",
    transmision="Automatic",
    suggested_bid=5000.0,
    current_bid=1000.0,
    photos=None,
    photos_hd=None,
    assessments=None,
    sales_history=None,
):
    vehicle = f"{year} {make} {model}".strip()
    return CarCreateSchema(
        vin=vin,
        vehicle=vehicle,
        make=make,
        model=model,
        year=year,
        mileage=mileage,
        fuel_type=fuel_type,
        transmision=transmision,
        suggested_bid=suggested_bid,
        current_bid=current_bid,
        photos=photos or [],
        photos_hd=photos_hd or [],
        condition_assessments=assessments or [],
        sales_history=sales_history or [],
    )


async def fetch_car(db, vin):
    row = await db.execute(select(CarModel).where(CarModel.vin == vin))
    return row.scalar_one()


async def all_from(db, stmt):
    return (await db.execute(stmt)).unique().scalars().all()


async def test_create_vehicle_active_by_filter_and_flags(db_session):
    await db_session.execute(
        insert(FilterModel).values(
            make="Honda", model="CR-V", year_from=2010, year_to=2020, odometer_max=150_000
        )
    )
    await db_session.commit()

    data = make_schema(
        vin="VIN-ACTIVE-001",
        fuel_type="Diesel",
        transmision="Manual",
        photos=[PhotoSchema(url="http://img/1.jpg")],
        photos_hd=[PhotoSchema(url="http://img/2.jpg")],
        assessments=[
            ConditionAssessmentResponseSchema(type_of_damage="Other", issue_description="Water/Flood"),
            ConditionAssessmentResponseSchema(type_of_damage="Other", issue_description="Minor Dents"),
        ],
        sales_history=[
            SalesHistoryBaseSchema(date="2024-01-01T00:00:00", price=10000.0, auction="Copart", lot=1, source="A"),
            SalesHistoryBaseSchema(date="2024-02-01T00:00:00", price=11000.0, auction="Copart", lot=2, source="B"),
        ],
    )

    should_parse = await save_vehicle_with_photos(data, "initial", db_session)
    assert should_parse is True

    car = await fetch_car(db_session, "VIN-ACTIVE-001")
    assert car.relevance == RelevanceStatus.ACTIVE
    assert car.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
    assert "Diesel;" in (car.recommendation_status_reasons or "")
    assert "Manual;" in (car.recommendation_status_reasons or "")
    assert "Water/Flood;" in (car.recommendation_status_reasons or "")

    photos = await all_from(db_session, select(PhotoModel).where(PhotoModel.car_id == car.id))
    assert len(photos) == 2
    assert any(p.is_hd is False for p in photos) and any(p.is_hd is True for p in photos)

    asses = await all_from(db_session, select(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == car.id))
    assert len(asses) == 2

    sales = await all_from(db_session, select(CarSaleHistoryModel).where(CarSaleHistoryModel.car_id == car.id))
    assert len(sales) == 2


async def test_create_vehicle_irrelevant_without_filter(db_session):
    data = make_schema(vin="VIN-IRREL-001")
    should_parse = await save_vehicle_with_photos(data, "initial", db_session)
    assert should_parse is False
    car = await fetch_car(db_session, "VIN-IRREL-001")
    assert car.relevance == RelevanceStatus.IRRELEVANT


async def test_create_vehicle_sales_history_many_sets_not_recommended(db_session):
    await db_session.execute(
        insert(FilterModel).values(make="Honda", model="CR-V", year_from=2000, year_to=2030, odometer_max=300_000)
    )
    await db_session.commit()

    many_sales = [
        SalesHistoryBaseSchema(date=f"2023-0{i}-01T00:00:00", price=9000 + i, auction="Copart", lot=i, source="X")
        for i in range(1, 5)
    ]
    data = make_schema(vin="VIN-SALES-4", sales_history=many_sales)

    should_parse = await save_vehicle_with_photos(data, "initial", db_session)
    assert should_parse is True

    car = await fetch_car(db_session, "VIN-SALES-4")
    assert car.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
    assert "sales at auction in the last 3 years: 4;" in (car.recommendation_status_reasons or "")

    sales = await all_from(db_session, select(CarSaleHistoryModel).where(CarSaleHistoryModel.car_id == car.id))
    assert len(sales) == 4


async def test_update_existing_active_triggers_to_parse_and_updates(db_session):
    vin = "VIN-UPD-ACTIVE-1"
    car = CarModel(
        vin=vin,
        vehicle="2016 Honda CR-V",
        make="Honda",
        model="CR-V",
        year=2016,
        mileage=120_000,
        fuel_type="Gasoline",
        transmision="Automatic",
        suggested_bid=5000.0,
        current_bid=1000.0,
        relevance=RelevanceStatus.ACTIVE,
        is_checked=False,
        attempts=0,
    )
    db_session.add(car)
    await db_session.commit()
    await db_session.refresh(car)

    data = make_schema(
        vin=vin,
        current_bid=6000.0,
        photos=[PhotoSchema(url="http://img/existing.jpg")],
        photos_hd=[PhotoSchema(url="http://img/new-hd.jpg")],
        assessments=[ConditionAssessmentResponseSchema(type_of_damage="Other", issue_description="Mechanical")],
        sales_history=[SalesHistoryBaseSchema(date="2024-03-01T00:00:00", price=12000.0, auction="Copart", lot=9, source="Z")],
    )

    should_parse = await save_vehicle_with_photos(data, "update", db_session)
    assert should_parse is True

    updated = await fetch_car(db_session, vin)
    assert updated.recommendation_status == RecommendationStatus.NOT_RECOMMENDED

    photos = await all_from(db_session, select(PhotoModel).where(PhotoModel.car_id == updated.id))
    urls = {p.url for p in photos}
    assert "http://img/new-hd.jpg" in urls

    asses = await all_from(db_session, select(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == updated.id))
    assert len(asses) == 1 and asses[0].issue_description == "Mechanical"

    sales = await all_from(db_session, select(CarSaleHistoryModel).where(CarSaleHistoryModel.car_id == updated.id))
    assert len(sales) == 1


async def test_update_existing_archival_becomes_active_when_filter_matches(db_session):
    vin = "VIN-UPD-ARCH-1"
    car = CarModel(
        vin=vin,
        vehicle="2016 Honda CR-V",
        make="Honda",
        model="CR-V",
        year=2016,
        mileage=90_000,
        fuel_type="Gasoline",
        transmision="Automatic",
        suggested_bid=4000.0,
        relevance=RelevanceStatus.ARCHIVAL,
        is_checked=True,
        attempts=5,
    )
    db_session.add(car)
    await db_session.commit()

    await db_session.execute(
        insert(FilterModel).values(
            make="Honda", model="CR-V", year_from=2010, year_to=2020, odometer_min=0, odometer_max=100_000
        )
    )
    await db_session.commit()

    data = CarCreateSchema(
        vin=vin,
        vehicle="2016 Honda CR-V",
        make="Honda",
        model="CR-V",
        year=2016,
        mileage=95_000,
    )

    to_parse = await save_vehicle_with_photos(data, "update", db_session)
    assert to_parse is True

    db_session.expire_all()
    updated = (await db_session.execute(select(CarModel).where(CarModel.vin == vin))).scalar_one()
    await db_session.refresh(updated)

    assert updated.relevance == RelevanceStatus.ACTIVE
    assert updated.attempts == 0
    assert updated.is_checked is False


async def test_integrity_error_on_duplicate_vin_returns_false(db_session, monkeypatch):
    vin = "VIN-DUP-001"
    first = CarModel(
        vin=vin,
        vehicle="2016 Honda CR-V",
        make="Honda",
        model="CR-V",
        year=2016,
        mileage=80_000,
        fuel_type="Gasoline",
        transmision="Automatic",
        suggested_bid=3000.0,
        relevance=RelevanceStatus.IRRELEVANT,
    )
    db_session.add(first)
    await db_session.commit()

    from crud import vehicle as vehicle_module

    async def fake_get_vehicle_by_vin(db, vin_value):
        return None

    monkeypatch.setattr(vehicle_module, "get_vehicle_by_vin", fake_get_vehicle_by_vin, raising=True)

    data = make_schema(vin=vin)

    ok = await save_vehicle_with_photos(data, "initial", db_session)
    assert ok is False


async def test_existing_sales_history_not_duplicated(db_session):
    vin = "VIN-HIST-EXIST"
    car = CarModel(
        vin=vin,
        vehicle="2016 Honda CR-V",
        make="Honda",
        model="CR-V",
        year=2016,
        mileage=80000,
        fuel_type="Gasoline",
        transmision="Automatic",
        suggested_bid=3000.0,
        relevance=RelevanceStatus.ACTIVE,
        is_checked=True,
        attempts=0,
    )
    db_session.add(car)
    await db_session.commit()
    await db_session.refresh(car)

    db_session.add_all([
        CarSaleHistoryModel(
            car_id=car.id,
            date=datetime.fromisoformat("2024-01-01T00:00:00"),
            source="A",
            lot_number=1,
            final_bid=9000,
            status="Sold",
        ),
        CarSaleHistoryModel(
            car_id=car.id,
            date=datetime.fromisoformat("2024-02-01T00:00:00"),
            source="B",
            lot_number=2,
            final_bid=9500,
            status="Sold",
        ),
    ])
    await db_session.commit()

    payload = CarCreateSchema(
        vin=vin,
        vehicle="2016 Honda CR-V",
        make="Honda",
        model="CR-V",
        year=2016,
        mileage=80500,
        sales_history=[
            SalesHistoryBaseSchema(
                date="2024-03-01T00:00:00",
                price=10000.0,
                auction="Copart",
                lot=3,
                source="C",
            ),
            SalesHistoryBaseSchema(
                date="2024-04-01T00:00:00",
                price=10200.0,
                auction="Copart",
                lot=4,
                source="D",
            ),
        ],
    )

    to_parse = await save_vehicle_with_photos(payload, "update", db_session)
    assert to_parse is False  # ACTIVE + is_checked=True => не тригеримо парс

    rows = await db_session.execute(
        select(CarSaleHistoryModel).where(CarSaleHistoryModel.car_id == car.id)
    )
    history = rows.scalars().all()
    assert len(history) == 2

    got_dates = {h.date.replace(microsecond=0).isoformat() for h in history}
    assert "2024-01-01T00:00:00" in got_dates
    assert "2024-02-01T00:00:00" in got_dates