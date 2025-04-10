import pytest
from src.auth import create_access_token

@pytest.mark.asyncio
async def test_register_user(client):
    response = client.post(
        "/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "test@example.com"

@pytest.mark.asyncio
async def test_register_duplicate_user(client):
    response = client.post(
        "/register",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"]

@pytest.mark.asyncio
async def test_login(client):
    response = client.post(
        "/login",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert response.json()["token_type"] == "bearer"

@pytest.mark.asyncio
async def test_login_invalid_credentials(client):
    response = client.post(
        "/login",
        json={"email": "test@example.com", "password": "wrongpassword"}
    )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]

@pytest.mark.asyncio
async def test_get_me(client):
    token = create_access_token(data={"sub": "test@example.com"})
    response = client.get(
        "/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "test@example.com"

@pytest.mark.asyncio
async def test_get_me_unauthorized(client):
    response = client.get("/me")
    assert response.status_code == 401
    assert "Not authenticated" in response.json()["detail"]

@pytest.mark.asyncio
async def test_crud_operations(db_session):
    from src.crud import get_user_by_email, create_user
    from src.schemas import UserCreate

    user_in = UserCreate(email="crud@example.com", password="crudpassword")
    user = await create_user(db=db_session, user=user_in)
    assert user.email == "crud@example.com"

    fetched_user = await get_user_by_email(db=db_session, email="crud@example.com")
    assert fetched_user is not None
    assert fetched_user.email == "crud@example.com"

    non_existent_user = await get_user_by_email(db=db_session, email="nonexistent@example.com")
    assert non_existent_user is None