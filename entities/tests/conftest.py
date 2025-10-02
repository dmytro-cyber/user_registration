# conftest.py
import asyncio
import os
from types import SimpleNamespace
from pathlib import Path
import tempfile
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from sqlalchemy.orm import registry, sessionmaker
import pytest
from sqlalchemy import insert, select, create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from models.user import UserRoleModel, UserRoleEnum, UserModel

# ==== FastAPI app ====
from main import app

# ==== App dependencies to override ====
from core.dependencies import get_settings
from db.session import POSTGRESQL_DATABASE_URL  # just to ensure module import side-effects don't break
from core.security.token_manager import JWTAuthManager
import tasks.task as task_module

# ========= Logging (quieter tests) =========
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
for name in ("sqlalchemy.engine", "sqlalchemy.pool"):
    logging.getLogger(name).setLevel(logging.WARNING)


# ========= Test Settings =========
class TestSettings:
    """
    Minimal test settings stub that mimics the production Settings interface
    but uses in-memory/harmless defaults.
    """
    # DB (unused here; we create our own SQLite engine below)
    POSTGRES_USER = "test_user"
    POSTGRES_PASSWORD = "test_password"
    POSTGRES_HOST = "test_host"
    POSTGRES_DB_PORT = 5432
    POSTGRES_DB = "test_db"

    # JWT
    SECRET_KEY_ACCESS = "test_access_key"
    SECRET_KEY_REFRESH = "test_refresh_key"
    SECRET_KEY_USER_INTERACTION = "test_user_interaction_key"
    JWT_SIGNING_ALGORITHM = "HS256"

    # S3 (dummy)
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


# ========= event loop =========
@pytest.fixture(scope="session")
def event_loop():
    """
    Single event loop for all async tests in the session.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ========= async test DB (SQLite aiosqlite) =========
@pytest.fixture(scope="session")
def _test_db_file(tmp_path_factory):
    # Persistent across the whole test session
    tmpdir = tmp_path_factory.mktemp("db")
    return tmpdir / "test.db"


@pytest.fixture(scope="session")
async def engine(_test_db_file):
    url = f"sqlite+aiosqlite:///{_test_db_file}"
    eng = create_async_engine(url, echo=False, future=True)

    from models import Base  # <-- import the correct Base

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield eng
    finally:
        # Drop tables and close engine so Windows allows removing the file
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest.fixture(scope="session")
def anyio_backend():
    """
    Force AnyIO to use asyncio backend for the whole test session.
    This avoids ScopeMismatch with our session-scoped async fixtures.
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
    SessionTest = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with SessionTest() as session:
        yield session
        # Explicit rollback in case there are unclosed transactions
        await session.rollback()


