import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta
import os

from models.user import UserModel, UserRoleModel, UserRoleEnum  # Додаємо UserRoleModel
from exceptions.security import BaseSecurityError

@pytest.mark.asyncio
class TestAuthEndpoints:
    """Test suite for authentication endpoints."""

    @pytest.mark.parametrize(
        "invite_code, first_name, last_name, phone_number, date_of_birth, email, password",  # Змінюємо invitation_code на invite_code
        [
            ("valid_code", "John", "Doe", "+12064471575", "1990-01-01", "newuser@gmail.com", "ZXCzxc!@#123"),  # Valid case
            ("valid_code", "", "", "+12064471575", "1985-05-15", "anotheruser@gmail.com", "ZXCzxc!@#123"),  # Empty first_name and last_name
        ],
        ids=["valid_user", "empty_names"]
    )
    async def test_sign_up_success(
        self, client: AsyncClient, db_session: AsyncSession, mock_verefy_invite,
        invite_code, first_name, last_name, phone_number, date_of_birth, email, password, setup_roles, reset_db  # Змінюємо invitation_code на invite_code
    ):
        """Test successful user registration with valid data."""
        # Get the USER role
        user_role = (await db_session.execute(
            select(UserRoleModel).where(UserRoleModel.name == UserRoleEnum.USER)
        )).scalars().first()
        
        assert user_role is not None, f"Role {UserRoleEnum.USER} not found in database"
        
        # Setup mock for verefy_invite
        mock_verefy_invite.return_value = {
            "user_email": email,
            "role_id": user_role.id
        }

        # Request data
        request_data = {
            "invite_code": invite_code,  # Змінюємо invitation_code на invite_code
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
            "date_of_birth": date_of_birth,
            "email": email,
            "password": password,
        }

        # Make request
        response = await client.post("/api/v1/sign-up/", json=request_data)
        if response.status_code != 201:
            print(f"Response: {response.json()}")
            print(f"Request data: {request_data}")
            print(f"Status code: {response.status_code}")
        assert response.status_code == 201

        # Verify response
        response_data = response.json()
        assert response_data["email"] == email
        assert response_data["first_name"] == first_name
        assert response_data["last_name"] == last_name
        assert response_data["phone_number"] == phone_number

        # Verify user in database
        user = (await db_session.execute(
            select(UserModel).where(UserModel.email == email)
        )).scalars().first()
        assert user is not None
        assert user.verify_password(password)

    @pytest.mark.parametrize(
        "invite_code, first_name, last_name, phone_number, date_of_birth, email, password, expected_error",  # Змінюємо invitation_code на invite_code
        [
            ("valid_code", "John", "Doe", "invalid_phone", "1990-01-01", "newuser@example.com", "StrongPass123!", "Invalid phone number format"),  # Invalid phone
            ("valid_code", "John", "Doe", "+380123456789", "1990-01-01", "invalid-email", "StrongPass123!", "Invalid email format"),  # Invalid email
            ("valid_code", "John", "Doe", "+380123456789", "1990-01-01", "newuser@example.com", "weak", "Password must be at least 8 characters long"),  # Weak password
            ("valid_code", "John", "Doe", "+380123456789", "invalid-date", "newuser@example.com", "StrongPass123!", "time data 'invalid-date' does not match format"),  # Invalid date
        ],
        ids=["invalid_phone", "invalid_email", "weak_password", "invalid_date"]
    )
    async def test_sign_up_validation_errors(
        self, client: AsyncClient, db_session: AsyncSession, mock_verefy_invite,
        invite_code, first_name, last_name, phone_number, date_of_birth, email, password, expected_error, setup_roles, reset_db  # Змінюємо invitation_code на invite_code
    ):
        """Test user registration with invalid input data."""
        # Get the USER role
        user_role = (await db_session.execute(
            select(UserRoleModel).where(UserRoleModel.name == UserRoleEnum.USER)
        )).scalars().first()
        
        assert user_role is not None, f"Role {UserRoleEnum.USER} not found in database"
        
        # Setup mock for verefy_invite
        mock_verefy_invite.return_value = {
            "user_email": email,
            "role_id": user_role.id
        }

        # Request data with invalid fields
        request_data = {
            "invite_code": invite_code,  # Змінюємо invitation_code на invite_code
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
            "date_of_birth": date_of_birth,
            "email": email,
            "password": password,
        }

        # Make request
        response = await client.post("/api/v1/sign-up/", json=request_data)
        assert response.status_code == 400
        assert expected_error in response.json()["detail"]

    async def test_sign_up_existing_user(
        self, client: AsyncClient, db_session: AsyncSession, mock_verefy_invite, test_user, setup_roles, reset_db
    ):
        """Test user registration with an existing email."""
        # Setup mock for verefy_invite
        mock_verefy_invite.return_value = {
            "user_email": test_user.email,
            "role_id": test_user.role_id
        }

        # Request data with existing email
        request_data = {
            "invite_code": "valid_code",  # Змінюємо invitation_code на invite_code
            "first_name": "John",
            "last_name": "Doe",
            "phone_number": "+380123456789",
            "date_of_birth": "1990-01-01",
            "email": test_user.email,
            "password": "StrongPass123!"
        }

        # Make request
        response = await client.post("/api/v1/sign-up/", json=request_data)
        assert response.status_code == 409
        assert response.json()["detail"] == f"A user with this email {test_user.email} already exists."

    async def test_sign_up_invalid_invitation(
        self, client: AsyncClient, db_session: AsyncSession, mock_verefy_invite, setup_roles, reset_db
    ):
        """Test user registration with an invalid invitation code."""
        # Setup mock to raise an exception
        mock_verefy_invite.side_effect = BaseSecurityError("Invalid invitation code")

        # Request data
        request_data = {
            "invitation_code": "invalid_code",
            "first_name": "John",
            "last_name": "Doe",
            "phone_number": "+380123456789",
            "date_of_birth": "1990-01-01",
            "email": "newuser@example.com",
            "password": "StrongPass123!"
        }

        # Make request
        response = await client.post("/sign-up/", json=request_data)
        assert response.status_code == 400
        assert "Invalid invitation code" in response.json()["detail"]

    async def test_login_success(
        self, client: AsyncClient, db_session: AsyncSession, test_user, jwt_manager, reset_db
    ):
        """Test successful user login with valid credentials."""
        # Request data
        request_data = {
            "email": test_user.email,
            "password": "StrongPass123!"
        }

        # Make request
        response = await client.post("/login/", json=request_data)
        assert response.status_code == 201
        assert response.json()["message"] == "Login successful."

        # Verify tokens in cookies
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies

        # Verify token validity
        access_token = response.cookies["access_token"]
        refresh_token = response.cookies["refresh_token"]
        decoded_access = jwt_manager.decode_access_token(access_token)
        decoded_refresh = jwt_manager.decode_refresh_token(refresh_token)
        assert decoded_access["user_id"] == test_user.id
        assert decoded_refresh["user_id"] == test_user.id

    @pytest.mark.parametrize(
        "email, password, expected_detail",
        [
            ("wronguser@example.com", "StrongPass123!", "Invalid email or password."),  # Wrong email
            ("testuser@example.com", "WrongPass123!", "Invalid email or password."),  # Wrong password
            ("", "StrongPass123!", "Invalid email or password."),  # Empty email
            ("testuser@example.com", "", "Invalid email or password."),  # Empty password
        ],
        ids=["wrong_email", "wrong_password", "empty_email", "empty_password"]
    )
    async def test_login_invalid_credentials(
        self, client: AsyncClient, db_session: AsyncSession, test_user, email, password, expected_detail, reset_db
    ):
        """Test user login with invalid credentials."""
        # Request data with invalid credentials
        request_data = {
            "email": email,
            "password": password
        }

        # Make request
        response = await client.post("/login/", json=request_data)
        assert response.status_code == 401
        assert response.json()["detail"] == expected_detail

    async def test_refresh_success(
        self, client: AsyncClient, db_session: AsyncSession, test_user, jwt_manager, reset_db
    ):
        """Test successful token refresh with a valid refresh token."""
        # Generate tokens for the user
        refresh_token = jwt_manager.create_refresh_token({"user_id": test_user.id})
        access_token = jwt_manager.create_access_token({"user_id": test_user.id})

        # Set cookies
        client.cookies.set("refresh_token", refresh_token)
        client.cookies.set("access_token", access_token)

        # Make request
        response = await client.post("/refresh/")
        assert response.status_code == 200
        assert response.json()["message"] == "Access token refreshed"

        # Verify new tokens in cookies
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies

        # Verify new token validity
        new_access_token = response.cookies["access_token"]
        new_refresh_token = response.cookies["refresh_token"]
        decoded_access = jwt_manager.decode_access_token(new_access_token)
        decoded_refresh = jwt_manager.decode_refresh_token(new_refresh_token)
        assert decoded_access["user_id"] == test_user.id
        assert decoded_refresh["user_id"] == test_user.id

    async def test_refresh_no_token(
        self, client: AsyncClient, db_session: AsyncSession, reset_db
    ):
        """Test token refresh with no refresh token in cookies."""
        # Make request without refresh token
        response = await client.post("/refresh/")
        assert response.status_code == 403
        assert response.json()["detail"] == "Refresh token not found"

    async def test_refresh_invalid_token(
        self, client: AsyncClient, db_session: AsyncSession, test_user, jwt_manager, reset_db
    ):
        """Test token refresh with an expired refresh token."""
        # Generate an expired refresh token
        expired_token = jwt_manager.create_refresh_token(
            {"user_id": test_user.id},
            expires_delta=timedelta(seconds=-1)
        )

        # Set cookies
        client.cookies.set("refresh_token", expired_token)

        # Make request
        response = await client.post("/refresh/")
        assert response.status_code == 400
        assert "Token has expired" in response.json()["detail"]

    async def test_refresh_user_not_found(
        self, client: AsyncClient, db_session: AsyncSession, jwt_manager, reset_db
    ):
        """Test token refresh for a non-existent user."""
        # Generate a token for a non-existent user
        refresh_token = jwt_manager.create_refresh_token({"user_id": 9999})

        # Set cookies
        client.cookies.set("refresh_token", refresh_token)

        # Make request
        response = await client.post("/refresh/")
        assert response.status_code == 404
        assert response.json()["detail"] == "User not found."

    async def test_logout_success(
        self, client: AsyncClient, db_session: AsyncSession, test_user, jwt_manager, reset_db
    ):
        """Test successful user logout with a valid access token."""
        # Generate an access token for the user
        access_token = jwt_manager.create_access_token({"user_id": test_user.id})

        # Set cookies
        client.cookies.set("access_token", access_token)

        # Make request
        response = await client.post("/logout/")
        assert response.status_code == 200
        assert response.json()["message"] == "Logged out"

        # Verify cookies are cleared
        assert "access_token" not in response.cookies or response.cookies["access_token"] == ""
        assert "refresh_token" not in response.cookies or response.cookies["refresh_token"] == ""

    async def test_logout_unauthorized(
        self, client: AsyncClient, db_session: AsyncSession, reset_db
    ):
        """Test user logout without an access token."""
        # Make request without access token
        response = await client.post("/logout/")
        assert response.status_code == 401
        assert "Not authenticated" in response.json()["detail"]