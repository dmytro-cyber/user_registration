import logging
import logging.handlers
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_current_user, get_jwt_auth_manager
from core.security.interfaces import JWTAuthManagerInterface
from crud.user import (
    get_all_roles,
    get_filtered_users,
    get_role_by_name,
    get_user_by_email,
    get_user_by_id,
    update_user_info,
    update_user_password,
    update_user_role,
)
from db.session import get_db
from models.user import UserModel, UserRoleEnum
from schemas.message import MessageResponseSchema
from schemas.user import (
    ChangePasswordRequestSchema,
    PasswordResetConfirmSchema,
    PasswordResetRequestSchema,
    SendInviteRequestSchema,
    UpdateEmailSchema,
    UserAdminListResponseSchema,
    UserInvitationRequestSchema,
    UserInvitationResponseSchema,
    UserResponseSchema,
    UserRoleListResponseSchema,
    UserUpdateRequestSchema,
)
from services.email import send_email
from services.user import (
    check_admin_privileges,
    confirm_email_change,
    confirm_password_reset,
    generate_invite_link,
    prepare_roles_response,
    prepare_user_list_response,
    prepare_user_response,
    request_email_change,
    request_password_reset,
    send_invite_email,
    validate_and_change_password,
    validate_and_update_user_info,
)

# Configure logging for production environment
logger = logging.getLogger("users_router")
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
#     filename="logs/users.log",
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

router = APIRouter(prefix="/users")


