from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.sql.dml import Delete

from crud.expired_vehicles import delete_expired_irrelevant_vehicles
from db.config import DatabaseConfig
from db.factories import create_sync_database
from models.user import UserModel, UserRoleEnum, UserRoleModel, user_likes
from models.vehicle import (
    AutoCheckModel,
    CarInventoryInvestmentsModel,
    CarInventoryInvestmentsType,
    CarInventoryModel,
    CarModel,
    CarSaleHistoryModel,
    CarStatus,
    ConditionAssessmentModel,
    HistoryModel,
    PartModel,
    PhotoModel,
    RelevanceStatus,
)
from services.expired_vehicles import cleanup_expired_irrelevant_vehicles

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEPENDENCIES = [
    CarInventoryInvestmentsModel,
    HistoryModel,
    CarInventoryModel,
    AutoCheckModel,
    PhotoModel,
    ConditionAssessmentModel,
    CarSaleHistoryModel,
    PartModel,
]


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


def seed(database):
    with database.sessions() as db:
        cars = []
        for vin, changes in [
            ("expired", {}),
            ("boundary", {"date": NOW}),
            ("won", {"car_status": CarStatus.WON}),
            ("active", {"relevance": RelevanceStatus.ACTIVE}),
            ("archival", {"relevance": RelevanceStatus.ARCHIVAL}),
            ("future", {"date": NOW + timedelta(seconds=1)}),
            ("undated", {"date": None}),
        ]:
            values = dict(
                vin=vin, vehicle="test", date=NOW - timedelta(seconds=1), relevance=RelevanceStatus.IRRELEVANT
            )
            values.update(changes)
            cars.append(CarModel(**values))
        db.add_all(cars)
        role = UserRoleModel(name=UserRoleEnum.USER)
        db.add(role)
        db.flush()
        user = UserModel(email="cleanup@example.com", _hashed_password="unused", role_id=role.id)
        db.add(user)
        db.flush()
        for car in [cars[0], cars[2]]:
            inventory = CarInventoryModel(car_id=car.id, vehicle="test", vin=car.vin, stock=car.vin)
            db.add(inventory)
            db.flush()
            db.add_all(
                [
                    CarInventoryInvestmentsModel(
                        car_inventory_id=inventory.id,
                        vendor="v",
                        description="d",
                        cost=1,
                        payment_method="cash",
                        investment_type=CarInventoryInvestmentsType.PARTS,
                    ),
                    HistoryModel(car_inventory_id=inventory.id, action="Added", user_id=user.id),
                    HistoryModel(car_id=car.id, action="Added", user_id=user.id),
                    AutoCheckModel(car_id=car.id),
                    PhotoModel(car_id=car.id, url=car.vin),
                    ConditionAssessmentModel(car_id=car.id, issue_description="test"),
                    CarSaleHistoryModel(car_id=car.id),
                    PartModel(car_id=car.id, name="test"),
                ]
            )
            db.execute(insert(user_likes).values(user_id=user.id, car_id=car.id))
        db.commit()


def counts(db):
    return {
        model.__tablename__: db.scalar(select(func.count()).select_from(model)) for model in DEPENDENCIES + [CarModel]
    }


async def test_cleanup_selection_dependents_and_commit_visible_to_other_session(database):
    seed(database)
    with database.sessions() as db:
        assert cleanup_expired_irrelevant_vehicles(db, NOW) == {"deleted_irrelevant": 2, "errors": 0}
    with database.sessions() as observer:
        assert set(observer.scalars(select(CarModel.vin))) == {"won", "active", "archival", "future", "undated"}
        remaining = counts(observer)
        assert all(remaining[m.__tablename__] == (2 if m is HistoryModel else 1) for m in DEPENDENCIES)
        assert observer.scalar(select(func.count()).select_from(user_likes)) == 1
        assert observer.scalar(select(func.count()).select_from(UserModel)) == 1


async def test_repository_does_not_commit_deletions(database):
    seed(database)
    with database.sessions() as db:
        before = counts(db)
        assert delete_expired_irrelevant_vehicles(db, NOW) == 2
        assert db.scalar(select(func.count()).select_from(CarModel)) == 5
        db.rollback()
    with database.sessions() as db:
        assert counts(db) == before


@pytest.mark.parametrize("failure", ["delete", "commit"])
async def test_failure_rolls_back_all_dependencies_and_preserves_legacy_counter(database, monkeypatch, failure):
    seed(database)
    with database.sessions() as db:
        before = counts(db)
        if failure == "commit":
            monkeypatch.setattr(db, "commit", Mock(side_effect=RuntimeError("commit failed")))
        else:
            execute = db.execute

            def fail_late(statement, *args, **kwargs):
                if isinstance(statement, Delete) and statement.table.name == "parts":
                    raise RuntimeError("late delete failed")
                return execute(statement, *args, **kwargs)

            monkeypatch.setattr(db, "execute", fail_late)
        assert cleanup_expired_irrelevant_vehicles(db, NOW) == {
            "deleted_irrelevant": 2 if failure == "commit" else 0,
            "errors": 1,
        }
    with database.sessions() as observer:
        assert counts(observer) == before
        assert observer.scalar(select(func.count()).select_from(user_likes)) == 2


async def test_empty_selection_does_not_commit(database, monkeypatch):
    with database.sessions() as db:
        commit = Mock()
        monkeypatch.setattr(db, "commit", commit)
        assert cleanup_expired_irrelevant_vehicles(db, NOW) == {"deleted_irrelevant": 0, "errors": 0}
        commit.assert_not_called()


async def test_celery_task_continues_active_processing_after_cleanup_failure(database, monkeypatch):
    from services import expired_vehicle_refresh
    from tasks import task

    seed(database)
    monkeypatch.setattr(task, "SessionLocal", database.sessions)
    clock = Mock(wraps=datetime)
    clock.now.return_value = NOW
    monkeypatch.setattr(task, "datetime", clock)
    response = Mock(status_code=404)
    source = Mock()
    source.get.return_value = response
    monkeypatch.setattr(task, "SyncParsingClient", Mock(return_value=source))

    def fail_cleanup(db, now):
        monkeypatch.setattr(db, "commit", Mock(side_effect=RuntimeError("commit failed")))
        return cleanup_expired_irrelevant_vehicles(db, now)

    monkeypatch.setattr(expired_vehicle_refresh, "cleanup_expired_irrelevant_vehicles", fail_cleanup)
    result = task.parse_and_update_cars_with_expired_auction_date()
    assert (
        task.parse_and_update_cars_with_expired_auction_date.name
        == "tasks.task.parse_and_update_cars_with_expired_auction_date"
    )
    # The legacy deletion counter is assigned before commit even when that commit fails.
    assert result == {
        "checked": 1,
        "archived": 1,
        "reactivated_or_kept_active": 0,
        "deleted_irrelevant": 2,
        "updated": 0,
        "errors": 1,
    }
    source.get.assert_called_once_with("/api/v1/apicar/active", timeout=30.0)
    with database.sessions() as db:
        assert db.scalar(select(CarModel).where(CarModel.vin == "active")).relevance == RelevanceStatus.ARCHIVAL
        assert db.scalar(select(func.count()).select_from(CarModel)) == 7
