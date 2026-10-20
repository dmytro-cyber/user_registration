"""SQLite fixtures, loaded only by tests that exercise persistence."""

import itertools
from datetime import date

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from models.user import UserModel, UserRoleEnum, UserRoleModel


@pytest.fixture
def _test_db_file(tmp_path):
    """Each test gets its own database, including writes committed by services."""
    return tmp_path / "test.db"


@pytest.fixture
async def engine(_test_db_file):
    """
    Async SQLAlchemy engine bound to the current test's SQLite file.
    """
    database_url = f"sqlite+aiosqlite:///{_test_db_file}"
    async_engine = create_async_engine(database_url, echo=False, future=True)

    from models import Base
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield async_engine
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await async_engine.dispose()


@pytest.fixture(scope="session")
def anyio_backend():
    """
    Force AnyIO to use asyncio backend for the whole test session.
    """
    return "asyncio"


@pytest.fixture
def async_session_factory(engine):
    """
    Factory for AsyncSession.
    """
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="function")
async def db_session(engine):
    """
    Fresh AsyncSession per test function.
    """
    SessionForTests = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with SessionForTests() as test_session:
        yield test_session
        await test_session.rollback()


@pytest.fixture(scope="function")
async def reset_db(db_session: AsyncSession):
    """
    Truncate critical tables between tests.
    """
    await db_session.execute(text("PRAGMA foreign_keys = OFF;"))
    for table_name in ("users", "user_roles"):
        try:
            await db_session.execute(text(f"DELETE FROM {table_name};"))
        except Exception:
            pass
    await db_session.commit()
    await db_session.execute(text("PRAGMA foreign_keys = ON;"))


@pytest.fixture(scope="function")
async def setup_roles(db_session: AsyncSession, reset_db):
    """
    Ensure default roles exist.
    """
    role_rows = [
        UserRoleModel(name=UserRoleEnum.USER),
        UserRoleModel(name=UserRoleEnum.ADMIN),
        UserRoleModel(name=UserRoleEnum.VEHICLE_MANAGER),
        UserRoleModel(name=UserRoleEnum.PART_MANAGER),
    ]
    db_session.add_all(role_rows)
    await db_session.commit()
    return role_rows


@pytest.fixture(scope="function")
async def test_user(db_session: AsyncSession, setup_roles):
    """
    Create a test user assigned to USER role, with all fields required by UserResponseSchema.
    """
    new_user = UserModel.create(email="testuser@example.com", raw_password="StrongPass123!")
    new_user.first_name = "John"
    new_user.last_name = "Doe"
    new_user.phone_number = "+15555550123"
    new_user.date_of_birth = date(1990, 1, 1)

    user_role = (
        await db_session.execute(select(UserRoleModel).where(UserRoleModel.name == UserRoleEnum.USER))
    ).scalars().first()
    new_user.role_id = user_role.id

    db_session.add(new_user)
    await db_session.commit()
    await db_session.refresh(new_user)
    return new_user


@pytest.fixture
async def seed_roles(engine):
    """
    Seed role rows in the current test's database.
    """
    async with engine.begin() as connection:
        await connection.execute(
            insert(UserRoleModel),
            [
                {"name": UserRoleEnum.USER},
                {"name": UserRoleEnum.ADMIN},
                {"name": UserRoleEnum.VEHICLE_MANAGER},
                {"name": UserRoleEnum.PART_MANAGER},
            ],
        )


@pytest.fixture
async def user_role_id(db_session: AsyncSession, seed_roles):
    """
    Return the id of USER role.
    """
    role_id_scalar = await db_session.execute(
        select(UserRoleModel.id).where(UserRoleModel.name == UserRoleEnum.USER)
    )
    return role_id_scalar.scalar_one()


async def create_user_with_role(db_session: AsyncSession, email: str, password: str, role_id: int) -> UserModel:
    """
    Create user with a specified role and return it.
    """
    created_user = UserModel.create(email=email, raw_password=password)
    created_user.role_id = role_id
    db_session.add(created_user)
    await db_session.commit()
    await db_session.refresh(created_user)
    return created_user


