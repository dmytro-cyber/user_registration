import pytest
from httpx import AsyncClient
from db.session import get_db
from core.security.token_manager import JWTAuthManager


@pytest.mark.asyncio
async def test_register_user(client: AsyncClient):
    response = await client.post(
        "/auth/register",
        json={
            "email": "user@example.com",
            "password": "SecurePass123!",
            "invite_code": "valid_invite_token",
            "first_name": "John",
            "last_name": "Doe",
            "phone_number": "123456789",
            "date_of_birth": "1990-01-01",
        },
    )
    assert response.status_code == 201
    assert response.json()["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_login(client: AsyncClient):
    response = await client.post("/auth/login", json={"email": "user@example.com", "password": "SecurePass123!"})
    assert response.status_code == 200
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_access_protected_route(client: AsyncClient):
    login_response = await client.post("/auth/login", json={"email": "user@example.com", "password": "SecurePass123!"})
    access_token = login_response.cookies.get("access_token")

    response = await client.get("/protected-route", cookies={"access_token": access_token})
    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"
