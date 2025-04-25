import logging
from datetime import timedelta
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from core.security.passwords import pwd_context
from models.validators.user import validate_password_strength, validate_email, validate_phone_number
from models.user import UserModel, UserRoleEnum, UserRoleModel
from core.security.interfaces import JWTAuthManagerInterface
from services.email import send_email
from schemas.user import (
    UserInvitationRequestSchema,
    UserRoleResponseSchema,
    UserRoleListResponseSchema,
    ChangePasswordRequestSchema,
    UserUpdateRequestSchema,
    UpdateEmailSchema,
    PasswordResetConfirmSchema,
    SendInviteRequestSchema,
    UserResponseSchema,
    UserAdminListResponseSchema,
)
from typing import List, Optional, Dict

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def check_admin_privileges(current_user: UserModel) -> None:
    """
    Check if the current user has ADMIN privileges.

    Args:
        current_user (UserModel): The current authenticated user.

    Raises:
        HTTPException: If the user does not have ADMIN role.
    """
    if not current_user.has_role(UserRoleEnum.ADMIN):
        logger.warning(f"User {current_user.email} does not have ADMIN role")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )


async def generate_invite_link(user_data: UserInvitationRequestSchema, jwt_manager: JWTAuthManagerInterface) -> str:
    """
    Generate an invitation link for a new user.

    Args:
        user_data (UserInvitationRequestSchema): The invitation data.
        jwt_manager (JWTAuthManagerInterface): The JWT manager to create the token.

    Returns:
        str: The generated invitation link.
    """
    invite_data = {
        "user_email": user_data.email,
        "role_id": user_data.role_id,
    }
    invite_code = jwt_manager.create_user_interaction_token(
        invite_data, expires_delta=timedelta(days=user_data.expire_days_delta)
    )
    invite_link = f"https://link-to-front?invite={invite_code}"
    logger.info(f"Invitation link generated for {user_data.email}: {invite_link}")
    return invite_link