@pytest.fixture
def engine_sync(_test_db_file):
    """
    Synchronous engine over the same SQLite file to share data with async engine.
    """
    database_url = f"sqlite:///{_test_db_file}"
    sync_engine = create_engine(database_url, echo=False, future=True)
    from models import Base
    Base.metadata.create_all(bind=sync_engine)
    try:
        yield sync_engine
    finally:
        sync_engine.dispose()


@pytest.fixture(scope="function")
def db_session_sync(engine_sync):
    """
    Fresh sync Session per test function for task module tests.
    """
    SyncSession = sessionmaker(bind=engine_sync, autoflush=False, autocommit=False)
    sync_session = SyncSession()
    try:
        yield sync_session
    finally:
        try:
            sync_session.rollback()
        except Exception:
            pass
        sync_session.close()


@pytest.fixture
async def sqlite_fk_off(db_session: AsyncSession):
    """
    Temporarily disable SQLite foreign keys within the test.
    """
    await db_session.execute(text("PRAGMA foreign_keys = OFF;"))
    try:
        yield
    finally:
        await db_session.execute(text("PRAGMA foreign_keys = ON;"))


@pytest.fixture
async def user_role(db_session: AsyncSession):
    """
    Ensure USER role exists and return it.
    """
    role_row = (await db_session.execute(
        select(UserRoleModel).where(UserRoleModel.name == UserRoleEnum.USER)
    )).scalars().first()
    if not role_row:
        role_row = UserRoleModel(name=UserRoleEnum.USER)
        db_session.add(role_row)
        await db_session.commit()
        await db_session.refresh(role_row)
    return role_row


@pytest.fixture
def gen_vin():
    """
    Return a VIN generator function producing unique 17-char strings.
    """
    counter = itertools.count(1)
    def generate(prefix: str = "TESTVIN") -> str:
        counter_value = next(counter)
        padded_tail = f"{counter_value:010d}"
        head = (prefix + padded_tail)[:17]
        if len(head) < 17:
            head += "X" * (17 - len(head))
        return head
    return generate


@pytest.fixture
def create_car(db_session: AsyncSession):
    """
    Return an async factory that inserts a CarModel row with sensible defaults.
    """
    from datetime import datetime, timedelta

    from models.vehicle import CarModel, RelevanceStatus

    async def factory(
        vin: str | None = None,
        is_active: bool = True,
        **kwargs,
    ) -> "CarModel":
        car_row = CarModel(
            vin=vin or "TESTVIN000000000",
            vehicle=kwargs.get("vehicle", "2015 Toyota Camry"),
            engine_title=kwargs.get("engine_title", "2.5L"),
            mileage=kwargs.get("mileage", 120000),
            make=kwargs.get("make", "Toyota"),
            model=kwargs.get("model", "Camry"),
            year=kwargs.get("year", 2015),
            transmision=kwargs.get("transmision", "Automatic"),
            auction=kwargs.get("auction", "copart"),
            auction_name=kwargs.get("auction_name", "Live"),
            date=kwargs.get("date", datetime.utcnow() - timedelta(days=1)),
            relevance=RelevanceStatus.ACTIVE if is_active else RelevanceStatus.ARCHIVAL,
            avg_market_price=kwargs.get("avg_market_price", None),
            auction_fee=kwargs.get("auction_fee", None),
            maintenance=kwargs.get("maintenance", None),
            transportation=kwargs.get("transportation", None),
            labor=kwargs.get("labor", None),
            parts_cost=kwargs.get("parts_cost", None),
            predicted_total_investments=kwargs.get("predicted_total_investments", None),
            predicted_profit_margin=kwargs.get("predicted_profit_margin", None),
            predicted_profit_margin_percent=kwargs.get("predicted_profit_margin_percent", None),
            suggested_bid=kwargs.get("suggested_bid", 0),
            is_checked=kwargs.get("is_checked", False),
        )
        db_session.add(car_row)
        await db_session.commit()
        await db_session.refresh(car_row)
        return car_row

    return factory
