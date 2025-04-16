from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Query
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from schemas.user import (
    UserInvitationRequestSchema,
    UserInvitationResponseSchema,
    UserRoleResponseSchema,
    UserRoleListResponseSchema,
    UserResponseSchema,
    UserUpdateRequestSchema,
    ChangePasswordRequestSchema,
    UpdateEmailSchema,
    PasswordResetRequestSchema,
    PasswordResetConfirmSchema,
    SendInvieteRequestSchema,
    UserAdminListResponseSchema,
)
from sqlalchemy.orm import selectinload
from schemas.message import MessageResponseSchema
from core.security import get_jwt_auth_manager
from core.security.passwords import pwd_context
from services.email import send_email
from models.validators.user import validate_password_strength, validate_email, validate_phone_number
from models.user import UserModel, UserRoleModel, UserRoleEnum
from core.security.interfaces import JWTAuthManagerInterface
from core.dependencies import get_current_user
from db.session import get_db
from jose import jwt
import logging
from datetime import timedelta

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

    - Checks if the current user has ADMIN role; raises HTTP 403 if not.
    - Verifies if a user with the provided email already exists; raises HTTP 409 if true.
    - Generates an invitation code with the specified expiration time.
    - Returns the invitation link for the user.
    - Raises HTTP 500 if an error occurs during the process.
    """
    logger.info(f"User {current_user.email} is attempting to invite a new user with email: {user_data.email}")

    if not current_user.has_role(UserRoleEnum.ADMIN):
        logger.warning(f"User {current_user.email} does not have ADMIN role to invite users")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )
    
    logger.info(f"User {current_user.email} is inviting a new user with email: {user_data.email}")

    existing_user = await db.execute(select(UserModel).where(UserModel.email == user_data.email))
    existing_user = existing_user.scalars().first()
    if existing_user is not None:
        logger.warning(f"User with email {user_data.email} already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with this email {user_data.email} already exists.",
        )

    invite_data = {
        "user_email": user_data.email,
        "role_id": user_data.role_id,
    }
    invite_code = jwt_manager.create_user_interaction_token(invite_data, expires_delta=timedelta(user_data.expire_days_delta))
    invite_link = f"https://link-to-front?invite={invite_code}"
    logger.info(f"Invitation link generated for {user_data.email}: {invite_link}")
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

    - Fetches all roles from the database.
    - Raises HTTP 404 if no roles are found.
    - Raises HTTP 500 if an error occurs during fetching.
    """
    logger.info("Fetching all user roles")

    try:
        result = await db.execute(select(UserRoleModel))
        roles = result.scalars().all()

        if not roles:
            logger.warning("No roles found in the database")
            raise HTTPException(status_code=404, detail="No roles found")

        response = [UserRoleResponseSchema.model_validate(role) for role in roles]
        logger.info(f"Successfully fetched {len(roles)} roles")
        return UserRoleListResponseSchema(roles=response)

    except Exception as e:
        logger.error(f"Error fetching user roles: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An error occurred during user roles fetching: {str(e)}")


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

    - Verifies if the current user has ADMIN role; raises HTTP 403 if not.
    - Checks if the user with the provided email exists; raises HTTP 404 if not.
    - Validates the provided role; raises HTTP 400 if invalid.
    - Updates the user's role in the database.
    - Raises HTTP 500 if an error occurs during the process.
    """
    logger.info(f"User {current_user.email} is attempting to assign role {role} to user with email: {email}")

    if not current_user.has_role(UserRoleEnum.ADMIN):
        logger.warning(f"User {current_user.email} does not have ADMIN role to assign roles")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )

    result = await db.execute(select(UserModel).where(UserModel.email == email))
    user = result.scalars().first()

    if not user:
        logger.warning(f"User with email {email} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    result = await db.execute(select(UserRoleModel).where(UserRoleModel.name == role))
    role_model = result.scalars().first()

    if not role_model:
        logger.error(f"Invalid role: {role}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role.",
        )

    user.role_id = role_model.id
    try:
        db.add(user)
        await db.commit()
        logger.info(f"Role {role} successfully assigned to user {email}")
    except Exception as e:
        await db.rollback()
        logger.error(f"Error assigning role to user {email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

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

    - Verifies the old password; raises HTTP 400 if incorrect.
    - Ensures the new password is different from the old one; raises HTTP 400 if not.
    - Confirms that the two new password entries match; raises HTTP 400 if they don't.
    - Validates the new password strength; raises HTTP 400 if it doesn't meet requirements.
    - Updates the user's password in the database.
    - Raises HTTP 500 if an error occurs during the process.
    """
    logger.info(f"User {current_user.email} is attempting to change their password")

    result = await db.execute(select(UserModel).where(UserModel.email == current_user.email))
    user = result.scalars().first()

    if not user:
        logger.error(f"User {current_user.email} not found in database")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not exist.",
        )

    if not pwd_context.verify(change_password_data.old_password, user._hashed_password):
        logger.warning(f"User {current_user.email} provided incorrect old password")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password is incorrect.",
        )

    if change_password_data.new_password_1 == change_password_data.old_password:
        logger.warning(f"User {current_user.email} attempted to set new password same as old")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password cannot be the same as the old password.",
        )

    if change_password_data.new_password_1 != change_password_data.new_password_2:
        logger.warning(f"User {current_user.email} provided mismatched new passwords")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New passwords do not match.",
        )

    try:
        validate_password_strength(change_password_data.new_password_1)
        logger.debug(f"New password for user {current_user.email} meets strength requirements")
    except ValueError as e:
        logger.error(f"New password validation failed for user {current_user.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    user.password = change_password_data.new_password_1
    try:
        await db.commit()
        logger.info(f"Password successfully changed for user {current_user.email}")
    except Exception as e:
        logger.error(f"Error changing password for user {current_user.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

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

    - Returns the user's email, first name, last name, phone number, date of birth, and role.
    - Requires the user to be authenticated.
    """
    logger.info(f"Fetching information for user {current_user.email}")

    return UserResponseSchema(
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone_number=current_user.phone_number,
        date_of_birth=current_user.date_of_birth,
        role=current_user.role.name,
    )


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

    - Validates the phone number if provided; raises HTTP 400 if invalid.
    - Updates the user's fields (e.g., first name, last name, phone number) with the provided data.
    - Returns the updated user information.
    - Raises HTTP 500 if an error occurs during the update process.
    """
    logger.info(f"User {current_user.email} is updating their information")

    role = current_user.role.name
    result = await db.execute(select(UserModel).where(UserModel.id == current_user.id))
    current_user = result.scalars().first()

    if user_data.phone_number:
        try:
            validate_phone_number(user_data.phone_number)
            logger.debug(f"Phone number {user_data.phone_number} is valid for user {current_user.email}")
        except ValueError as exc:
            logger.error(f"Invalid phone number for user {current_user.email}: {str(exc)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    for field, value in user_data.model_dump().items():
        if value:
            setattr(current_user, field, value)

    try:
        await db.commit()
        await db.refresh(current_user)
        logger.info(f"User {current_user.email} information updated successfully")
    except Exception as e:
        logger.error(f"Error updating user {current_user.email} information: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

    return UserResponseSchema(
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone_number=current_user.phone_number,
        date_of_birth=current_user.date_of_birth,
        role=role,
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

    - Validates the new email format and checks if it's already in use; raises HTTP 400 if invalid or taken.
    - Generates a confirmation token with a 1-hour expiration.
    - Sends a confirmation link to the user's current email.
    - Stores the new email temporarily in the database.
    - Raises HTTP 500 if an error occurs during the process.
    """
    logger.info(f"User {current_user.email} is requesting to change email to {data.new_email}")

    try:
        new_email = validate_email(data.new_email)
        logger.debug(f"New email {new_email} is valid")
    except ValueError as e:
        logger.error(f"Invalid email format for {data.new_email}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    existing_user = await db.execute(select(UserModel).filter(UserModel.email == new_email))
    if existing_user.scalar():
        logger.warning(f"Email {new_email} is already in use")
        raise HTTPException(status_code=400, detail="This email is already in use")

    token_data = {"user_id": current_user.id, "new_email": new_email}
    token = jwt_manager.create_user_interaction_token(token_data, expires_delta=timedelta(hours=1))

    result = await db.execute(select(UserModel).filter(UserModel.id == current_user.id))
    current_user = result.scalars().first()
    current_user.temp_email = new_email
    try:
        await db.commit()
        logger.debug(f"Temporary email {new_email} set for user {current_user.email}")
    except Exception as e:
        logger.error(f"Error setting temporary email for user {current_user.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

    confirm_url = f"127.0.0.1:8000/api/v1/users/confirm-email?token={token}"
    await send_email(
        current_user.email,
        "Confirm Email Change",
        f"To change your email, follow the link: {confirm_url}",
    )
    logger.info(f"Email change confirmation sent to {current_user.email}")
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

    - Decodes the token to extract user ID and new email.
    - Verifies that the temporary email matches the new email; raises HTTP 400 if not.
    - Updates the user's email in the database and clears the temporary email.
    - Raises HTTP 400 if the token is invalid or the user is not found.
    - Raises HTTP 500 if an error occurs during the process.
    """
    logger.info("Confirming email change with provided token")

    try:
        payload = jwt_manager.decode_user_interaction_token(token)
        user_id = payload["user_id"]
        new_email = payload["new_email"]
        logger.debug(f"Decoded token: user_id={user_id}, new_email={new_email}")

        result = await db.execute(select(UserModel).filter(UserModel.id == user_id))
        user = result.scalars().first()
        if not user or user.temp_email != new_email:
            logger.error(
                f"Email change failed: user_id={user_id}, temp_email={user.temp_email}, new_email={new_email}"
            )
            raise HTTPException(status_code=400, detail="Bad request")

        user.email = new_email
        user.temp_email = None
        try:
            await db.commit()
            logger.info(f"Email successfully changed to {new_email} for user_id {user_id}")
        except Exception as e:
            logger.error(f"Error updating email for user_id {user_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while processing the request.",
            )

        return {"message": "Email successfully changed"}

    except Exception as exc:
        logger.error(f"Error confirming email change: {str(exc)}")
        raise HTTPException(status_code=400, detail=str(exc))


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

    - Checks if the user with the provided email exists; raises HTTP 404 if not.
    - Generates a password reset token with a 15-minute expiration.
    - Sends a reset link to the user's email.
    - Raises HTTP 500 if an error occurs during the process.
    """
    logger.info(f"Password reset request for email: {data.email}")

    result = await db.execute(select(UserModel).filter(UserModel.email == data.email))
    user = result.scalars().first()
    if not user:
        logger.warning(f"User with email {data.email} not found")
        raise HTTPException(status_code=404, detail="User not found")

    token = jwt_manager.create_user_interaction_token({"sub": user.email}, expires_delta=timedelta(minutes=15))
    reset_link = f"https://localhost:5173/set-new-password?token={token}"

    try:
        await send_email(user.email, "Password Reset", f"Click the link to reset your password: {reset_link}")
        logger.info(f"Password reset link sent to {user.email}")
    except Exception as e:
        logger.error(f"Error sending password reset email to {user.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

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

    - Decodes the token to extract the user's email; raises HTTP 400 if invalid or expired.
    - Verifies the user exists; raises HTTP 404 if not.
    - Updates the user's password with the new one.
    - Raises HTTP 500 if an error occurs during the process.
    """
    logger.info("Confirming password reset with provided token")

    try:
        payload = jwt_manager.decode_user_interaction_token(data.token)
        if not payload:
            logger.error("Invalid or expired token")
            raise HTTPException(status_code=400, detail="Invalid or expired token")
        logger.debug(f"Decoded token: {payload}")

        result = await db.execute(select(UserModel).filter(UserModel.email == payload["sub"]))
        user = result.scalars().first()
        if not user:
            logger.warning(f"User with email {payload['sub']} not found")
            raise HTTPException(status_code=404, detail="User not found")

        user.password = data.new_password
        try:
            await db.commit()
            logger.info(f"Password successfully reset for user {user.email}")
        except Exception as e:
            logger.error(f"Error resetting password for user {user.email}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while processing the request.",
            )

        return {"message": "Password successfully reset"}

    except Exception as e:
        logger.error(f"Error confirming password reset: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


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
    data: SendInvieteRequestSchema,
    current_user: UserModel = Depends(get_current_user),
) -> MessageResponseSchema:
    """
    Sends an invitation email with a registration link.

    - Sends the invitation link to the specified email address.
    - Requires the user to be authenticated.
    - Raises HTTP 500 if an error occurs during email sending.
    """
    logger.info(f"User {current_user.email} is sending an invitation to {data.email}")

    try:
        await send_email(data.email, "Invitation", f"Click the link to complete registration: {data.invite}")
        logger.info(f"Invitation successfully sent to {data.email}")
    except Exception as e:
        logger.error(f"Error sending invitation to {data.email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

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

    - Verifies if the current user has ADMIN role; raises HTTP 403 if not.
    - Supports filtering by role ID, first name, last name, email, and phone number.
    - Returns a paginated list of users with pagination links.
    - Raises HTTP 500 if an error occurs during fetching.
    """
    logger.info(f"User {current_user.email} is fetching all users (page={page}, page_size={page_size})")

    if not current_user.has_role(UserRoleEnum.ADMIN):
        logger.warning(f"User {current_user.email} does not have ADMIN role to fetch users")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )

    query = select(UserModel).options(selectinload(UserModel.role))

    if role:
        query = query.filter(UserModel.role_id == role)
        logger.debug(f"Filtering by role_id: {role}")
    if first_name:
        query = query.filter(UserModel.first_name.ilike(f"%{first_name}%"))
        logger.debug(f"Filtering by first_name: {first_name}")
    if last_name:
        query = query.filter(UserModel.last_name.ilike(f"%{last_name}%"))
        logger.debug(f"Filtering by last_name: {last_name}")
    if email:
        query = query.filter(UserModel.email.ilike(f"%{email}%"))
        logger.debug(f"Filtering by email: {email}")
    if phone:
        query = query.filter(UserModel.phone.ilike(f"%{phone}%"))
        logger.debug(f"Filtering by phone: {phone}")

    try:
        total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
        total_pages = (total_count + page_size - 1) // page_size

        result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
        users = result.scalars().all()
        logger.info(f"Fetched {len(users)} users (total: {total_count})")

        base_url = str(request.url.remove_query_params("page"))
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

    except Exception as e:
        logger.error(f"Error fetching users: {str(e)}")
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
async def get_user_by_email(
    email: str,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponseSchema:
    """
    Retrieves user information by their email address.

    - Verifies if the current user has ADMIN role; raises HTTP 403 if not.
    - Fetches the user by email; raises HTTP 404 if not found.
    - Returns the user's information including email, name, and role.
    - Raises HTTP 500 if an error occurs during fetching.
    """
    logger.info(f"User {current_user.email} is fetching user with email: {email}")

    if not current_user.has_role(UserRoleEnum.ADMIN):
        logger.warning(f"User {current_user.email} does not have ADMIN role to fetch user by email")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )

    try:
        result = await db.execute(
            select(UserModel).options(selectinload(UserModel.role)).where(UserModel.email == email)
        )
        user = result.scalars().first()

        if not user:
            logger.warning(f"User with email {email} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with email {email} not found",
            )

        logger.info(f"Successfully fetched user {email}")
        return UserResponseSchema(
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            phone_number=user.phone_number,
            date_of_birth=user.date_of_birth,
            role=user.role.name,
        )

    except Exception as e:
        logger.error(f"Error fetching user {email}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )
