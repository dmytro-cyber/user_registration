import pytest
from core.security.utils import generate_secure_token
from schemas.user import UserRegistrationRequestSchema
from services.auth import verefy_invite
from fastapi import HTTPException
from tests.mock_jwt_manager import MockJWTAuthManager
from datetime import timedelta


def test_verify_invite_valid():
    jwt_manager = MockJWTAuthManager()
    valid_token = jwt_manager.create_refresh_token({"user_email": "test@example.com", "exp": 9999999999})
    user_data = UserRegistrationRequestSchema(
        email="test@example.com",
        password="Secure123!",
        invite_code=valid_token,
        first_name="Test",
        last_name="User",
        phone_number="123456789",
        date_of_birth="2000-01-01",
    )

    result = verefy_invite(user_data, jwt_manager)
    assert result["user_email"] == "test@example.com"


def test_verify_invite_expired():
    jwt_manager = MockJWTAuthManager()
    expired_token = jwt_manager.create_expired_token(
        {"user_email": "test@example.com", "exp": timedelta(minutes=6000)}
    )
    user_data = UserRegistrationRequestSchema(
        email="test@example.com",
        password="Secure123!",
        invite_code=expired_token,
        first_name="Test",
        last_name="User",
        phone_number="123456789",
        date_of_birth="2000-01-01",
    )

    with pytest.raises(HTTPException) as exc:
        verefy_invite(user_data, jwt_manager)

    assert exc.value.status_code == 400
    assert "has expired" in exc.value.detail
