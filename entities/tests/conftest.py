from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.dependencies import get_settings
from core.security.token_manager import JWTAuthManager
from db.test_session import get_test_db_session
from main import app  # Оновлений імпорт (з кореня проєкту)
from models.user import UserModel, UserRoleEnum, UserRoleModel


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for all tests in the session."""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def settings():
    """Fixture for application settings."""
    return get_settings()


@pytest.fixture(scope="function")
async def db_session():
    """Fixture for an async test database session using SQLite."""
    async with get_test_db_session() as session:
        yield session


@pytest.fixture(scope="function")
def jwt_manager(settings):
    """Fixture for JWTAuthManager."""
    return JWTAuthManager(
        secret_key_access=settings.SECRET_KEY_ACCESS,
        secret_key_refresh=settings.SECRET_KEY_REFRESH,
        secret_key_user_interaction=settings.SECRET_KEY_USER_INTERACTION,
        algorithm=settings.JWT_SIGNING_ALGORITHM,
    )


@pytest.fixture(scope="function")
async def client():
    """Fixture for an async HTTP client to test FastAPI endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.fixture(scope="function")
async def reset_db(db_session: AsyncSession):
    """Fixture to reset the test database."""
    await db_session.execute(text("DELETE FROM users;"))
    await db_session.execute(text("DELETE FROM user_roles;"))
    await db_session.commit()
    await db_session.rollback()


@pytest.fixture(scope="function")
async def setup_roles(db_session: AsyncSession, reset_db):
    """Fixture to create user roles in the test database."""
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
    """Fixture to create a test user in the database."""
    user = UserModel.create(email="testuser@example.com", raw_password="StrongPass123!")
    user.role_id = (
        (await db_session.execute(select(UserRoleModel).where(UserRoleModel.name == UserRoleEnum.USER)))
        .scalars()
        .first()
        .id
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture(scope="function")
def mock_verefy_invite():
    """Fixture to mock the verefy_invite function."""
    from services.auth import verefy_invite

    mock = AsyncMock(wraps=verefy_invite)
    app.dependency_overrides[verefy_invite] = lambda: mock
    yield mock
    app.dependency_overrides.pop(verefy_invite, None)
