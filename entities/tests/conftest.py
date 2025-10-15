# conftest.py
import asyncio
import os
import types
from types import SimpleNamespace
import logging
import itertools
from datetime import date
from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text, insert, select, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from db.session import get_db as real_get_db
from main import app
from core.dependencies import get_settings
from db.session import POSTGRESQL_DATABASE_URL  # noqa: F401
from core.security.token_manager import JWTAuthManager
import tasks.task as task_module
from models.user import UserRoleModel, UserRoleEnum, UserModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
for logger_name in ("sqlalchemy.engine", "sqlalchemy.pool"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)


class TestSettings:
    """
    Minimal test settings that mimic production interface with harmless defaults.
    """
    POSTGRES_USER = "test_user"
    POSTGRES_PASSWORD = "test_password"
    POSTGRES_HOST = "test_host"
    POSTGRES_DB_PORT = 5432
    POSTGRES_DB = "test_db"

    SECRET_KEY_ACCESS = "test_access_key"
    SECRET_KEY_REFRESH = "test_refresh_key"
    SECRET_KEY_USER_INTERACTION = "test_user_interaction_key"
    JWT_SIGNING_ALGORITHM = "HS256"

    MINIO_HOST = "minio-test"
    MINIO_PORT = 9000
    MINIO_ROOT_USER = "minioadmin"
    MINIO_ROOT_PASSWORD = "minioadmin"
    MINIO_STORAGE = "test-bucket"

    S3_STORAGE_HOST = "minio-test"
    S3_STORAGE_PORT = 9000
    S3_STORAGE_ACCESS_KEY = "minioadmin"
    S3_STORAGE_SECRET_KEY = "minioadmin"
    S3_BUCKET_NAME = "test-bucket"

    PARSERS_AUTH_TOKEN = "test-parsers-token"

    @property
    def S3_STORAGE_ENDPOINT(self) -> str:
        return f"http://{self.S3_STORAGE_HOST}:{self.S3_STORAGE_PORT}"


@pytest.fixture(scope="session")
def event_loop():
    """
    Single asyncio event loop for the whole test session.
    """
    loop_instance = asyncio.new_event_loop()
    yield loop_instance
    loop_instance.close()


@pytest.fixture(scope="session")
def _test_db_file(tmp_path_factory):
    """
    SQLite file path persisted for the whole test session.
    """
    tmp_dir = tmp_path_factory.mktemp("db")
    return tmp_dir / "test.db"


@pytest.fixture(scope="session")
async def engine(_test_db_file):
    """
    Async SQLAlchemy engine bound to a SQLite file for the whole session.
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


@pytest.fixture(scope="session")
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


@pytest.fixture(scope="session", autouse=True)
def override_settings_dependency():
    """
    Force app to use TestSettings for the whole session.
    """
    settings_obj = TestSettings()
    app.dependency_overrides[get_settings] = lambda: settings_obj
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(scope="function", autouse=True)
def override_db_dependency(db_session: AsyncSession):
    """
    Override get_db to use the test session by default.
    """
    from db.session import get_db as real_get_db

    async def get_db_override():
        yield db_session

    app.dependency_overrides[real_get_db] = get_db_override
    yield
    app.dependency_overrides.pop(real_get_db, None)


@pytest.fixture(scope="function", autouse=True)
def isolate_celery(monkeypatch):
    """
    Prevent any real Celery I/O during tests.
    """
    try:
        from core.celery_config import app as celery_app
        monkeypatch.setattr(
            celery_app,
            "send_task",
            lambda *args, **kwargs: SimpleNamespace(id="test-task-id", state="SUCCESS"),
            raising=True,
        )
    except Exception:
        pass
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")


@pytest.fixture(scope="function")
def jwt_manager():
    """
    JWT manager configured with test secrets.
    """
    settings_obj = TestSettings()
    return JWTAuthManager(
        secret_key_access=settings_obj.SECRET_KEY_ACCESS,
        secret_key_refresh=settings_obj.SECRET_KEY_REFRESH,
        secret_key_user_interaction=settings_obj.SECRET_KEY_USER_INTERACTION,
        algorithm=settings_obj.JWT_SIGNING_ALGORITHM,
    )


@pytest.fixture(scope="function")
async def client():
    """
    In-process HTTP client for the FastAPI app.
    """
    asgi_transport = ASGITransport(app=app)
    async with AsyncClient(transport=asgi_transport, base_url="http://test") as async_client:
        yield async_client


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
def patch_verify_invite(monkeypatch):
    """
    Patch services.auth.verify_invite with a permissive version.
    """
    def fake_verify_invite(user_data, jwt_manager):
        return {"user_email": user_data.email, "role_id": 1}
    monkeypatch.setattr("services.auth.verify_invite", fake_verify_invite)
    return fake_verify_invite


@pytest.fixture(scope="session")
async def seed_roles(engine):
    """
    Seed role rows once per session.
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


@pytest.fixture(scope="session")
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


@pytest.fixture(autouse=False)
def patch_task_sessionlocal(monkeypatch, db_session_sync):
    """
    Patch task_module.SessionLocal to return the test sync session.
    """
    class _ContextManager:
        def __enter__(self): return db_session_sync
        def __exit__(self, exc_type, exc, tb): pass

    class _SessionLocalProxy:
        def __call__(self): return _ContextManager()

    monkeypatch.setattr(task_module, "SessionLocal", _SessionLocalProxy())


@pytest.fixture(autouse=False)
def patch_task_settings(monkeypatch):
    """
    Inject TestSettings into the task module.
    """
    settings_obj = TestSettings()
    monkeypatch.setattr(task_module, "settings", settings_obj)
    return settings_obj


@pytest.fixture(autouse=False)
def mock_s3_for_task(monkeypatch):
    """
    Mock S3StorageClient used by the task module.
    """
    upload_calls: list[tuple[str, str]] = []

    class DummyS3Client:
        def __init__(self, **kwargs): ...
        def upload_fileobj_sync(self, key: str, fileobj: BytesIO):
            data_str = fileobj.read().decode("utf-8")
            upload_calls.append((key, data_str))

    monkeypatch.setattr(task_module, "S3StorageClient", DummyS3Client)
    return upload_calls


@pytest.fixture(autouse=False)
def mock_roi_and_fees(monkeypatch):
    """
    Deterministic ROI/fees helpers for task tests.
    """
    class ROIStub:
        roi = 25.0
        profit_margin = 10.0

    def load_default_roi(_db): return ROIStub()
    def load_fees(_db, _auction, _base): return {"stub": True}
    def apply_fees(base_amount, _fees): return round((base_amount or 0.0) * 0.05, 2)

    monkeypatch.setattr(task_module, "_load_default_roi", load_default_roi)
    monkeypatch.setattr(task_module, "_load_fees", load_fees)
    monkeypatch.setattr(task_module, "_apply_fees", apply_fees)


@pytest.fixture(autouse=False)
def http_router_mock(monkeypatch):
    """
    Router for http_get_with_retries used by task module.
    """
    handlers = {"history": None, "parser": None}

    def set_handlers(history_response_callable, parser_response_callable):
        handlers["history"] = history_response_callable
        handlers["parser"] = parser_response_callable

    def router(url, headers, timeout):
        if "/apicar/get/" in url:
            return handlers["history"]()
        return handlers["parser"]()

    monkeypatch.setattr(task_module, "http_get_with_retries", router)
    return set_handlers


@pytest.fixture(scope="session", autouse=True)
def silence_app_loggers_for_tests():
    """
    Silence noisy app loggers during tests so they don't spam stdout.
    Pytest все одно збирає їх і показує тільки для фейлів.
    """
    noisy_loggers = (
        "vehicles_router",
        "auth_router",
        "admin_router",
        "bidding_hub_router",
        "users_router"
    )
    for name in noisy_loggers:
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.WARNING)

    root = logging.getLogger()
    root.setLevel(logging.WARNING)


@pytest.fixture
def override_bidding_hub_user(test_user):
    """
    Override user dependency for the bidding_hub router and for global get_current_user.
    """
    from core.dependencies import get_current_user as core_get_current_user
    import api.v1.routers.bidding_hub as bidding_hub_router_module

    dummy_user = types.SimpleNamespace(id=int(test_user.id), email=getattr(test_user, "email", "test@example.com"))
    app.dependency_overrides[core_get_current_user] = lambda: dummy_user
    app.dependency_overrides[bidding_hub_router_module.get_current_user] = lambda: dummy_user
    try:
        yield dummy_user
    finally:
        app.dependency_overrides.pop(core_get_current_user, None)
        app.dependency_overrides.pop(bidding_hub_router_module.get_current_user, None)


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
def as_admin():
    """
    Override get_current_user to return an admin-like object.
    """
    from core.dependencies import get_current_user as core_get_current_user

    class AdminRole: name = UserRoleEnum.ADMIN
    class AdminUser:
        id = 777
        email = "admin@example.com"
        is_admin = True
        role = AdminRole()
        roles = [UserRoleEnum.ADMIN]
        scopes = ["admin"]

    app.dependency_overrides[core_get_current_user] = lambda: AdminUser()
    try:
        yield
    finally:
        app.dependency_overrides.pop(core_get_current_user, None)


@pytest.fixture
def patch_admin_module(monkeypatch):
    """
    Patch admin router helpers: locks and celery send_task.
    """
    from api.v1.routers import admin as admin_router_module
    monkeypatch.setattr(admin_router_module, "is_kickoff_busy", lambda: False, raising=True)
    monkeypatch.setattr(admin_router_module, "set_kickoff_lock", lambda _id: None, raising=True)
    monkeypatch.setattr(
        admin_router_module.celery_app,
        "send_task",
        lambda *args, **kwargs: SimpleNamespace(id="fake-task-id"),
        raising=True,
    )


@pytest.fixture
def patch_admin_module_lock_busy(monkeypatch):
    """
    Patch admin router to simulate locked kickoff state.
    """
    from api.v1.routers import admin as admin_router_module
    monkeypatch.setattr(admin_router_module, "is_kickoff_busy", lambda: True, raising=True)