async def validate_and_change_password(user: UserModel, change_password_data: ChangePasswordRequestSchema) -> None:
    """
    Validate and change the user's password.

    Args:
        user (UserModel): The user to update.
        change_password_data (ChangePasswordRequestSchema): The password change data.

    Raises:
        HTTPException: If validation fails.
    """
    if not pwd_context.verify(change_password_data.old_password, user._hashed_password):
        logger.warning(f"User {user.email} provided incorrect old password")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password is incorrect.",
        )

    if change_password_data.new_password_1 == change_password_data.old_password:
        logger.warning(f"User {user.email} attempted to set new password same as old")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password cannot be the same as the old password.",
        )

    if change_password_data.new_password_1 != change_password_data.new_password_2:
        logger.warning(f"User {user.email} provided mismatched new passwords")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New passwords do not match.",
        )

    try:
        validate_password_strength(change_password_data.new_password_1)
        logger.debug(f"New password for user {user.email} meets strength requirements")
    except ValueError as e:
        logger.error(f"New password validation failed for user {user.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


async def validate_and_update_user_info(user: UserModel, user_data: UserUpdateRequestSchema) -> dict:
    """
    Validate and prepare user info updates.

    Args:
        user (UserModel): The user to update.
        user_data (UserUpdateRequestSchema): The update data.

    Returns:
        dict: The validated updates.

    Raises:
        HTTPException: If validation fails.
    """
    if user_data.phone_number:
        try:
            validate_phone_number(user_data.phone_number)
            logger.debug(f"Phone number {user_data.phone_number} is valid for user {user.email}")
        except ValueError as exc:
            logger.error(f"Invalid phone number for user {user.email}: {str(exc)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return user_data.model_dump(exclude_unset=True)


async def request_email_change(
    user: UserModel, data: UpdateEmailSchema, db: AsyncSession, jwt_manager: JWTAuthManagerInterface
) -> str:
    """
    Initiate an email change request.

    Args:
        user (UserModel): The user requesting the email change.
        data (UpdateEmailSchema): The email change data.
        db (AsyncSession): The database session.
        jwt_manager (JWTAuthManagerInterface): The JWT manager to create the token.

    Returns:
        str: The confirmation URL.

    Raises:
        HTTPException: If validation fails or email is already in use.
    """
    try:
        new_email = validate_email(data.new_email)
        logger.debug(f"New email {new_email} is valid")
    except ValueError as e:
        logger.error(f"Invalid email format for {data.new_email}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    from crud.user import get_user_by_email  # Імпортуємо тут, щоб уникнути циклічного імпорту

    existing_user = await get_user_by_email(db, new_email)
    if existing_user:
        logger.warning(f"Email {new_email} is already in use")
        raise HTTPException(status_code=400, detail="This email is already in use")

    token_data = {"user_id": user.id, "new_email": new_email}
    token = jwt_manager.create_user_interaction_token(token_data, expires_delta=timedelta(hours=1))
    confirm_url = f"127.0.0.1:8000/api/v1/users/confirm-email?token={token}"
    return confirm_url


async def confirm_email_change(user: UserModel, new_email: str) -> None:
    """
    Confirm and apply the email change.

    Args:
        user (UserModel): The user to update.
        new_email (str): The new email to set.

    Raises:
        HTTPException: If the temporary email does not match.
    """
    if user.temp_email != new_email:
        logger.error(f"Email change failed: user_id={user.id}, temp_email={user.temp_email}, new_email={new_email}")
        raise HTTPException(status_code=400, detail="Bad request")


async def request_password_reset(user: UserModel, jwt_manager: JWTAuthManagerInterface) -> str:
    """
    Generate a password reset link.

    Args:
        user (UserModel): The user requesting the password reset.
        jwt_manager (JWTAuthManagerInterface): The JWT manager to create the token.

    Returns:
        str: The reset link.
    """
    token = jwt_manager.create_user_interaction_token({"sub": user.email}, expires_delta=timedelta(minutes=15))
    reset_link = f"https://localhost:5173/set-new-password?token={token}"
    return reset_link


async def confirm_password_reset(user: UserModel, data: PasswordResetConfirmSchema) -> None:
    """
    Validate and confirm the password reset.

    Args:
        user (UserModel): The user to update.
        data (PasswordResetConfirmSchema): The password reset data.

    Raises:
        HTTPException: If validation fails.
    """
    try:
        validate_password_strength(data.new_password)
        logger.debug(f"New password for user {user.email} meets strength requirements")
    except ValueError as e:
        logger.error(f"New password validation failed for user {user.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


async def send_invite_email(email: str, invite_link: str) -> None:
    """
    Send an invitation email.

    Args:
        email (str): The recipient's email.
        invite_link (str): The invitation link.

    Raises:
        HTTPException: If sending the email fails.
    """
    try:
        await send_email(email, "Invitation", f"Click the link to complete registration: {invite_link}")
        logger.info(f"Invitation successfully sent to {email}")
    except Exception as e:
        logger.error(f"Error sending invitation to {email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )


async def prepare_user_list_response(
    users: List[UserModel], total_pages: int, page: int, base_url: str
) -> UserAdminListResponseSchema:
    """
    Prepare the response for a paginated list of users.

    Args:
        users (List[UserModel]): The list of users.
        total_pages (int): Total number of pages.
        page (int): Current page.
        base_url (str): Base URL for pagination links.

    Returns:
        UserAdminListResponseSchema: The response schema.
    """
    page_links = {i: f"{base_url}&page={i}" for i in range(1, total_pages + 1) if i != page}
    return UserAdminListResponseSchema(
        users=[
            UserResponseSchema(
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                phone_number=user.phone_number,
                date_of_birth=user.date_of_birth,
                role=user.role.name,
            )
            for user in users
        ],
        page_links=page_links,
    )


def prepare_user_response(user: UserModel) -> UserResponseSchema:
    """
    Prepare a single user response.

    Args:
        user (UserModel): The user to prepare.

    Returns:
        UserResponseSchema: The response schema.
    """
    return UserResponseSchema(
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone_number=user.phone_number,
        date_of_birth=user.date_of_birth,
        role=user.role.name,
    )


def prepare_roles_response(roles: List[UserRoleModel]) -> UserRoleListResponseSchema:
    """
    Prepare the response for a list of roles.

    Args:
        roles (List[UserRoleModel]): The list of roles.

    Returns:
        UserRoleListResponseSchema: The response schema.
    """
    return UserRoleListResponseSchema(roles=[UserRoleResponseSchema.model_validate(role) for role in roles])
