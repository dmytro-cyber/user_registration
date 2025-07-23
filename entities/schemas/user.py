import datetime
from datetime import date
from typing import List

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from models.validators import user as validators


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
    email: EmailStr | None = Field(default=None, exclude=True)
    password: str
    invite_code: str
    first_name: str
    last_name: str
    phone_number: str
    date_of_birth: date

    @field_validator("date_of_birth")
    def parse_date_of_birth(cls, value):
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%d").date()
        return value

    @field_validator("date_of_birth")
    def validate_date_of_birth(cls, value):
        if value > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return value


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
    date_of_birth: date
    role: str

    model_config = ConfigDict(from_attributes=True)


class UserUpdateRequestSchema(BaseModel):
    first_name: str | None
    last_name: str | None
    phone_number: str | None
    date_of_birth: date | None


class UpdateEmailSchema(BaseModel):
    new_email: EmailStr


class SendInviteRequestSchema(BaseModel):
    email: str
    invite: str


class UserAdminListResponseSchema(BaseModel):
    users: List[UserResponseSchema]
    page_links: dict
