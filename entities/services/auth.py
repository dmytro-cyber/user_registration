# services/auth.py
import datetime
import logging

from fastapi import HTTPException, status

from core.dependencies import get_jwt_auth_manager
from core.security.interfaces import JWTAuthManagerInterface
from exceptions.security import BaseSecurityError
from schemas.user import UserRegistrationRequestSchema

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def verify_invite(user_data: UserRegistrationRequestSchema, jwt_manager: JWTAuthManagerInterface) -> dict:
    """
    Verify the invitation code and return the decoded payload.

    Args:
        user_data (UserRegistrationRequestSchema): The user registration data containing the invite code.
        jwt_manager (JWTAuthManagerInterface): The JWT manager to decode the token.

    Returns:
        dict: The decoded payload from the invite code.

    Raises:
        HTTPException: If the invite code is invalid, expired, or does not match the provided email.
    """
    invite_code = user_data.invite_code
    logger.debug(f"Verifying invite code: {invite_code}")

    try:
        decoded_code = jwt_manager.decode_user_interaction_token(invite_code)
    except BaseSecurityError:
        logger.error(f"Invalid invite code: {invite_code}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid invite code {invite_code}.")

    if decoded_code.get("exp") < datetime.datetime.now(datetime.timezone.utc).timestamp():
        logger.warning(f"Invite code {invite_code} has expired")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invite code {user_data.invite_code} has expired."
        )

    if user_data.email and decoded_code.get("user_email") != user_data.email:
        logger.warning(f"Invite code {invite_code} does not match email {user_data.email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite code {user_data.invite_code} does not match the provided email.",
        )

    logger.info(f"Invite code {invite_code} successfully verified")
    return decoded_code