@router.post(
    "/invite/",
    response_model=UserInvitationResponseSchema,
    summary="Invite a new user",
    description="Invites a new user by generating an invitation code and sending it via email.",
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {
            "description": "Forbidden - Only ADMIN can invite users.",
            "content": {"application/json": {"example": {"detail": "You must be an ADMIN to perform this action."}}},
        },
        409: {
            "description": "Conflict - User with this email already exists.",
            "content": {"application/json": {"example": {"detail": "A user with this email already exists."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred during user invitation.",
            "content": {"application/json": {"example": {"detail": "An error occurred during user invitation."}}},
        },
    },
)
async def invite_user(
    user_data: UserInvitationRequestSchema,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> UserInvitationResponseSchema:
    """
    Invites a new user by sending an email with a unique invitation code.

    Args:
        user_data (UserInvitationRequestSchema): The data of the user to invite.
        current_user (UserModel): The currently authenticated user.
        db (AsyncSession): The database session dependency.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        UserInvitationResponseSchema: The response containing the invitation link.

    Raises:
        HTTPException: 403 if the current user is not an admin.
        HTTPException: 409 if a user with the given email already exists.
        HTTPException: 500 if an error occurs during the invitation process.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(
        f"User {current_user.email} is attempting to invite a new user with email: {user_data.email}", extra=extra
    )

    try:
        check_admin_privileges(current_user)

        existing_user = await get_user_by_email(db, user_data.email)
        if existing_user:
            logger.warning(f"User with email {user_data.email} already exists", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A user with this email {user_data.email} already exists.",
            )

        invite_link = await generate_invite_link(user_data, jwt_manager)
        logger.info(f"Invitation link generated for email {user_data.email}: {invite_link}", extra=extra)
        return UserInvitationResponseSchema(invite_link=invite_link)
    except HTTPException as e:
        logger.error(f"Failed to invite user with email {user_data.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during user invitation for email {user_data.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during user invitation.",
        )


@router.get(
    "/roles/",
    response_model=UserRoleListResponseSchema,
    summary="Get available user roles",
    description="Retrieves a list of all available user roles in the system.",
    status_code=status.HTTP_200_OK,
    responses={
        404: {
            "description": "Not Found - No roles found.",
            "content": {"application/json": {"example": {"detail": "No roles found"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while fetching roles.",
            "content": {"application/json": {"example": {"detail": "An error occurred during user roles fetching."}}},
        },
    },
)
async def get_user_roles(db: AsyncSession = Depends(get_db)) -> UserRoleListResponseSchema:
    """
    Retrieves a list of all available user roles.

    Args:
        db (AsyncSession): The database session dependency.

    Returns:
        UserRoleListResponseSchema: The list of available roles.

    Raises:
        HTTPException: 404 if no roles are found.
        HTTPException: 500 if an error occurs while fetching roles.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Fetching all user roles", extra=extra)

    try:
        roles = await get_all_roles(db)
        if not roles:
            logger.warning("No roles found in the database", extra=extra)
            raise HTTPException(status_code=404, detail="No roles found")

        logger.info(f"Successfully fetched {len(roles)} roles", extra=extra)
        return prepare_roles_response(roles)
    except HTTPException as e:
        logger.error(f"Failed to fetch roles: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching roles: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during user roles fetching.",
        )


@router.post(
    "/assign-role/",
    response_model=MessageResponseSchema,
    summary="Assign a role to a user",
    description="Assigns a specified role (USER, MODERATOR, ADMIN) to a user by their email.",
    status_code=status.HTTP_200_OK,
    responses={
        403: {
            "description": "Forbidden - Only ADMIN can assign roles.",
            "content": {"application/json": {"example": {"detail": "You must be an ADMIN to perform this action."}}},
        },
        404: {
            "description": "Not Found - User not found.",
            "content": {"application/json": {"example": {"detail": "User not found."}}},
        },
        400: {
            "description": "Bad Request - Invalid role.",
            "content": {"application/json": {"example": {"detail": "Invalid role."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while assigning the role.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def assign_role(
    email: str,
    role: UserRoleEnum,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> MessageResponseSchema:
    """
    Assigns a role to a user by their email.

    Args:
        email (str): The email of the user to assign the role to.
        role (UserRoleEnum): The role to assign (USER, MODERATOR, ADMIN).
        db (AsyncSession): The database session dependency.
        current_user (UserModel): The currently authenticated user.

    Returns:
        MessageResponseSchema: Confirmation message of the role assignment.

    Raises:
        HTTPException: 403 if the current user is not an admin.
        HTTPException: 404 if the user is not found.
        HTTPException: 400 if the role is invalid.
        HTTPException: 500 if an error occurs during role assignment.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(
        f"User {current_user.email} is attempting to assign role {role} to user with email: {email}", extra=extra
    )

    try:
        check_admin_privileges(current_user)

        user = await get_user_by_email(db, email)
        if not user:
            logger.warning(f"User with email {email} not found", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )

        role_model = await get_role_by_name(db, role)
        if not role_model:
            logger.error(f"Invalid role: {role}", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid role.",
            )

        await update_user_role(db, user, role_model.id)
        logger.info(f"Role {role} assigned to user with email {email}", extra=extra)
        return {"detail": f"User's role updated to {role.value}."}
    except HTTPException as e:
        logger.error(f"Failed to assign role to user with email {email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while assigning role to user with email {email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.post(
    "/change-password/",
    response_model=MessageResponseSchema,
    summary="Change user password",
    description="Changes the password for the authenticated user's account.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - Invalid old password, new password issues, or user not found.",
            "content": {"application/json": {"example": {"detail": "Old password is incorrect."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while changing the password.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def change_password(
    change_password_data: ChangePasswordRequestSchema,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> MessageResponseSchema:
    """
    Changes the password for the authenticated user's account.

    Args:
        change_password_data (ChangePasswordRequestSchema): The data containing old and new passwords.
        db (AsyncSession): The database session dependency.
        current_user (UserModel): The currently authenticated user.

    Returns:
        MessageResponseSchema: Confirmation message of the password change.

    Raises:
        HTTPException: 400 if the user is not found or the old password is incorrect.
        HTTPException: 500 if an error occurs during password change.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"User {current_user.email} is attempting to change their password", extra=extra)

    try:
        user = await get_user_by_email(db, current_user.email)
        if not user:
            logger.error(f"User {current_user.email} not found in database", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User does not exist.",
            )

        await validate_and_change_password(user, change_password_data)
        await update_user_password(db, user, change_password_data.new_password_1)
        logger.info(f"Password changed successfully for user {current_user.email}", extra=extra)
        return MessageResponseSchema(message="Password changed successfully.")
    except HTTPException as e:
        logger.error(f"Failed to change password for user {current_user.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while changing password for user {current_user.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.get(
    "/me/",
    response_model=UserResponseSchema,
    summary="Get current user information",
    description="Retrieves the authenticated user's information, including email, name, and role.",
    status_code=status.HTTP_200_OK,
    responses={
        401: {
            "description": "Unauthorized - User not authenticated.",
            "content": {"application/json": {"example": {"detail": "Not authenticated"}}},
        },
    },
)
async def get_current_user_info(
    current_user: UserModel = Depends(get_current_user),
) -> UserResponseSchema:
    """
    Retrieves the authenticated user's information.

    Args:
        current_user (UserModel): The currently authenticated user.

    Returns:
        UserResponseSchema: The user's information.

    Raises:
        HTTPException: 401 if the user is not authenticated.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Fetching information for user {current_user.email}", extra=extra)
    return prepare_user_response(current_user)


@router.patch(
    "/me/",
    response_model=UserResponseSchema,
    summary="Update current user information",
    description="Updates the authenticated user's information, such as name, phone number, or date of birth.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - Invalid phone number format.",
            "content": {"application/json": {"example": {"detail": "Invalid phone number format."}}},
        },
        401: {
            "description": "Unauthorized - User not authenticated.",
            "content": {"application/json": {"example": {"detail": "Not authenticated"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while updating user information.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def update_current_user_info(
    user_data: UserUpdateRequestSchema,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponseSchema:
    """
    Updates the authenticated user's information.

    Args:
        user_data (UserUpdateRequestSchema): The updated user information.
        current_user (UserModel): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        UserResponseSchema: The updated user information.

    Raises:
        HTTPException: 400 if the phone number format is invalid.
        HTTPException: 401 if the user is not authenticated.
        HTTPException: 500 if an error occurs during the update.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"User {current_user.email} is updating their information", extra=extra)

    try:
        user = await get_user_by_id(db, current_user.id)
        updates = await validate_and_update_user_info(user, user_data)
        updated_user = await update_user_info(db, user, updates)
        logger.info(f"User information updated successfully for user {current_user.email}", extra=extra)
        return prepare_user_response(updated_user)
    except HTTPException as e:
        logger.error(f"Failed to update user information for user {current_user.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error while updating user information for user {current_user.email}: {str(e)}", extra=extra
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.post(
    "/change-email/",
    response_model=MessageResponseSchema,
    summary="Request email change",
    description="Initiates an email change request by sending a confirmation link to the current email.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - Email already in use or invalid email format.",
            "content": {"application/json": {"example": {"detail": "This email is already in use"}}},
        },
        401: {
            "description": "Unauthorized - User not authenticated.",
            "content": {"application/json": {"example": {"detail": "Not authenticated"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the request.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def request_email_change(
    data: UpdateEmailSchema,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> MessageResponseSchema:
    """
    Initiates an email change request for the authenticated user.

    Args:
        data (UpdateEmailSchema): The new email address data.
        db (AsyncSession): The database session dependency.
        current_user (UserModel): The currently authenticated user.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        MessageResponseSchema: Confirmation message of the email change request.

    Raises:
        HTTPException: 400 if the email is already in use or invalid.
        HTTPException: 401 if the user is not authenticated.
        HTTPException: 500 if an error occurs during the request.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"User {current_user.email} is requesting to change email to {data.new_email}", extra=extra)

    try:
        user = await get_user_by_id(db, current_user.id)
        confirm_url = await request_email_change(user, data, db, jwt_manager)

        user.temp_email = data.new_email
        await db.commit()

        await send_email(
            user.email,
            "Confirm Email Change",
            f"To change your email, follow the link: {confirm_url}",
        )
        logger.info(f"Email change confirmation sent to {user.email}", extra=extra)
        return {"message": "Email change request sent"}
    except HTTPException as e:
        logger.error(f"Failed to request email change for user {current_user.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error while requesting email change for user {current_user.email}: {str(e)}", extra=extra
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.get(
    "/confirm-email/",
    response_model=MessageResponseSchema,
    summary="Confirm email change",
    description="Confirms the email change using a token provided in the query parameter.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - Invalid token or mismatched email.",
            "content": {"application/json": {"example": {"detail": "Bad request"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the request.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def confirm_email_change(
    token: str,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> MessageResponseSchema:
    """
    Confirms the email change using a provided token.

    Args:
        token (str): The email change confirmation token.
        db (AsyncSession): The database session dependency.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        MessageResponseSchema: Confirmation message of the email change.

    Raises:
        HTTPException: 400 if the token is invalid or the email doesn't match.
        HTTPException: 500 if an error occurs during the confirmation.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Confirming email change with provided token", extra=extra)

    try:
        payload = jwt_manager.decode_user_interaction_token(token)
        user_id = payload["user_id"]
        new_email = payload["new_email"]

        user = await get_user_by_id(db, user_id)
        if not user:
            logger.error(f"User with ID {user_id} not found", extra=extra)
            raise HTTPException(status_code=400, detail="Bad request")

        await confirm_email_change(user, new_email)
        user.email = new_email
        user.temp_email = None
        await db.commit()
        logger.info(f"Email successfully changed for user ID {user_id} to {new_email}", extra=extra)
        return {"message": "Email successfully changed"}
    except HTTPException as e:
        logger.error(f"Failed to confirm email change: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while confirming email change: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.post(
    "/password-reset/request/",
    response_model=MessageResponseSchema,
    summary="Request password reset",
    description="Initiates a password reset by sending a reset link to the user's email.",
    status_code=status.HTTP_200_OK,
    responses={
        404: {
            "description": "Not Found - User not found.",
            "content": {"application/json": {"example": {"detail": "User not found"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the request.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def request_password_reset(
    data: PasswordResetRequestSchema,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> MessageResponseSchema:
    """
    Initiates a password reset by sending a reset link to the user's email.

    Args:
        data (PasswordResetRequestSchema): The email data for the password reset request.
        db (AsyncSession): The database session dependency.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        MessageResponseSchema: Confirmation message of the password reset request.

    Raises:
        HTTPException: 404 if the user is not found.
        HTTPException: 500 if an error occurs during the request.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Password reset request for email: {data.email}", extra=extra)

    try:
        user = await get_user_by_email(db, data.email)
        if not user:
            logger.warning(f"User with email {data.email} not found", extra=extra)
            raise HTTPException(status_code=404, detail="User not found")

        reset_link = await request_password_reset(user, jwt_manager)
        await send_email(user.email, "Password Reset", f"Click the link to reset your password: {reset_link}")
        logger.info(f"Password reset link sent to {user.email}", extra=extra)
        return {"message": "Password reset link sent"}
    except HTTPException as e:
        logger.error(f"Failed to request password reset for email {data.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while requesting password reset for email {data.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.post(
    "/password-reset/confirm/",
    response_model=MessageResponseSchema,
    summary="Confirm password reset",
    description="Confirms the password reset using a token and sets the new password.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - Invalid or expired token.",
            "content": {"application/json": {"example": {"detail": "Invalid or expired token"}}},
        },
        404: {
            "description": "Not Found - User not found.",
            "content": {"application/json": {"example": {"detail": "User not found"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the request.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def confirm_password_reset(
    data: PasswordResetConfirmSchema,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> MessageResponseSchema:
    """
    Confirms the password reset using a provided token.

    Args:
        data (PasswordResetConfirmSchema): The data containing the token and new password.
        db (AsyncSession): The database session dependency.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        MessageResponseSchema: Confirmation message of the password reset.

    Raises:
        HTTPException: 400 if the token is invalid or expired.
        HTTPException: 404 if the user is not found.
        HTTPException: 500 if an error occurs during the reset.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Confirming password reset with provided token", extra=extra)

    try:
        payload = jwt_manager.decode_user_interaction_token(data.token)
        if not payload:
            logger.error("Invalid or expired token", extra=extra)
            raise HTTPException(status_code=400, detail="Invalid or expired token")

        user = await get_user_by_email(db, payload["sub"])
        if not user:
            logger.warning(f"User with email {payload['sub']} not found", extra=extra)
            raise HTTPException(status_code=404, detail="User not found")

        await confirm_password_reset(user, data)
        await update_user_password(db, user, data.new_password)
        logger.info(f"Password successfully reset for user {user.email}", extra=extra)
        return {"message": "Password successfully reset"}
    except HTTPException as e:
        logger.error(f"Failed to confirm password reset: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while confirming password reset: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.post(
    "/send-invite/",
    response_model=MessageResponseSchema,
    summary="Send user invitation",
    description="Sends an invitation email with a registration link to the specified email.",
    status_code=status.HTTP_200_OK,
    responses={
        401: {
            "description": "Unauthorized - User not authenticated.",
            "content": {"application/json": {"example": {"detail": "Not authenticated"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while sending the invitation.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def send_invite(
    data: SendInviteRequestSchema,
    current_user: UserModel = Depends(get_current_user),
) -> MessageResponseSchema:
    """
    Sends an invitation email with a registration link.

    Args:
        data (SendInviteRequestSchema): The invitation data containing the email and invite link.
        current_user (UserModel): The currently authenticated user.

    Returns:
        MessageResponseSchema: Confirmation message of the invitation delivery.

    Raises:
        HTTPException: 401 if the user is not authenticated.
        HTTPException: 500 if an error occurs while sending the invitation.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"User {current_user.email} is sending an invitation to {data.email}", extra=extra)

    try:
        await send_invite_email(data.email, data.invite)
        logger.info(f"Invitation successfully sent to {data.email}", extra=extra)
        return MessageResponseSchema(message="Invitation was successfully delivered")
    except Exception as e:
        logger.error(f"Failed to send invitation to {data.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.get(
    "/",
    response_model=UserAdminListResponseSchema,
    summary="Get all users",
    description="Retrieves a paginated list of users with optional filtering by role, name, email, or phone.",
    status_code=status.HTTP_200_OK,
    responses={
        403: {
            "description": "Forbidden - Only ADMIN can access this endpoint.",
            "content": {"application/json": {"example": {"detail": "You must be an ADMIN to perform this action."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while fetching users.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def get_all_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    role: Optional[int] = Query(None, description="Filter by role id"),
    first_name: Optional[str] = Query(None, description="Filter by first name"),
    last_name: Optional[str] = Query(None, description="Filter by last name"),
    email: Optional[str] = Query(None, description="Filter by email"),
    phone: Optional[str] = Query(None, description="Filter by phone number"),
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserAdminListResponseSchema:
    """
    Retrieves a paginated list of users with optional filtering.

    Args:
        request (Request): The FastAPI request object for context.
        page (int): Page number for pagination (default: 1).
        page_size (int): Number of items per page (default: 10, max: 100).
        role (Optional[int], optional): Filter by role ID.
        first_name (Optional[str], optional): Filter by first name.
        last_name (Optional[str], optional): Filter by last name.
        email (Optional[str], optional): Filter by email.
        phone (Optional[str], optional): Filter by phone number.
        current_user (UserModel): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        UserAdminListResponseSchema: Paginated list of users with pagination links.

    Raises:
        HTTPException: 403 if the current user is not an admin.
        HTTPException: 500 if an error occurs while fetching users.
    """
    request_id = str(id(request))
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"User {current_user.email} is fetching all users (page={page}, page_size={page_size})", extra=extra)

    try:
        check_admin_privileges(current_user)

        users, total_count, total_pages = await get_filtered_users(
            db, page, page_size, role, first_name, last_name, email, phone
        )
        base_url = str(request.url.remove_query_params("page"))
        response = await prepare_user_list_response(users, total_pages, page, base_url)
        logger.info(f"Returning {len(users)} users, total pages: {total_pages}", extra=extra)
        return response
    except HTTPException as e:
        logger.error(f"Failed to fetch users for user {current_user.email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching users for user {current_user.email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


@router.get(
    "/{email}/",
    response_model=UserResponseSchema,
    summary="Get user by email",
    description="Retrieves user information by their email address.",
    status_code=status.HTTP_200_OK,
    responses={
        403: {
            "description": "Forbidden - Only ADMIN can access this endpoint.",
            "content": {"application/json": {"example": {"detail": "You must be an ADMIN to perform this action."}}},
        },
        404: {
            "description": "Not Found - User not found.",
            "content": {"application/json": {"example": {"detail": "User with email not found"}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while fetching the user.",
            "content": {
                "application/json": {"example": {"detail": "An error occurred while processing the request."}}
            },
        },
    },
)
async def get_user_by_email_end(
    email: str,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponseSchema:
    """
    Retrieves user information by their email address.

    Args:
        email (str): The email address of the user to fetch.
        current_user (UserModel): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        UserResponseSchema: The user's information.

    Raises:
        HTTPException: 403 if the current user is not an admin.
        HTTPException: 404 if the user is not found.
        HTTPException: 500 if an error occurs while fetching the user.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"User {current_user.email} is fetching user with email: {email}", extra=extra)

    try:
        check_admin_privileges(current_user)

        user = await get_user_by_email(db, email)
        if not user:
            logger.warning(f"User with email {email} not found", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with email {email} not found",
            )

        logger.info(f"Successfully fetched user with email {email}", extra=extra)
        return prepare_user_response(user)
    except HTTPException as e:
        logger.error(f"Failed to fetch user with email {email}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching user with email {email}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )
