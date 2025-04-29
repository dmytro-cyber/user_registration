# api/v1/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.user import (
    UserLoginRequestSchema,
    UserRegistrationRequestSchema,
    UserRegistrationResponseSchema,
)
from models.user import UserModel
from schemas.message import MessageResponseSchema
from core.dependencies import get_jwt_auth_manager
from models.validators.user import validate_phone_number
from core.dependencies import Settings, get_current_user, get_settings
from core.security.interfaces import JWTAuthManagerInterface
from exceptions.security import BaseSecurityError
from db.session import get_db
from services.auth import verify_invite
from services.cookie import set_token_cookie, delete_token_cookie
from crud.user import create_user, get_user_by_email, get_user_by_id
from dotenv import load_dotenv
import os
import logging

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/sign-up/",
    response_model=UserRegistrationResponseSchema,
    summary="Register a new user",
    description="Registers a new user with email, password, and additional details. Assigns the user to a role based on the invitation code.",
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "description": "Bad Request - Invalid phone number format.",
            "content": {"application/json": {"example": {"detail": "Invalid phone number format."}}},
        },
        409: {
            "description": "Conflict - User with this email already exists.",
            "content": {
                "application/json": {"example": {"detail": "A user with this email test@example.com already exists."}}
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred during user creation.",
            "content": {"application/json": {"example": {"detail": "An error occurred during user creation."}}},
        },
    },
)
async def register_user(
    user_data: UserRegistrationRequestSchema,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> UserRegistrationResponseSchema:
    """
    Registers a new user with the provided email, password, and additional details.

    - Validates the invitation code and extracts user email and role.
    - Checks if a user with the same email already exists; raises HTTP 409 if true.
    - Validates the phone number format; raises HTTP 400 if invalid.
    - Creates a new user, hashes the password, and assigns the role from the invitation.
    - Raises HTTP 500 if an error occurs during user creation.
    """
    logger.info(f"Starting user registration for email: {user_data.email}")

    payload = verify_invite(user_data, jwt_manager)
    logger.debug(f"Decoded invitation code: {payload}")

    existing_user = await get_user_by_email(db, payload.get("user_email"))
    if existing_user:
        logger.warning(f"User with email {user_data.email} already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with this email {user_data.email} already exists.",
        )

    try:
        validate_phone_number(user_data.phone_number)
        logger.debug(f"Phone number {user_data.phone_number} is valid")
    except ValueError as exc:
        logger.error(f"Invalid phone number: {str(exc)}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    new_user = await create_user(
        db=db,
        email=payload.get("user_email"),
        raw_password=user_data.password,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        phone_number=user_data.phone_number,
        date_of_birth=user_data.date_of_birth,
        role_id=payload.get("role_id"),
    )
    return UserRegistrationResponseSchema.model_validate(new_user)


@router.post(
    "/login/",
    response_model=MessageResponseSchema,
    summary="Log in a user",
    description="Authenticates a user with email and password, returning access and refresh tokens as cookies.",
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {
            "description": "Unauthorized - Invalid email or password.",
            "content": {"application/json": {"example": {"detail": "Invalid email or password."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the request.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def login_user(
    response: Response,
    login_data: UserLoginRequestSchema,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> MessageResponseSchema:
    """
    Authenticates a user and sets access and refresh tokens as cookies.

    - Validates the user's email and password.
    - If credentials are invalid, raises HTTP 401.
    - Generates access and refresh tokens using JWT.
    - Sets tokens as HTTP cookies with specified expiration times.
    - Raises HTTP 500 if an error occurs during processing.
    """
    logger.info(f"Login attempt for email: {login_data.email}")

    user = await get_user_by_email(db, login_data.email)
    if not user or not user.verify_password(login_data.password):
        logger.warning(f"Failed login attempt for email: {login_data.email} - Invalid credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    jwt_refresh_token = jwt_manager.create_refresh_token({"user_id": user.id})
    jwt_access_token = jwt_manager.create_access_token({"user_id": user.id})

    set_token_cookie(
        response=response,
        key="access_token",
        value=jwt_access_token,
        max_age=int(os.getenv("ACCESS_KEY_TIMEDELTA_MINUTES")) * 60,
    )
    set_token_cookie(
        response=response,
        key="refresh_token",
        value=jwt_refresh_token,
        max_age=int(os.getenv("REFRESH_KEY_TIMEDELTA_MINUTES")) * 60,
    )
    logger.info(f"User {login_data.email} logged in successfully")
    return {"message": "Login successful."}


@router.post(
    "/refresh/",
    response_model=MessageResponseSchema,
    summary="Refresh access token",
    description="Refreshes the access token using a valid refresh token provided in cookies.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - The provided refresh token is invalid or expired.",
            "content": {"application/json": {"example": {"detail": "Token has expired."}}},
        },
        403: {
            "description": "Forbidden - Refresh token not found in cookies.",
            "content": {"application/json": {"example": {"detail": "Refresh token not found."}}},
        },
        404: {
            "description": "Not Found - The user associated with the token does not exist.",
            "content": {"application/json": {"example": {"detail": "User not found."}}},
        },
    },
)
async def refresh_access_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> MessageResponseSchema:
    """
    Refreshes the access token using a refresh token.

    - Retrieves the refresh token from cookies.
    - Validates the refresh token and extracts the user ID; raises HTTP 400 if invalid or expired.
    - Checks if the user exists; raises HTTP 404 if not.
    - Generates new access and refresh tokens and sets them as cookies.
    - Raises HTTP 403 if the refresh token is not found in cookies.
    """
    logger.info("Starting access token refresh")

    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        logger.warning("Refresh token not found in cookies")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Refresh token not found")

    try:
        decoded_token = jwt_manager.decode_refresh_token(refresh_token)
        user_id = decoded_token.get("user_id")
        logger.debug(f"Decoded refresh token for user_id: {user_id}")
    except BaseSecurityError as error:
        logger.error(f"Invalid or expired refresh token: {str(error)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )

    user = await get_user_by_id(db, user_id)
    if not user:
        logger.warning(f"User with id {user_id} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    new_access_token = jwt_manager.create_access_token({"user_id": user_id})
    new_refresh_token = jwt_manager.create_refresh_token({"user_id": user_id})
    set_token_cookie(
        response=response,
        key="access_token",
        value=new_access_token,
        max_age=int(os.getenv("ACCESS_KEY_TIMEDELTA_MINUTES")) * 60,
    )
    set_token_cookie(
        response=response,
        key="refresh_token",
        value=new_refresh_token,
        max_age=int(os.getenv("REFRESH_KEY_TIMEDELTA_MINUTES")) * 60,
    )
    logger.info(f"Access token refreshed for user_id: {user_id}")
    return {"message": "Access token refreshed"}


@router.post(
    "/logout/",
    response_model=MessageResponseSchema,
    summary="Log out a user",
    description="Logs out the user by deleting access and refresh token cookies.",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "OK - User logged out successfully.",
            "content": {"application/json": {"example": {"message": "Logged out"}}},
        },
        403: {
            "description": "Forbidden - User not authenticated.",
            "content": {"application/json": {"example": {"detail": "User not authenticated."}}},
        },
    },
)
async def logout(response: Response, current_user: UserModel = Depends(get_current_user)) -> MessageResponseSchema:
    """
    Logs out the user by deleting access and refresh token cookies.

    - Removes the access and refresh tokens from the cookies.
    - Returns a success message upon logout.
    """
    logger.info("User logout initiated")

    delete_token_cookie(response, "access_token")
    delete_token_cookie(response, "refresh_token")

    logger.info(f"User logged out successfully {current_user.email}")
    return {"message": "Logged out"}