# ========= dependency overrides: settings & get_db =========
@pytest.fixture(scope="session", autouse=True)
def override_settings_dependency():
    """
    Force the app to use TestSettings for the whole test session.
    """
    test_settings = TestSettings()
    app.dependency_overrides[get_settings] = lambda: test_settings
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(scope="function", autouse=True)
def override_db_dependency(db_session: AsyncSession):
    """
    Override the get_db dependency to use the test SQLite session.
    """
    from db.session import get_db as real_get_db  # for key

    async def _get_db_override():
        yield db_session

    app.dependency_overrides[real_get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(real_get_db, None)


# ========= Celery isolation (no Redis calls) =========
@pytest.fixture(scope="function", autouse=True)
def isolate_celery(monkeypatch):
    """
    Prevent any real Celery communication:
    - Patch celery app.send_task to a no-op stub.
    - If code sometimes calls .delay on tasks, we patch those too (best effort).
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
        # If celery app fails to import in tests, ignore. Endpoints might not touch it.
        pass

    # Optionally mark Celery eager if some tasks are imported directly
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")


# ========= JWT manager (helper) =========
@pytest.fixture(scope="function")
def jwt_manager():
    """
    Construct JWTAuthManager with test settings.
    """
    s = TestSettings()
    return JWTAuthManager(
        secret_key_access=s.SECRET_KEY_ACCESS,
        secret_key_refresh=s.SECRET_KEY_REFRESH,
        secret_key_user_interaction=s.SECRET_KEY_USER_INTERACTION,
        algorithm=s.JWT_SIGNING_ALGORITHM,
    )


# ========= HTTP client =========
@pytest.fixture(scope="function")
async def client():
    """
    Async HTTP client using in-process ASGITransport.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ========= DB helpers =========
@pytest.fixture(scope="function")
async def reset_db(db_session: AsyncSession):
    """
    Truncate critical tables between tests. Adjust table names to your schema.
    """
    # If you use foreign keys, consider disabling/enabling FK checks on SQLite.
    await db_session.execute(text("PRAGMA foreign_keys = OFF;"))
    for tbl in ("users", "user_roles"):
        try:
            await db_session.execute(text(f"DELETE FROM {tbl};"))
        except Exception:
            # Ignore if the table does not exist in the current test context
            pass
    await db_session.commit()
    await db_session.execute(text("PRAGMA foreign_keys = ON;"))


@pytest.fixture(scope="function")
async def setup_roles(db_session: AsyncSession, reset_db):
    """
    Create default roles for tests.
    """
    try:
        from models.user import UserRoleModel, UserRoleEnum
    except Exception as e:
        pytest.skip(f"User models not available: {e}")

    roles = [
        UserRoleModel(name=UserRoleEnum.USER),
        UserRoleModel(name=UserRoleEnum.ADMIN),
        UserRoleModel(name=UserRoleEnum.VEHICLE_MANAGER),
        UserRoleModel(name=UserRoleEnum.PART_MANAGER),
    ]
    db_session.add_all(roles)
    await db_session.commit()
    return roles


@pytest.fixture(scope="function")
async def test_user(db_session: AsyncSession, setup_roles):
    """
    Create a test user with USER role.
    """
    try:
        from models.user import UserModel, UserRoleModel, UserRoleEnum
        from sqlalchemy.future import select
    except Exception as e:
        pytest.skip(f"User models not available: {e}")

    user = UserModel.create(email="testuser@example.com", raw_password="StrongPass123!")
    role = (
        await db_session.execute(select(UserRoleModel).where(UserRoleModel.name == UserRoleEnum.USER))
    ).scalars().first()
    user.role_id = role.id
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
def patch_verify_invite(monkeypatch):
    def _fake_verify_invite(user_data, jwt_manager):
        # Return the minimum payload expected by the router
        return {
            "user_email": user_data.email,
            "role_id": 1,  # Existing role in the test DB (see setup above)
        }
    monkeypatch.setattr("services.auth.verify_invite", _fake_verify_invite)
    return _fake_verify_invite


@pytest.fixture(scope="session")
async def seed_roles(engine):
    # Create roles once per session
    async with engine.begin() as conn:
        await conn.execute(
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
    row = await db_session.execute(
        select(UserRoleModel.id).where(UserRoleModel.name == UserRoleEnum.USER)
    )
    return row.scalar_one()

async def create_user_with_role(db_session: AsyncSession, email: str, password: str, role_id: int) -> UserModel:
    u = UserModel.create(email=email, raw_password=password)
    u.role_id = role_id
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture(scope="session")
def engine_sync(_test_db_file):
    """
    Sync engine over the same SQLite file as the async engine.
    This way, tables/data are shared for both stacks.
    """
    url = f"sqlite:///{_test_db_file}"
    eng = create_engine(url, echo=False, future=True)
    # Create schema if the async part hasn't done it yet
    from models import Base
    Base.metadata.create_all(bind=eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="function")
def db_session_sync(engine_sync):
    """
    Separate sync Session per task test.
    """
    Session = sessionmaker(bind=engine_sync, autoflush=False, autocommit=False)
    sess = Session()
    try:
        yield sess
    finally:
        # Clean rollback in case of unclosed transactions
        try:
            sess.rollback()
        except Exception:
            pass
        sess.close()


@pytest.fixture(autouse=False)
def patch_task_sessionlocal(monkeypatch, db_session_sync):
    """
    Patch task_module.SessionLocal with a context manager returning our sync db_session_sync.
    NOTE: enable this fixture ONLY in task tests (via explicit fixture usage).
    """
    class _CM:
        def __enter__(self): return db_session_sync
        def __exit__(self, exc_type, exc, tb): pass

    class _SessionLocal:
        def __call__(self): return _CM()

    monkeypatch.setattr(task_module, "SessionLocal", _SessionLocal())


# ==== TestSettings -> task module (settings) ====
@pytest.fixture(autouse=False)
def patch_task_settings(monkeypatch):
    """
    Inject TestSettings into the task module (your class above).
    """
    s = TestSettings()
    monkeypatch.setattr(task_module, "settings", s)
    return s


# ==== Mock S3 client for task ====
from io import BytesIO

@pytest.fixture(autouse=False)
def mock_s3_for_task(monkeypatch):
    """
    Mock S3StorageClient in the task module.
    Returns a list of calls for assertions.
    """
    calls = []

    class _DummyS3:
        def __init__(self, **kwargs): pass
        def upload_fileobj_sync(self, key, fileobj: BytesIO):
            data = fileobj.read().decode("utf-8")
            calls.append((key, data))

    monkeypatch.setattr(task_module, "S3StorageClient", _DummyS3)
    return calls


# ==== ROI / fees helpers (deterministic) ====
@pytest.fixture(autouse=False)
def mock_roi_and_fees(monkeypatch):
    class _ROI:
        roi = 25.0          # => investments = avg / 1.25
        profit_margin = 10.0  # => margin_amount = avg * 0.10

    def _load_default_roi(db): return _ROI()
    def _load_fees(db, auction, base): return {"stub": True}
    def _apply_fees(base, fees): return round((base or 0.0) * 0.05, 2)  # 5%

    monkeypatch.setattr(task_module, "_load_default_roi", _load_default_roi)
    monkeypatch.setattr(task_module, "_load_fees", _load_fees)
    monkeypatch.setattr(task_module, "_apply_fees", _apply_fees)


# ==== HTTP router mock for task ====
@pytest.fixture(autouse=False)
def http_router_mock(monkeypatch):
    """
    Provide a router function for http_get_with_retries:
    - you can pass 2 handlers: for /apicar/get/ (history) and for /parsers/scrape/dc (parser)
    """
    handlers = {"history": None, "parser": None}

    def set_handlers(history_resp_callable, parser_resp_callable):
        handlers["history"] = history_resp_callable
        handlers["parser"] = parser_resp_callable

    def _router(url, headers, timeout):
        if "/apicar/get/" in url:
            return handlers["history"]()
        return handlers["parser"]()

    monkeypatch.setattr(task_module, "http_get_with_retries", _router)
    return set_handlers

@pytest.fixture(scope="session", autouse=True)
def silence_app_loggers_for_tests():
    """
    During tests, strip console handlers from noisy app loggers and
    raise their levels to WARNING. Pytest will still capture logs and
    show them only for failing tests.
    """
    noisy = ("vehicles_router", "auth_router", "admin_router")
    for name in noisy:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(logging.WARNING)

    root = logging.getLogger()
    if os.environ.get("PYTEST_FORCE_ROOT_WARNING", "1") == "1":
        root.setLevel(logging.WARNING)
