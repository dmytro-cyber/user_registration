from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from models.validators import user as validators

import datetime

class BaseEmailPasswordSchema(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        return value.lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        return validators.validate_password_strength(value)


class ChangePasswordRequestSchema(BaseModel):
    email: str
    old_password: str
    new_password: str


class UserRegistrationRequestSchema(BaseEmailPasswordSchema):
    invite_code: str
    first_name: str
    last_name: str
    phone_number: str
    date_of_birth: datetime.date


class UserLoginRequestSchema(BaseEmailPasswordSchema):
    pass


class UserLoginResponseSchema(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserRegistrationResponseSchema(BaseModel):
    id: int
    email: EmailStr

    model_config = ConfigDict(from_attributes=True)


class TokenRefreshRequestSchema(BaseModel):
    refresh_token: str


class TokenRefreshResponseSchema(BaseModel):
    access_token: str
    token_type: str = "bearer"
