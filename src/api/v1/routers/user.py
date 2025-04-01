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

from datetime import timedelta


router = APIRouter(prefix="/users")


@router.post(
    "/invite/",
    response_model=UserInvitationResponseSchema,
    summary="Invite User",
    description="Invite a new user with an email.",
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": "Conflict - User with this email already exists.",
            "content": {"application/json": {"example": {"detail": "A user with this email already exists."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred during user creation.",
            "content": {"application/json": {"example": {"detail": "An error occurred during user creation."}}},
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
    Endpoint for user invitation.

    Invites a new user by sending an email with a unique invitation code.
    If a user with the same email already exists, an HTTP 409 error is raised.
    In case of any unexpected issues during the creation process, an HTTP 500 error is returned.
    """

    if not current_user.has_role(UserRoleEnum.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )

    existing_user = await db.execute(select(UserModel).where(UserModel.email == user_data.email))
    existing_user = existing_user.scalars().first()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"A user with this email {user_data.email} already exists."
        )

    invite_data = {
        "user_email": user_data.email,
        "role_id": user_data.role_id,
    }
    invite_code = jwt_manager.create_invitation_code(invite_data, expires_delta=user_data.expire_days_delta)
    invite_link = f"https://link-to-front?invite={invite_code}"
    return UserInvitationResponseSchema(invite_link=invite_link)


@router.get(
    "/roles/",
    response_model=UserRoleListResponseSchema,
    summary="Get User Roles",
)
async def get_user_roles(db: AsyncSession = Depends(get_db)) -> UserRoleListResponseSchema:
    """
    Endpoint for getting user roles.

    Returns a list of available user roles.
    """
    try:
        result = await db.execute(select(UserRoleModel))
        roles = result.scalars().all()

        if not roles:
            raise HTTPException(status_code=404, detail="No roles found")

        response = [UserRoleResponseSchema.model_validate(role) for role in roles]

        return UserRoleListResponseSchema(roles=response)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred during user roles fetching: {str(e)}")


@router.post(
    "/assign-role/",
    response_model=MessageResponseSchema,
    summary="Assign Role to User",
    description="Assigns a role (USER, MODERATOR, ADMIN) to a user.",
    status_code=status.HTTP_200_OK,
    responses={
        403: {
            "description": "Forbidden - Only ADMIN can assign roles.",
            "content": {"application/json": {"example": {"detail": "You do not have permission to assign roles."}}},
        },
        404: {
            "description": "Not Found - User not found.",
            "content": {"application/json": {"example": {"detail": "User with provided email does not exist."}}},
        },
        400: {
            "description": "Bad Request - Invalid role.",
            "content": {"application/json": {"example": {"detail": "Invalid role provided."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the request.",
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
):
    """
    Endpoint for assigning a role to a user.
    The request must include the user's email and the desired role (USER, MODERATOR, ADMIN).
    Only users with the ADMIN role can assign roles.
    """
    if not current_user.has_role(UserRoleEnum.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )

    result = await db.execute(select(UserModel).where(UserModel.email == email))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    result = await db.execute(select(UserRoleModel).where(UserRoleModel.name == role))
    role = result.scalars().first()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role.",
        )

    user.role_id = role.id
    try:
        db.add(user)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

    return {"detail": f"User's role updated to {role.value}."}


@router.post(
    "/change-password/",
    response_model=MessageResponseSchema,
    summary="Change Password",
    description="Change the password for a user's account.",
    status_code=status.HTTP_200_OK,
)
async def change_password(
    change_password_data: ChangePasswordRequestSchema,
    db: AsyncSession = Depends(get_db),
) -> MessageResponseSchema:
    """
    Endpoint to change password for a user's account.
    """
    result = await db.execute(select(UserModel).where(UserModel.email == change_password_data.email))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not exist.",
        )

    if not pwd_context.verify(change_password_data.old_password, user._hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password is incorrect.",
        )

    try:
        validate_password_strength(change_password_data.new_password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    hashed_new_password = pwd_context.hash(change_password_data.new_password)

    user._hashed_password = hashed_new_password
    await db.commit()

    return MessageResponseSchema(message="Password changed successfully.")


@router.get(
    "/me/",
    response_model=UserResponseSchema,
    summary="Get Current User",
    description="Get the current user's information.",
)
async def get_current_user_info(
    current_user: UserModel = Depends(get_current_user),
) -> UserResponseSchema:
    """
    Endpoint to get current user's information.
    """
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
    summary="Update Current User",
    description="Update the current user's information.",
)
async def update_current_user_info(
    user_data: UserUpdateRequestSchema,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponseSchema:
    """
    Endpoint to update current user's information.
    """
    role = current_user.role.name
    result = await db.execute(select(UserModel).where(UserModel.id == current_user.id))
    current_user = result.scalars().first()
    if user_data.phone_number:
        try:
            validate_phone_number(user_data.phone_number)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    for field, value in user_data.model_dump().items():
        if value:
            setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)

    return UserResponseSchema(
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone_number=current_user.phone_number,
        date_of_birth=current_user.date_of_birth,
        role=role,
    )


@router.post("/change-email")
async def request_email_change(
    data: UpdateEmailSchema,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
):
    new_email = validate_email(data.new_email)
    existing_user = await db.execute(select(UserModel).filter(UserModel.email == new_email))
    if existing_user.scalar():
        raise HTTPException(status_code=400, detail="This email is already in use")

    token_data = {"user_id": current_user.id, "new_email": new_email}
    token = jwt_manager.create_invitation_code(token_data, expires_delta=timedelta(hours=1))

    result = await db.execute(select(UserModel).filter(UserModel.id == current_user.id))
    current_user = result.scalars().first()
    current_user.temp_email = new_email
    await db.commit()

    confirm_url = f"127.0.0.1:8000/api/v1/users/confirm-email?token={token}"
    await send_email(
        current_user.email, "Confirm Email Change", f"To change your email, follow the link: {confirm_url}"
    )

    return {"message": "email change request sent"}


@router.get("/confirm-email")
async def confirm_email_change(
    token: str,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
):
    try:
        payload = jwt_manager.decode_refresh_token(token)
        user_id = payload["user_id"]
        new_email = payload["new_email"]

        result = await db.execute(select(UserModel).filter(UserModel.id == user_id))
        user = result.scalars().first()
        if not user or user.temp_email != new_email:
            print(user.temp_email, new_email, user)
            raise HTTPException(status_code=400, detail="Bad request")

        user.email = new_email
        user.temp_email = None
        await db.commit()

        return {"message": "Email successfully changed"}

    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/password-reset/request/")
async def request_password_reset(
    data: PasswordResetRequestSchema,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
):
    result = await db.execute(select(UserModel).filter(UserModel.email == data.email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    token = jwt_manager.create_invitation_code({"sub": user.email}, expires_delta=timedelta(minutes=15))
    reset_link = f"127.0.0.1:8000/api/v1/password-reset/confirm?token={token}"

    await send_email(user.email, "Password Reset", f"Click the link to reset your password: {reset_link}")
    return {"message": "Password reset link sent"}


@router.post("/password-reset/confirm/")
async def confirm_password_reset(
    data: PasswordResetConfirmSchema,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
):
    payload = jwt_manager.decode_refresh_token(data.token)
    if not payload:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    result = await db.execute(select(UserModel).filter(UserModel.email == payload["sub"]))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password = data.new_password
    await db.commit()
    return {"message": "Password successfully reset"}


@router.post("/send-invite/", response_model=MessageResponseSchema)
async def send_invite(
    data: SendInvieteRequestSchema,
    current_user: UserModel = Depends(get_current_user),
) -> MessageResponseSchema:
    await send_email(data.email, "Invitation", f"Click the link to complete registration: {data.invite}")
    return MessageResponseSchema(message="Invitation was succesfuly delivered")


@router.get("/", response_model=UserAdminListResponseSchema)
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
    if not current_user.has_role(UserRoleEnum.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )

    query = select(UserModel).options(selectinload(UserModel.role))

    if role:
        query = query.filter(UserModel.role_id == role)
    if first_name:
        query = query.filter(UserModel.first_name.ilike(f"%{first_name}%"))
    if last_name:
        query = query.filter(UserModel.last_name.ilike(f"%{last_name}%"))
    if email:
        query = query.filter(UserModel.email.ilike(f"%{email}%"))
    if phone:
        query = query.filter(UserModel.phone.ilike(f"%{phone}%"))

    total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
    total_pages = (total_count + page_size - 1) // page_size

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    users = result.scalars().all()

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


@router.get("/{email}", response_model=UserResponseSchema)
async def get_user_by_email(
    email: str,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponseSchema:
    if not current_user.has_role(UserRoleEnum.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an ADMIN to perform this action.",
        )

    result = await db.execute(select(UserModel).options(selectinload(UserModel.role)).where(UserModel.email == email))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email {email} not found",
        )

    return UserResponseSchema(
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone_number=user.phone_number,
        date_of_birth=user.date_of_birth,
        role=user.role.name,
    )
