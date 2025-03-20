from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from schemas.user import (
    UserLoginRequestSchema,
    UserRegistrationRequestSchema,
    UserRegistrationResponseSchema,
)
from schemas.message import MessageResponseSchema
from core.security import get_jwt_auth_manager
from models.user import UserModel
from core.dependencies import Settings, get_settings
from core.security.interfaces import JWTAuthManagerInterface
from exceptions.security import BaseSecurityError
from db.session import get_db
from services.auth import verefy_invite

import datetime


router = APIRouter()


@router.post(
    "/sign-up/",
    response_model=UserRegistrationResponseSchema,
    summary="User Registration",
    description="Register a new user with an email and password.",
    status_code=status.HTTP_201_CREATED,
    responses={
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
    Endpoint for user registration.

    Registers a new user, hashes their password, and assigns them to the role provided in invitation code.
    If a user with the same email already exists, an HTTP 409 error is raised.
    In case of any unexpected issues during the creation process, an HTTP 500 error is returned.
    """

    decoded_code = verefy_invite(user_data, jwt_manager)

    result = await db.execute(select(UserModel).where(UserModel.email == user_data.email))
    existing_user = result.scalars().first()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with this email {user_data.email} already exists.",
        )

    try:
        new_user = UserModel.create(
            email=str(user_data.email),
            raw_password=user_data.password,
        )
        new_user.role_id = decoded_code.get("role_id")
        new_user.first_name = user_data.first_name
        new_user.last_name = user_data.last_name
        new_user.phone_number = user_data.phone_number
        new_user.date_of_birth = user_data.date_of_birth
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during user creation.",
        )

    return UserRegistrationResponseSchema.model_validate(new_user)


@router.post(
    "/login/",
    response_model=MessageResponseSchema,
    summary="User Login",
    description="Authenticate a user and return access and refresh tokens.",
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
    Endpoint for user login.

    Authenticates a user using their email and password.
    If authentication is successful, creates a new refresh token and
    returns both access and refresh tokens.
    """
    result = await db.execute(select(UserModel).where(UserModel.email == login_data.email))
    user = result.scalars().first()

    if not user or not user.verify_password(login_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    jwt_refresh_token = jwt_manager.create_refresh_token({"user_id": user.id})
    jwt_access_token = jwt_manager.create_access_token({"user_id": user.id})

    response.set_cookie("access_token", jwt_access_token, httponly=True, samesite="lax")
    response.set_cookie("refresh_token", jwt_refresh_token, httponly=True, samesite="lax")
    return {"message": "Login successful."}


@router.post(
    "/refresh/",
    response_model=MessageResponseSchema,
    summary="Refresh Access Token",
    description="Refresh the access token using a valid refresh token.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - The provided refresh token is invalid or expired.",
            "content": {"application/json": {"example": {"detail": "Token has expired."}}},
        },
        401: {
            "description": "Unauthorized - Refresh token not found.",
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
    Endpoint to refresh an access token.

    Validates the provided refresh token, extracts the user ID from it, and issues
    a new access token. If the token is invalid or expired, an error is returned.
    """
    refresh_token = request.cookies.get("refresh_token")
    try:
        decoded_token = jwt_manager.decode_refresh_token(refresh_token)
        user_id = decoded_token.get("user_id")
    except BaseSecurityError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )

    result = await db.execute(select(UserModel).filter_by(id=user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    new_access_token = jwt_manager.create_access_token({"user_id": user_id})
    new_refresh_token = jwt_manager.create_refresh_token({"user_id": user_id})
    response.set_cookie("access_token", new_access_token, httponly=True, samesite="lax")
    response.set_cookie("refresh_token", new_refresh_token, httponly=True, samesite="lax")

    return {"message": "Access token refreshed"}


@router.post(
    "/logout",
    response_model=MessageResponseSchema,
    summary="Logout",
    description="Logout the user by deleting the access and refresh tokens.",
    status_code=status.HTTP_200_OK,
)
async def logout(response: Response):
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"message": "Logged out"}
