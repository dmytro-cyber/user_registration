from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Optional


class JWTAuthManagerInterface(ABC):
    """
    Interface for JWT Authentication Manager.
    Defines methods for creating, decoding, and verifying JWT tokens.
    """

    @abstractmethod
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a new access token.
        """
        pass

    @abstractmethod
    def create_user_interaction_token(self, data: dict) -> str:
        """
        Create a new user interaction token.
        """
        pass

    @abstractmethod
    def decode_access_token(self, token: str) -> dict:
        """
        Decode and validate an access token.
        """
        pass

    @abstractmethod
    def decode_refresh_token(self, token: str) -> dict:
        """
        Decode and validate a refresh token.
        """
        pass

    @abstractmethod
    def decode_user_interaction_token(self, code: str) -> dict:
        """
        Decode and validate a user interaction token.
        """
        pass

    @abstractmethod
    def verify_refresh_token_or_raise(self, token: str) -> None:
        """
        Verify a refresh token or raise an error if invalid.
        """
        pass

    @abstractmethod
    def verify_access_token_or_raise(self, token: str) -> None:
        """
        Verify an access token or raise an error if invalid.
        """
        pass

    @abstractmethod
    def verify_user_interaction_token_or_raise(self, token: str) -> None:
        """
        Verify an user interaction token or raise an error if invalid.
        """
        pass
