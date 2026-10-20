import pytest
from sqlalchemy import select

from models.vehicle import CarModel, CarSaleHistoryModel, PhotoModel, RecommendationStatus
from schemas.vehicle import CarCreateSchema
from services.single_vehicle_ingestion import ingest_sales_history, ingest_vehicle

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class SnapshotSource:
    def __init__(self, **data):
        self.snapshot = CarCreateSchema.model_validate(data)

    async def fetch(self, vin):
        assert vin == self.snapshot.vin
        return self.snapshot


async def test_single_vehicle_snapshot_is_committed_with_children(postgres_sessions):
    source = SnapshotSource(
        vin="SINGLE-INGEST",
        vehicle="Toyota Camry",
        year=2018,
        mileage=50000,
        fuel_type="Gasoline",
        transmision="Automatic",
        photos=[{"url": "https://photos.test/single.jpg"}],
        sales_history=[{"source": "Copart", "final_bid": 1200}],
    )
    async with postgres_sessions() as db:
        result = await ingest_vehicle("SINGLE-INGEST", db, source)
        assert result.vin == "SINGLE-INGEST"
        assert result.photos == ["https://photos.test/single.jpg"]
    async with postgres_sessions() as db:
        assert (await db.execute(select(CarModel.vin))).scalar_one() == "SINGLE-INGEST"
        assert (await db.execute(select(PhotoModel.url))).scalar_one() == "https://photos.test/single.jpg"
        assert (await db.execute(select(CarSaleHistoryModel.source))).scalar_one() == "Copart"
        assert (await db.execute(select(CarSaleHistoryModel.final_bid))).scalar_one() == 1200


async def test_sales_history_keeps_recommendation_rule_and_commit(postgres_sessions):
    async with postgres_sessions() as db:
        db.add(
            CarModel(vin="SINGLE-HISTORY", vehicle="Existing", recommendation_status=RecommendationStatus.RECOMMENDED)
        )
        await db.commit()
    source = SnapshotSource(vin="SINGLE-HISTORY", sales_history=[{"source": "Copart"} for _ in range(4)])
    async with postgres_sessions() as db:
        result = await ingest_sales_history("SINGLE-HISTORY", db, source)
        assert result.vin == "SINGLE-HISTORY"
    async with postgres_sessions() as db:
        assert len((await db.execute(select(CarSaleHistoryModel))).scalars().all()) == 4
        car = (await db.execute(select(CarModel))).scalar_one()
        assert car.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
