from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from typing import List

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
    old_password: str
    new_password_1: str
    new_password_2: str


class PasswordResetRequestSchema(BaseModel):
    email: EmailStr


class PasswordResetConfirmSchema(BaseModel):
    token: str
    new_password: str


class UserRegistrationRequestSchema(BaseModel):
    email: EmailStr | None
    password: str
    invite_code: str
    first_name: str
    last_name: str
    phone_number: str
    date_of_birth: datetime.date


class UserLoginRequestSchema(BaseEmailPasswordSchema):
    pass


class UserRegistrationResponseSchema(BaseModel):
    id: int
    email: EmailStr

    model_config = ConfigDict(from_attributes=True)


class UserInvitationResponseSchema(BaseModel):
    invite_link: str


class UserInvitationRequestSchema(BaseModel):
    email: EmailStr
    expire_days_delta: int | None
    role_id: int


class UserRoleResponseSchema(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class UserRoleListResponseSchema(BaseModel):
    roles: list[UserRoleResponseSchema]


class UserResponseSchema(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    phone_number: str
    date_of_birth: datetime.date
    role: str

    model_config = ConfigDict(from_attributes=True)


class UserUpdateRequestSchema(BaseModel):
    first_name: str | None
    last_name: str | None
    phone_number: str | None
    date_of_birth: datetime.date | None


class UpdateEmailSchema(BaseModel):
    new_email: EmailStr


class SendInvieteRequestSchema(BaseModel):
    email: str
    invite: str


class UserAdminListResponseSchema(BaseModel):
    users: List[UserResponseSchema]
    page_links: dict
