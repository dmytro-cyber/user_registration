from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from schemas.user import (
    UserInvitationRequestSchema,
    UserInvitationResponseSchema,
    UserRoleResponseSchema,
    UserRoleListResponseSchema
)
from schemas.message import MessageResponseSchema
from core.security import get_jwt_auth_manager
from models.user import UserModel, UserRoleModel
from core.security.interfaces import JWTAuthManagerInterface
from core.dependencies import get_current_user
from db.session import get_db

import datetime


router = APIRouter()


@router.post(
    "/invite/",
    response_model=UserInvitationResponseSchema,
    summary="Invite User",
    description="Invite a new user with an email.",
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": "Conflict - User with this email already exists.",
            "content": {
                "application/json": {"example": {"detail": "A user with this email already exists."}}
            },
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
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager)
) -> UserInvitationResponseSchema:
    """
    Endpoint for user invitation.

    Invites a new user by sending an email with a unique invitation code.
    If a user with the same email already exists, an HTTP 409 error is raised.
    In case of any unexpected issues during the creation process, an HTTP 500 error is returned.
    """
    
    # if current_user.role.name != "admin":
    #     raise HTTPException(
    #         status_code=status.HTTP_401_UNAUTHORIZED,
    #         detail="Only admin users can invite new users."
    #     )
    
    existing_user = await db.execute(select(UserModel).where(UserModel.email == user_data.email))
    existing_user = existing_user.scalars().first()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with this email {user_data.email} already exists."
        )

    invite_data = {
        "user_email": user_data.email,
        "role_id": user_data.role_id,
    }
    invite_code = jwt_manager.create_invitation_code(invite_data, expires_delta=user_data.expire_days_delta)
    return UserInvitationResponseSchema(invite_code=invite_code)


@router.get(
    "/roles/",
    response_model=UserRoleListResponseSchema,
    summary="Get User Roles",
)
async def get_user_roles(
    db: AsyncSession = Depends(get_db)
) -> UserRoleListResponseSchema:
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
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred during user roles fetching: {str(e)}"
        )
