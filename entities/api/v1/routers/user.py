from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.user import (
    UserInvitationRequestSchema,
    UserInvitationResponseSchema,
    UserRoleListResponseSchema,
    UserResponseSchema,
    UserUpdateRequestSchema,
    ChangePasswordRequestSchema,
    UpdateEmailSchema,
    PasswordResetRequestSchema,
    PasswordResetConfirmSchema,
    SendInviteRequestSchema,
    UserAdminListResponseSchema,
)
from schemas.message import MessageResponseSchema
from core.dependencies import get_jwt_auth_manager
from models.user import UserModel, UserRoleEnum
from core.security.interfaces import JWTAuthManagerInterface
from core.dependencies import get_current_user
from db.session import get_db
import logging
from crud.user import (
    get_user_by_email,
    get_all_roles,
    get_role_by_name,
    update_user_role,
    update_user_password,
    update_user_info,
    get_filtered_users,
    get_user_by_id,
)
from services.user import (
    check_admin_privileges,
    generate_invite_link,
    validate_and_change_password,
    validate_and_update_user_info,
    request_email_change,
    confirm_email_change,
    request_password_reset,
    confirm_password_reset,
    send_invite_email,
    prepare_user_list_response,
    prepare_user_response,
    prepare_roles_response,
)
from services.email import send_email

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    """
    logger.info(f"User {current_user.email} is attempting to invite a new user with email: {user_data.email}")

    check_admin_privileges(current_user)

    existing_user = await get_user_by_email(db, user_data.email)
    if existing_user:
        logger.warning(f"User with email {user_data.email} already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with this email {user_data.email} already exists.",
        )

    invite_link = await generate_invite_link(user_data, jwt_manager)
    return UserInvitationResponseSchema(invite_link=invite_link)


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
    """
    logger.info("Fetching all user roles")

    roles = await get_all_roles(db)
    if not roles:
        logger.warning("No roles found in the database")
        raise HTTPException(status_code=404, detail="No roles found")

    return prepare_roles_response(roles)


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
    """
    logger.info(f"User {current_user.email} is attempting to assign role {role} to user with email: {email}")

    check_admin_privileges(current_user)

    user = await get_user_by_email(db, email)
    if not user:
        logger.warning(f"User with email {email} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    role_model = await get_role_by_name(db, role)
    if not role_model:
        logger.error(f"Invalid role: {role}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role.",
        )

    await update_user_role(db, user, role_model.id)
    return {"detail": f"User's role updated to {role.value}."}


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
    """
    logger.info(f"User {current_user.email} is attempting to change their password")

    user = await get_user_by_email(db, current_user.email)
    if not user:
        logger.error(f"User {current_user.email} not found in database")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not exist.",
        )

    await validate_and_change_password(user, change_password_data)
    await update_user_password(db, user, change_password_data.new_password_1)
    return MessageResponseSchema(message="Password changed successfully.")


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
    """
    logger.info(f"Fetching information for user {current_user.email}")
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
    """
    logger.info(f"User {current_user.email} is updating their information")

    user = await get_user_by_id(db, current_user.id)
    updates = await validate_and_update_user_info(user, user_data)
    updated_user = await update_user_info(db, user, updates)
    return prepare_user_response(updated_user)


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
    """
    logger.info(f"User {current_user.email} is requesting to change email to {data.new_email}")

    user = await get_user_by_id(db, current_user.id)
    confirm_url = await request_email_change(user, data, db, jwt_manager)

    user.temp_email = data.new_email
    await db.commit()

    await send_email(
        user.email,
        "Confirm Email Change",
        f"To change your email, follow the link: {confirm_url}",
    )
    logger.info(f"Email change confirmation sent to {user.email}")
    return {"message": "Email change request sent"}


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
    """
    logger.info("Confirming email change with provided token")

    payload = jwt_manager.decode_user_interaction_token(token)
    user_id = payload["user_id"]
    new_email = payload["new_email"]

    user = await get_user_by_id(db, user_id)
    if not user:
        logger.error(f"User with ID {user_id} not found")
        raise HTTPException(status_code=400, detail="Bad request")

    await confirm_email_change(user, new_email)
    user.email = new_email
    user.temp_email = None
    await db.commit()
    return {"message": "Email successfully changed"}


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
    """
    logger.info(f"Password reset request for email: {data.email}")

    user = await get_user_by_email(db, data.email)
    if not user:
        logger.warning(f"User with email {data.email} not found")
        raise HTTPException(status_code=404, detail="User not found")

    reset_link = await request_password_reset(user, jwt_manager)
    await send_email(user.email, "Password Reset", f"Click the link to reset your password: {reset_link}")
    return {"message": "Password reset link sent"}


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
    """
    logger.info("Confirming password reset with provided token")

    payload = jwt_manager.decode_user_interaction_token(data.token)
    if not payload:
        logger.error("Invalid or expired token")
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user = await get_user_by_email(db, payload["sub"])
    if not user:
        logger.warning(f"User with email {payload['sub']} not found")
        raise HTTPException(status_code=404, detail="User not found")

    await confirm_password_reset(user, data)
    await update_user_password(db, user, data.new_password)
    return {"message": "Password successfully reset"}


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
    """
    logger.info(f"User {current_user.email} is sending an invitation to {data.email}")

    await send_invite_email(data.email, data.invite)
    return MessageResponseSchema(message="Invitation was successfully delivered")


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
    """
    logger.info(f"User {current_user.email} is fetching all users (page={page}, page_size={page_size})")

    check_admin_privileges(current_user)

    users, total_count, total_pages = await get_filtered_users(
        db, page, page_size, role, first_name, last_name, email, phone
    )
    base_url = str(request.url.remove_query_params("page"))
    return await prepare_user_list_response(users, total_pages, page, base_url)


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
    """
    logger.info(f"User {current_user.email} is fetching user with email: {email}")

    check_admin_privileges(current_user)

    user = await get_user_by_email(db, email)
    if not user:
        logger.warning(f"User with email {email} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email {email} not found",
        )

    return prepare_user_response(user)
