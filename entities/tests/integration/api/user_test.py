# entities/tests/integration/api/users_test.py
from datetime import date
from typing import Dict, List

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import api.v1.routers.user as users_router
from core.dependencies import get_current_user, get_jwt_auth_manager
from main import app
from models.user import UserModel, UserRoleEnum, UserRoleModel

pytestmark = pytest.mark.anyio

API_PREFIX = "/api/v1/users"


# ============================== core role / user fixtures ==============================

@pytest.fixture
async def ensure_roles(db_session: AsyncSession) -> Dict[UserRoleEnum, int]:
    """
    Ensure USER and ADMIN roles exist and return their ids.
    Works without relying on external fixtures.
    """
    existing = {
        r.name: r.id
        for r in (await db_session.execute(select(UserRoleModel))).scalars()
    }
    needed = [UserRoleEnum.USER, UserRoleEnum.ADMIN]
    for role in needed:
        if role not in existing:
            db_session.add(UserRoleModel(name=role))
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(UserRoleModel).where(UserRoleModel.name.in_(needed))
        )
    ).scalars().all()
    out: Dict[UserRoleEnum, int] = {r.name: r.id for r in rows}
    await db_session.commit()
    return out


@pytest.fixture
async def ensure_test_user_has_role(
    db_session: AsyncSession,
    test_user: UserModel,
    ensure_roles: Dict[UserRoleEnum, int],
) -> UserModel:
    """
    Make sure `test_user` has role_id set (USER by default).
    """
    if getattr(test_user, "role_id", None) is None:
        test_user.role_id = ensure_roles[UserRoleEnum.USER]
        await db_session.commit()
    return test_user


# ============================== dependency overrides ==============================

@pytest.fixture
def as_admin_user():
    """
    Return minimal admin-like object for endpoints requiring ADMIN.
    """
    class AdminRole:
        name = UserRoleEnum.ADMIN

    class AdminUser:
        id = 777
        email = "admin@example.com"
        role = AdminRole()
        roles = [UserRoleEnum.ADMIN]

    app.dependency_overrides[get_current_user] = lambda: AdminUser()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def as_test_user(ensure_test_user_has_role: UserModel):
    """
    Return REAL ORM model for current user to avoid attribute errors / lazy loads.
    """
    app.dependency_overrides[get_current_user] = lambda: ensure_test_user_has_role
    try:
        yield ensure_test_user_has_role
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ============================== mocks ==============================

@pytest.fixture
def mock_check_admin_privileges(monkeypatch):
    """
    Disable admin privilege enforcement to isolate endpoint behavior.
    """
    monkeypatch.setattr(users_router, "check_admin_privileges", lambda *_: True, raising=True)


@pytest.fixture
def mock_generate_invite_link(monkeypatch):
    """
    Router awaits this function, so it MUST be async.
    """
    async def _fake_generate_invite_link(*_args, **_kwargs):
        return "https://inv/link/abc"
    monkeypatch.setattr(users_router, "generate_invite_link", _fake_generate_invite_link, raising=True)


@pytest.fixture
def mock_send_email(monkeypatch) -> List[dict]:
    """
    Capture outbound emails without sending.
    """
    calls: List[dict] = []

    async def _fake_send_email(to, subject, body):
        calls.append({"to": to, "subject": subject, "body": body})
        return None

    monkeypatch.setattr(users_router, "send_email", _fake_send_email, raising=True)
    return calls


# ============================== /invite/ ==============================

@pytest.mark.usefixtures("ensure_roles", "mock_check_admin_privileges", "mock_generate_invite_link", "mock_send_email")
async def test_invite_user_success(client, as_admin_user, db_session: AsyncSession):
    role_id = (
        await db_session.execute(select(UserRoleModel.id).where(UserRoleModel.name == UserRoleEnum.USER))
    ).scalar_one()

    payload = {"email": "newuser@example.com", "role_id": role_id, "expire_days_delta": 3}
    resp = await client.post(f"{API_PREFIX}/invite/", json=payload)

    assert resp.status_code == 201
    data = resp.json()
    assert data["invite_link"].startswith("https://inv/link/")


@pytest.mark.usefixtures("ensure_roles", "mock_check_admin_privileges")
async def test_invite_user_conflict_if_exists(client, as_admin_user, db_session: AsyncSession, ensure_test_user_has_role: UserModel):
    role_id = (
        await db_session.execute(select(UserRoleModel.id).where(UserRoleModel.name == UserRoleEnum.USER))
    ).scalar_one()

    payload = {"email": ensure_test_user_has_role.email, "role_id": role_id, "expire_days_delta": 1}
    resp = await client.post(f"{API_PREFIX}/invite/", json=payload)

    assert resp.status_code == 409
    assert "already exists" in resp.text


# ============================== /roles/ ==============================

@pytest.mark.usefixtures("ensure_roles")
async def test_get_user_roles_ok(client):
    resp = await client.get(f"{API_PREFIX}/roles/")
    assert resp.status_code == 200
    data = resp.json()
    assert "roles" in data and isinstance(data["roles"], list) and len(data["roles"]) >= 1


# ============================== /change-password/ ==============================

async def test_change_password_ok(client, as_test_user, monkeypatch):
    # Bypass heavy validation/hash logic.
    async def _noop_validate_and_change(user, data):
        return None

    monkeypatch.setattr(users_router, "validate_and_change_password", _noop_validate_and_change, raising=True)

    payload = {"old_password": "OldPass123!", "new_password_1": "NewPass123!", "new_password_2": "NewPass123!"}
    resp = await client.post(f"{API_PREFIX}/change-password/", json=payload)
    assert resp.status_code == 200
    assert "Password changed successfully" in resp.text


# ============================== /me (GET / PATCH) ==============================

async def test_get_me_ok(client, as_test_user: UserModel):
    resp = await client.get(f"{API_PREFIX}/me/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == as_test_user.email


async def test_patch_me_ok(client, as_test_user, monkeypatch):
    # Return valid values; SQLite Date requires a `date` object, not str.
    async def _fake_validate_and_update(user, data):
        return {
            "first_name": "New",
            "last_name": "Name",
            "phone_number": "+15555550123",
            "date_of_birth": date(1991, 2, 2),
        }

    monkeypatch.setattr(users_router, "validate_and_update_user_info", _fake_validate_and_update, raising=True)

    payload = {"first_name": "New", "last_name": "Name", "phone_number": "+15555550123", "date_of_birth": "1991-02-02"}
    resp = await client.patch(f"{API_PREFIX}/me/", json=payload)
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "New"


# ============================== change-email (POST) ==============================

async def test_request_email_change_ok(client, as_test_user, mock_send_email, monkeypatch):
    # Avoid recursive name collision and side effects from the service layer.
    async def _fake_service_request_email_change(user, data, db, jwt):
        return "https://confirm/email?token=abc"

    monkeypatch.setattr(users_router, "request_email_change", _fake_service_request_email_change, raising=True)

    payload = {"new_email": "new@example.com"}
    resp = await client.post(f"{API_PREFIX}/change-email/", json=payload)
    assert resp.status_code == 200
    assert "Email change request sent" in resp.text
    assert mock_send_email and mock_send_email[0]["to"]


# ============================== confirm-email (GET) ==============================

async def test_confirm_email_change_ok(client, db_session: AsyncSession, ensure_test_user_has_role: UserModel, monkeypatch):
    # emulate pending change stored in temp_email
    ensure_test_user_has_role.temp_email = "new2@example.com"
    await db_session.commit()

    class DummyJWT:
        def decode_user_interaction_token(self, token):
            return {"user_id": int(ensure_test_user_has_role.id), "new_email": "new2@example.com"}

    app.dependency_overrides[get_jwt_auth_manager] = lambda: DummyJWT()

    async def _fake_confirm_email_change(user, new_email):
        return None

    monkeypatch.setattr(users_router, "confirm_email_change", _fake_confirm_email_change, raising=True)

    try:
        resp = await client.get(f"{API_PREFIX}/confirm-email/", params={"token": "abc"})
        assert resp.status_code == 200

        refreshed = await db_session.get(UserModel, ensure_test_user_has_role.id)
        assert refreshed.email == "new2@example.com"
        assert refreshed.temp_email is None
    finally:
        app.dependency_overrides.pop(get_jwt_auth_manager, None)


# ============================== password reset (request / confirm) ==============================

@pytest.mark.usefixtures("ensure_roles")
async def test_password_reset_request_ok(client, db_session: AsyncSession, mock_send_email, monkeypatch, ensure_roles):
    # UserModel.create(...) doesn't accept role_id -> set it afterwards.
    u = UserModel.create(email="pwd@example.com", raw_password="Abc12345!")
    u.role_id = ensure_roles[UserRoleEnum.USER]
    db_session.add(u)
    await db_session.commit()

    async def _fake_request_password_reset(user, jwt):
        return "https://reset/link?token=zzz"

    monkeypatch.setattr(users_router, "request_password_reset", _fake_request_password_reset, raising=True)

    resp = await client.post(f"{API_PREFIX}/password-reset/request/", json={"email": "pwd@example.com"})
    assert resp.status_code == 200
    assert mock_send_email and "reset" in mock_send_email[0]["subject"].lower()


@pytest.mark.usefixtures("ensure_roles")
async def test_password_reset_confirm_ok(client, db_session: AsyncSession, monkeypatch, ensure_roles):
    u = UserModel.create(email="reset2@example.com", raw_password="Abc12345!")
    u.role_id = ensure_roles[UserRoleEnum.USER]
    db_session.add(u)
    await db_session.commit()

    class DummyJWT:
        def decode_user_interaction_token(self, token):
            # Router/service will use sub (email) to find the user
            return {"sub": "reset2@example.com"}

    app.dependency_overrides[get_jwt_auth_manager] = lambda: DummyJWT()

    async def _fake_confirm_password_reset(user, data):
        return None

    monkeypatch.setattr(users_router, "confirm_password_reset", _fake_confirm_password_reset, raising=True)

    try:
        resp = await client.post(
            f"{API_PREFIX}/password-reset/confirm/",
            json={"token": "zzz", "new_password": "NewPass123!"},
        )
        assert resp.status_code == 200
        assert "successfully reset" in resp.text.lower()
    finally:
        app.dependency_overrides.pop(get_jwt_auth_manager, None)


# ============================== admin list / get by email ==============================

@pytest.mark.usefixtures("ensure_roles", "mock_check_admin_privileges")
async def test_get_all_users_admin_ok(client, as_admin_user, db_session: AsyncSession, ensure_test_user_has_role: UserModel):
    resp = await client.get(f"{API_PREFIX}/", params={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert "users" in body and isinstance(body["users"], list)


@pytest.mark.usefixtures("ensure_roles", "mock_check_admin_privileges")
async def test_get_user_by_email_admin_ok(client, as_admin_user, ensure_test_user_has_role: UserModel):
    resp = await client.get(f"{API_PREFIX}/{ensure_test_user_has_role.email}/")
    assert resp.status_code == 200
    assert resp.json()["email"] == ensure_test_user_has_role.email