class _MockHTTPResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._json = data
        import json as _json
        self.text = _json.dumps(data)
    def json(self) -> dict:
        return self._json


class _MockAsyncClient:
    """
    AsyncClient stub returning canned responses for admin upload endpoints.
    """
    def __init__(self, *_, **__):
        self._closed = False
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        self._closed = True
    async def post(self, url: str, **kwargs):
        if url.endswith("/api/v1/parsers/scrape/iaai/fees"):
            fake_payload = {
                "source": "iaai",
                "payment_method": "standard",
                "fees": {
                    "high_volume_buyer_fees": {
                        "fees": {
                            "0.00-99.99": "25.00",
                            "100.00-199.99": "50.00",
                            "15000.00+": "2% of sale price"
                        }
                    },
                    "internet_bid_buyer_fees": {
                        "fees": {
                            "0.00-99.99": "5.00",
                            "100.00-199.99": "10.00",
                            "15000.00+": "1.5% of sale price"
                        }
                    },
                    "service_fee": {"amount": 95.0, "currency": "USD"},
                    "environmental_fee": {"amount": 15.0, "currency": "USD"},
                    "title_handling_fee": {"amount": 20.0, "currency": "USD"},
                },
                "scraped_at": "2025-01-01 00:00:00",
            }
            return _MockHTTPResponse(200, fake_payload)
        if url.endswith("/startup"):
            return _MockHTTPResponse(200, {"ok": True})
        return _MockHTTPResponse(404, {"detail": "not mocked"})


@pytest.fixture
def patch_admin_httpx_client(monkeypatch):
    """
    Replace httpx.AsyncClient inside admin router with a stub.
    """
    import api.v1.routers.admin as admin_router_module
    import httpx as real_httpx_module
    monkeypatch.setattr(admin_router_module, "httpx", real_httpx_module, raising=False)
    monkeypatch.setattr(admin_router_module.httpx, "AsyncClient", _MockAsyncClient, raising=True)


@pytest.fixture(autouse=True)
def _env_tokens(monkeypatch):
    """
    Ensure auth endpoints find required TTL env vars.
    """
    monkeypatch.setenv("ACCESS_KEY_TIMEDELTA_MINUTES", "5")
    monkeypatch.setenv("REFRESH_KEY_TIMEDELTA_MINUTES", "10")


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
async def invite_code(jwt_manager, db_session):
    """
    Create a valid invitation code for sign-up flows.
    """
    from services.user import generate_invite_link
    from schemas.user import UserInvitationRequestSchema

    role_id_value = (
        await db_session.execute(
            select(UserRoleModel.id).where(UserRoleModel.name == UserRoleEnum.USER)
        )
    ).scalar_one()

    invite_schema = UserInvitationRequestSchema(
        email="newuser@example.com",
        role_id=role_id_value,
        expire_days_delta=1,
    )
    invitation_link = await generate_invite_link(invite_schema, jwt_manager)
    return invitation_link.split("invite=")[-1]


@pytest.fixture
def mock_verify_invite_ok(monkeypatch, user_role):
    """
    Patch services.auth.verify_invite to return a valid payload.
    """
    from services import auth as services_auth
    def fake_verify_invite(payload, jwt_manager):
        return {"user_email": payload.email, "role_id": user_role.id}
    monkeypatch.setattr(services_auth, "verify_invite", fake_verify_invite)
    return fake_verify_invite


@pytest.fixture
def mock_verify_invite_broken(monkeypatch):
    """
    Patch services.auth.verify_invite to raise a decoding error.
    """
    from services import auth as services_auth
    def broken_verify_invite(*_, **__):
        raise RuntimeError("invite decode failed")
    monkeypatch.setattr(services_auth, "verify_invite", broken_verify_invite)
    return broken_verify_invite


@pytest.fixture
def override_current_user():
    """
    Override get_current_user globally with a dummy object.
    """
    from core.dependencies import get_current_user as core_get_current_user
    class DummyUser:
        id = 999
        email = "dummy@example.com"
    app.dependency_overrides[core_get_current_user] = lambda: DummyUser()
    try:
        yield
    finally:
        app.dependency_overrides.pop(core_get_current_user, None)


@pytest.fixture
def use_test_user(test_user):
    """
    Override get_current_user with a lightweight object mirroring test_user id.
    """
    from core.dependencies import get_current_user as core_get_current_user
    test_user_id = int(test_user.id)
    app.dependency_overrides[core_get_current_user] = (lambda uid=test_user_id: SimpleNamespace(id=uid))
    try:
        yield
    finally:
        app.dependency_overrides.pop(core_get_current_user, None)


@pytest.fixture
def fresh_app_db(engine):
    """
    Provide the app a brand-new AsyncSession per HTTP request to avoid cross-transaction issues.
    """
    from db.session import get_db as real_get_db
    SessionForApp = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def get_db_override():
        async with SessionForApp() as request_session:
            yield request_session

    app.dependency_overrides[real_get_db] = get_db_override
    try:
        yield
    finally:
        app.dependency_overrides.pop(real_get_db, None)


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
