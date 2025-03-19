from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError
from core.security.interfaces import JWTAuthManagerInterface
from schemas.user import UserRegistrationRequestSchema

from exceptions.security import BaseSecurityError

import datetime


def verefy_invite(user_data: UserRegistrationRequestSchema, db: AsyncSession, jwt_manager: JWTAuthManagerInterface) -> dict:
    invite_code = user_data.invite_code
    try:
        decoded_code = jwt_manager.decode_refresh_token(invite_code)
    except BaseSecurityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid invite code {invite_code}.")

    if decoded_code.exp < datetime.datetime.now(datetime.timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite code {user_data.invite_code} has expired.")

    if decoded_code.user_email != user_data.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite code {user_data.invite_code} does not match the provided email.")
    return decoded_code
