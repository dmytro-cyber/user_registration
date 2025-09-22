import os
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from main import app
from models.user import UserModel, UserRoleModel, UserRoleEnum
from core.dependencies import get_current_user
from services import auth as services_auth


pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _env_tokens(monkeypatch):
    """
    Ensure cookie TTL env vars exist (endpoints read them).
    """
    monkeypatch.setenv("ACCESS_KEY_TIMEDELTA_MINUTES", "5")
    monkeypatch.setenv("REFRESH_KEY_TIMEDELTA_MINUTES", "10")


@pytest.fixture
async def user_role(db_session: AsyncSession):
    """
    Make sure default USER role exists (sign-up uses role_id from invite payload).
    """
    role = (await db_session.execute(
        select(UserRoleModel).where(UserRoleModel.name == UserRoleEnum.USER)
    )).scalars().first()
    if not role:
        role = UserRoleModel(name=UserRoleEnum.USER)
        db_session.add(role)
        await db_session.commit()
        await db_session.refresh(role)
    return role


@pytest.fixture
def mock_verify_invite_ok(monkeypatch, user_role):
    """
    Mock verify_invite() to return a valid payload.
    """
    def _fake_verify_invite(payload, jwt_manager):
        return {
            "user_email": payload.email,
            "role_id": user_role.id,
        }
    monkeypatch.setattr(services_auth, "verify_invite", _fake_verify_invite)
    return _fake_verify_invite


@pytest.fixture
def mock_verify_invite_broken(monkeypatch):
    """
    Mock verify_invite() to raise error (to test 500/validation flow).
    """
    def _broken(*_, **__):
        raise RuntimeError("invite decode failed")
    monkeypatch.setattr(services_auth, "verify_invite", _broken)
    return _broken


# -------------------------
# /sign-up/
# -------------------------

@pytest.mark.integration
async def test_signup_success(client: AsyncClient, db_session: AsyncSession, mock_verify_invite_ok):
    payload = {
        "email": "newuser@example.com",
        "password": "%5H7zfIwoee5",
        "first_name": "John",
        "last_name": "Doe",
        "phone_number": "+18882804331",
        "date_of_birth": "1990-01-01",
        "invitation_code": "stub-token"
    }
    r = await client.post("/api/v1/sign-up/", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["email"] == payload["email"]

    # User was created in DB
    db_user = (await db_session.execute(
        select(UserModel).where(UserModel.email == payload["email"])
    )).scalars().first()
    assert db_user is not None


@pytest.mark.integration
async def test_signup_conflict_email(client: AsyncClient, db_session: AsyncSession, mock_verify_invite_ok):
    # pre-create user
    u = UserModel.create("taken@example.com", "%5H7zfIwoee5")
    db_session.add(u)
    await db_session.commit()

    payload = {
        "email": "taken@example.com",
        "password": "%5H7zfIwoee5",
        "first_name": "Jane",
        "last_name": "D",
        "phone_number": "+18882804331",
        "date_of_birth": "1990-01-01",
        "invitation_code": "stub-token"
    }
    r = await client.post("/api/v1/sign-up/", json=payload)
    assert r.status_code == 409
    assert "already exists" in r.text


@pytest.mark.integration
async def test_signup_invalid_phone(client: AsyncClient, mock_verify_invite_ok):
    payload = {
        "email": "phonebad@example.com",
        "password": "%5H7zfIwoee5",
        "first_name": "P",
        "last_name": "B",
        "phone_number": "12345",   # invalid on purpose
        "date_of_birth": "1990-01-01",
        "invitation_code": "stub-token"
    }
    r = await client.post("/api/v1/sign-up/", json=payload)
    assert r.status_code == 400
    assert "Invalid phone" in r.text or "format" in r.text


@pytest.mark.integration
async def test_signup_invite_decode_error_returns_500(client: AsyncClient, mock_verify_invite_broken):
    payload = {
        "email": "inv@broken.com",
        "password": "%5H7zfIwoee5",
        "first_name": "I",
        "last_name": "B",
        "phone_number": "+18882804331",
        "date_of_birth": "1990-01-01",
        "invitation_code": "bad"
    }
    r = await client.post("/api/v1/sign-up/", json=payload)
    # ваш код обгортає несподівану помилку у 500
    assert r.status_code == 500


# -------------------------
# /login/
# -------------------------

@pytest.mark.integration
async def test_login_success_sets_cookies(client: AsyncClient, db_session: AsyncSession):
    email = "loginok@example.com"
    raw = "%5H7zfIwoee5"
    u = UserModel.create(email=email, raw_password=raw)
    db_session.add(u)
    await db_session.commit()

    r = await client.post("/api/v1/login/", json={"email": email, "password": raw})
    assert r.status_code == 201, r.text

    # Cookies should be set
    # httpx keeps Set-Cookie headers in r.headers; to reuse for next requests, update client cookies:
    client.cookies.update(r.cookies)
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies


@pytest.mark.integration
async def test_login_invalid_credentials(client: AsyncClient):
    r = await client.post("/api/v1/login/", json={"email": "nope@example.com", "password": "bad"})
    assert r.status_code == 401
    assert "Invalid email or password" in r.text


# -------------------------
# /refresh/
# -------------------------

@pytest.mark.integration
async def test_refresh_success(client: AsyncClient, db_session: AsyncSession, jwt_manager):
    # create user + set valid refresh cookie
    u = UserModel.create(email="ref@example.com", raw_password="%5H7zfIwoee5")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)

    refresh_token = jwt_manager.create_refresh_token({"user_id": u.id})
    client.cookies.set("refresh_token", refresh_token)

    r = await client.post("/api/v1/refresh/")
    assert r.status_code == 200, r.text
    client.cookies.update(r.cookies)
    # new tokens should be set
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies


@pytest.mark.integration
async def test_refresh_missing_cookie(client: AsyncClient):
    # no cookie set
    r = await client.post("/api/v1/refresh/")
    assert r.status_code == 403
    assert "Refresh token not found" in r.text


@pytest.mark.integration
async def test_refresh_invalid_token(client: AsyncClient):
    client.cookies.set("refresh_token", "totally-bogus")
    r = await client.post("/api/v1/refresh/")
    # ваш код мапить помилки декоду на 400
    assert r.status_code == 400


# -------------------------
# /logout/
# -------------------------

@pytest.fixture
def override_current_user():
    """
    Override get_current_user to always return a fake user object.
    """
    class _Dummy:
        id = 999
        email = "dummy@example.com"
    app.dependency_overrides[get_current_user] = lambda: _Dummy()
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.integration
async def test_logout_ok(client: AsyncClient, override_current_user):
    # put some cookies before logout
    client.cookies.set("access_token", "xxx")
    client.cookies.set("refresh_token", "yyy")

    r = await client.post("/api/v1/logout/")
    assert r.status_code == 200
    # Expect Set-Cookie headers with deletion; httpx merges cookies,
    # але можна перевірити, що після відповіді cookies зникли, якщо сервер ставить Max-Age=0
    client.cookies.update(r.cookies)
    assert client.cookies.get("access_token") in (None, "")
    assert client.cookies.get("refresh_token") in (None, "")


@pytest.mark.integration
async def test_logout_forbidden_without_user(client: AsyncClient):
    # Without override_current_user, dependency повинна впасти з 401/403
    r = await client.post("/api/v1/logout/")
    assert r.status_code in (401, 403)
