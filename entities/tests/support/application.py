"""HTTP application fixtures; importing these boots the FastAPI application."""

import logging
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.dependencies import get_settings
from core.security.token_manager import JWTAuthManager
from main import app
from models.user import UserRoleEnum, UserRoleModel
from tests.support.settings import TestSettings


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


@pytest.fixture
def patch_verify_invite(monkeypatch):
    """
    Patch services.auth.verify_invite with a permissive version.
    """
    def fake_verify_invite(user_data, jwt_manager):
        return {"user_email": user_data.email, "role_id": 1}
    monkeypatch.setattr("services.auth.verify_invite", fake_verify_invite)
    return fake_verify_invite


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
    import api.v1.routers.bidding_hub as bidding_hub_router_module
    from core.dependencies import get_current_user as core_get_current_user

    dummy_user = types.SimpleNamespace(id=int(test_user.id), email=getattr(test_user, "email", "test@example.com"))
    app.dependency_overrides[core_get_current_user] = lambda: dummy_user
    app.dependency_overrides[bidding_hub_router_module.get_current_user] = lambda: dummy_user
    try:
        yield dummy_user
    finally:
        app.dependency_overrides.pop(core_get_current_user, None)
        app.dependency_overrides.pop(bidding_hub_router_module.get_current_user, None)


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
def patch_filter_enqueue(monkeypatch):
    """HTTP tests stub PostgreSQL queueing; the real queue has PostgreSQL tests."""
    enqueue = AsyncMock()
    from services import vehicle_filters

    monkeypatch.setattr(vehicle_filters, "enqueue_filter_kickoff", enqueue)
    return enqueue


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
    import httpx as real_httpx_module

    import api.v1.routers.admin as admin_router_module
    monkeypatch.setattr(admin_router_module, "httpx", real_httpx_module, raising=False)
    monkeypatch.setattr(admin_router_module, "ParsingClient", _MockAsyncClient, raising=True)


@pytest.fixture
async def invite_code(jwt_manager, db_session):
    """
    Create a valid invitation code for sign-up flows.
    """
    from schemas.user import UserInvitationRequestSchema
    from services.user import generate_invite_link

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
