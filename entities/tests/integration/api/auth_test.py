import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.dependencies import get_current_user
from main import app
from models.user import UserModel, UserRoleEnum, UserRoleModel
from schemas.user import UserInvitationRequestSchema
from services.user import generate_invite_link

pytestmark = pytest.mark.anyio


@pytest.mark.integration
async def test_signup_success(client: AsyncClient, db_session: AsyncSession, mock_verify_invite_ok, invite_code):
    """
    Successful sign-up returns 201 and persists the new user.
    """
    request_payload = {
        "invite_code": invite_code,
        "email": "newuser@example.com",
        "password": "%5H7zfIwoee5",
        "first_name": "John",
        "last_name": "Doe",
        "phone_number": "+18882804331",
        "date_of_birth": "1990-01-01",
    }
    response = await client.post("/api/v1/sign-up/", json=request_payload)
    assert response.status_code == 201, response.text
    response_body = response.json()
    assert response_body["email"] == request_payload["email"]

    created_user = (await db_session.execute(
        select(UserModel).where(UserModel.email == request_payload["email"])
    )).scalars().first()
    assert created_user is not None


@pytest.mark.integration
async def test_signup_conflict_email(client: AsyncClient, db_session: AsyncSession, jwt_manager):
    """
    Signing up with an already taken email returns 409.
    """
    role_id = (
        await db_session.execute(
            select(UserRoleModel.id).where(UserRoleModel.name == UserRoleEnum.USER)
        )
    ).scalar_one_or_none()
    if role_id is None:
        db_session.add(UserRoleModel(name=UserRoleEnum.USER))
        await db_session.commit()
        role_id = (
            await db_session.execute(
                select(UserRoleModel.id).where(UserRoleModel.name == UserRoleEnum.USER)
            )
        ).scalar_one()

    taken_email = "taken@example.com"
    existing_user = UserModel.create(email=taken_email, raw_password="%5H7zfIwoee5")
    existing_user.role_id = role_id
    db_session.add(existing_user)
    await db_session.commit()

    invitation_request = UserInvitationRequestSchema(
        email=taken_email,
        role_id=role_id,
        expire_days_delta=1,
    )
    invite_link = await generate_invite_link(invitation_request, jwt_manager)
    invite_code = invite_link.split("invite=")[-1]

    request_payload = {
        "email": taken_email,
        "password": "%5H7zfIwoee5",
        "first_name": "Jane",
        "last_name": "D",
        "phone_number": "+18882804331",
        "date_of_birth": "1990-01-01",
        "invite_code": invite_code,
    }
    response = await client.post("/api/v1/sign-up/", json=request_payload)
    assert response.status_code == 409, response.text
    assert "already exists" in response.text


@pytest.mark.integration
async def test_signup_invalid_phone(client: AsyncClient, mock_verify_invite_ok, jwt_manager):
    """
    Sign-up with invalid US phone number returns 400.
    """
    email = "phonebad@example.com"
    invitation_request = UserInvitationRequestSchema(
        email=email,
        role_id=1,
        expire_days_delta=1,
    )
    invite_link = await generate_invite_link(invitation_request, jwt_manager)
    invite_code = invite_link.split("invite=")[-1]
    request_payload = {
        "email": email,
        "password": "%5H7zfIwoee5",
        "first_name": "P",
        "last_name": "B",
        "phone_number": "12345",
        "date_of_birth": "1990-01-01",
        "invite_code": invite_code,
    }
    response = await client.post("/api/v1/sign-up/", json=request_payload)
    assert response.status_code == 400
    assert "Invalid US phone number." in response.text


@pytest.mark.integration
async def test_signup_invite_decode_error_returns_400(client: AsyncClient, mock_verify_invite_broken):
    """
    Broken invitation verification returns 400.
    """
    request_payload = {
        "email": "inv@broken.com",
        "password": "%5H7zfIwoee5",
        "first_name": "I",
        "last_name": "B",
        "phone_number": "+18882804331",
        "date_of_birth": "1990-01-01",
        "invite_code": "bad"
    }
    response = await client.post("/api/v1/sign-up/", json=request_payload)
    assert response.status_code == 400


@pytest.mark.integration
async def test_login_success_sets_cookies(client: AsyncClient, db_session: AsyncSession):
    """
    Successful login sets access and refresh cookies.
    """
    email = "loginok@example.com"
    password_plain = "%5H7zfIwoee5"
    user = UserModel.create(email=email, raw_password=password_plain)
    user.role_id = 1
    db_session.add(user)
    await db_session.commit()

    response = await client.post("/api/v1/login/", json={"email": email, "password": password_plain})
    assert response.status_code == 201, response.text
    client.cookies.update(response.cookies)
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies


@pytest.mark.integration
async def test_login_invalid_credentials(client: AsyncClient):
    """
    Invalid credentials return 401.
    """
    response = await client.post("/api/v1/login/", json={"email": "nope@example.com", "password": "%5H7zfIwoee5"})
    assert response.status_code == 401
    assert "Invalid email or password" in response.text


@pytest.mark.integration
async def test_refresh_success(client: AsyncClient, db_session: AsyncSession, jwt_manager):
    """
    Refreshing with a valid refresh token returns 200 and new cookies.
    """
    user = UserModel.create(email="ref@example.com", raw_password="%5H7zfIwoee5")
    user.role_id = 1
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    refresh_token = jwt_manager.create_refresh_token({"user_id": user.id})
    response = await client.post("/api/v1/refresh/", cookies={"refresh_token": refresh_token})
    assert response.status_code == 200, response.text
    client.cookies.update(response.cookies)
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies


@pytest.mark.integration
async def test_refresh_missing_cookie(client: AsyncClient):
    """
    Missing refresh cookie returns 403.
    """
    response = await client.post("/api/v1/refresh/")
    assert response.status_code == 403
    assert "Refresh token not found" in response.text


@pytest.mark.integration
async def test_refresh_invalid_token(client: AsyncClient):
    """
    Invalid refresh token returns 400.
    """
    client.cookies.set("refresh_token", "totally-bogus")
    response = await client.post("/api/v1/refresh/")
    assert response.status_code == 400


@pytest.fixture
def _override_current_user():
    """
    Override get_current_user for logout test.
    """
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": 999, "email": "dummy@example.com"})()
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.integration
async def test_logout_ok(client: AsyncClient, _override_current_user):
    """
    Logout clears auth cookies.
    """
    response = await client.post("/api/v1/logout/", cookies={"access_token": "xxx", "refresh_token": "yyy"})
    assert response.status_code == 200
    client.cookies.update(response.cookies)
    assert client.cookies.get("access_token") in (None, "")
    assert client.cookies.get("refresh_token") in (None, "")


@pytest.mark.integration
async def test_logout_forbidden_without_user(client: AsyncClient):
    """
    Logout without user dependency returns 401/403.
    """
    response = await client.post("/api/v1/logout/")
    assert response.status_code in (401, 403)
