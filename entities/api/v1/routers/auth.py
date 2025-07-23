import logging
import logging.handlers
import os

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import Settings, get_current_user, get_jwt_auth_manager, get_settings
from core.security.interfaces import JWTAuthManagerInterface
from crud.user import create_user, get_user_by_email, get_user_by_id
from db.session import get_db
from exceptions.security import BaseSecurityError
from models.user import UserModel
from models.validators.user import validate_phone_number
from schemas.message import MessageResponseSchema
from schemas.user import (
    UserLoginRequestSchema,
    UserRegistrationRequestSchema,
    UserRegistrationResponseSchema,
)
from services.auth import verify_invite
from services.cookie import delete_token_cookie, set_token_cookie

load_dotenv()

# Configure logging for production environment
logger = logging.getLogger("auth_router")
logger.setLevel(logging.DEBUG)  # Set the default logging level

# Define formatter for structured logging
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - [RequestID: %(request_id)s] - [UserID: %(user_id)s] - %(message)s"
)

# Comment out file logging setup to disable writing to file
# log_directory = "logs"
# if not os.path.exists(log_directory):
#     os.makedirs(log_directory)
# file_handler = logging.handlers.RotatingFileHandler(
#     filename="logs/auth.log",
#     maxBytes=10 * 1024 * 1024,  # 10 MB
#     backupCount=5,  # Keep up to 5 backup files
# )
# file_handler.setFormatter(formatter)
# file_handler.setLevel(logging.DEBUG)

# Set up console handler for debug output
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# Add handlers to the logger (only console handler is active)
# logger.addHandler(file_handler)  # Comment out to disable file logging
logger.addHandler(console_handler)


# Custom filter to add context (RequestID, UserID)
class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, "request_id", "N/A")
        record.user_id = getattr(record, "user_id", "N/A")
        return True


logger.addFilter(ContextFilter())

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

    Args:
        user_data (UserRegistrationRequestSchema): The data for the new user.
        db (AsyncSession): The database session dependency.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        UserRegistrationResponseSchema: The registered user's data.

    Raises:
        HTTPException: 400 if the phone number format is invalid.
        HTTPException: 409 if a user with the given email already exists.
        HTTPException: 500 if an error occurs during user creation.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Starting user registration for email: {user_data.email}", extra=extra)

    try:
        payload = verify_invite(user_data, jwt_manager)
        logger.debug(f"Decoded invitation code: {payload}", extra=extra)

        existing_user = await get_user_by_email(db, payload.get("user_email"))
        if existing_user:
            logger.warning(f"User with email {user_data.email} already exists", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A user with this email {user_data.email} already exists.",
            )

        try:
            validate_phone_number(user_data.phone_number)
            logger.debug(f"Phone number {user_data.phone_number} is valid", extra=extra)
        except ValueError as exc:
            logger.error(f"Invalid phone number: {str(exc)}", extra=extra)
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
        logger.info(f"User {user_data.email} registered successfully", extra=extra)
        return UserRegistrationResponseSchema.model_validate(new_user)
    except HTTPException as e:
        logger.error(f"Failed to register user with email {user_data.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during user registration for email {user_data.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during user creation.",
        )


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

    Args:
        response (Response): The FastAPI response object to set cookies.
        login_data (UserLoginRequestSchema): The login credentials.
        db (AsyncSession): The database session dependency.
        settings (Settings): Application settings dependency.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        MessageResponseSchema: Confirmation message of successful login.

    Raises:
        HTTPException: 401 if the email or password is invalid.
        HTTPException: 500 if an error occurs during login.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Login attempt for email: {login_data.email}", extra=extra)

    try:
        user = await get_user_by_email(db, login_data.email)
        if not user or not user.verify_password(login_data.password):
            logger.warning(f"Failed login attempt for email: {login_data.email} - Invalid credentials", extra=extra)
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
        logger.info(f"User {login_data.email} logged in successfully", extra=extra)
        return {"message": "Login successful."}
    except HTTPException as e:
        logger.error(f"Failed to login user with email {login_data.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during login for email {login_data.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


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

    Args:
        request (Request): The FastAPI request object to access cookies.
        response (Response): The FastAPI response object to set cookies.
        db (AsyncSession): The database session dependency.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        MessageResponseSchema: Confirmation message of successful token refresh.

    Raises:
        HTTPException: 400 if the refresh token is invalid or expired.
        HTTPException: 403 if the refresh token is not found in cookies.
        HTTPException: 404 if the user associated with the token does not exist.
    """
    request_id = str(id(request))
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Starting access token refresh", extra=extra)

    try:
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            logger.warning("Refresh token not found in cookies", extra=extra)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Refresh token not found")

        try:
            decoded_token = jwt_manager.decode_refresh_token(refresh_token)
            user_id = decoded_token.get("user_id")
            logger.debug(f"Decoded refresh token for user_id: {user_id}", extra=extra)
        except BaseSecurityError as error:
            logger.error(f"Invalid or expired refresh token: {str(error)}", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            )

        user = await get_user_by_id(db, user_id)
        if not user:
            logger.warning(f"User with id {user_id} not found", extra=extra)
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
        logger.info(f"Access token refreshed for user_id: {user_id}", extra=extra)
        return {"message": "Access token refreshed"}
    except HTTPException as e:
        logger.error(f"Failed to refresh access token: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while refreshing access token: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


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

    Args:
        response (Response): The FastAPI response object to delete cookies.
        current_user (UserModel): The currently authenticated user.

    Returns:
        MessageResponseSchema: Confirmation message of successful logout.

    Raises:
        HTTPException: 403 if the user is not authenticated.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info("User logout initiated", extra=extra)

    try:
        delete_token_cookie(response, "access_token")
        delete_token_cookie(response, "refresh_token")
        logger.info(f"User logged out successfully {current_user.email}", extra=extra)
        return {"message": "Logged out"}
    except Exception as e:
        logger.error(f"Failed to logout user {current_user.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )
