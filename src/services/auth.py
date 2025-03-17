from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from core.security.interfaces import JWTAuthManagerInterface
from schemas.user import UserRegistrationRequestSchema
from models.user import InviteModel
from db.session import get_db
import datetime
from exceptions.security import BaseSecurityError
from core.security import get_jwt_auth_manager


async def verefy_invite(user_data: UserRegistrationRequestSchema, db: AsyncSession, jwt_manager: JWTAuthManagerInterface) -> dict:
    invite = await db.execute(select(InviteModel).where(InviteModel.code == user_data.invite_code))
    invite = invite.scalars().first()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invite code {user_data.invite_code} not found.")
    if invite.expires_at < datetime.datetime.now():
        db.delete(invite)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite code {user_data.invite_code} has expired.")
    try:
        decoded_code = jwt_manager.decode_refresh_token(invite.code)
        user_email = decoded_code.get("user_email")
    except BaseSecurityError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )
    if user_email != user_data.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite code {user_data.invite_code} does not match the provided email.")
    return decoded_code
