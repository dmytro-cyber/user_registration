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


class UserRegistrationResponseSchema(BaseModel):
    id: int
    email: EmailStr

    model_config = ConfigDict(from_attributes=True)


class UserInvitationResponseSchema(BaseModel):
    invite_code: str


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
