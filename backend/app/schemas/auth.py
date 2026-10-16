"""Authentication & user schemas (register / login / refresh / me)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.security import validate_password
from app.models.user import UserRole


class UserCreate(BaseModel):
    """Public self-registration payload (farmer / agronomist only)."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128, description="8+ chars, letters + numbers")
    full_name: str = Field(min_length=2, max_length=120)
    role: UserRole = UserRole.FARMER
    phone: str | None = Field(default=None, max_length=32)
    locale: str = Field(default="ar", pattern="^(ar|en)$")

    @field_validator("role", mode="after")
    @classmethod
    def _public_roles_only(cls, value: UserRole) -> UserRole:
        if value.value not in UserRole.public_choices():
            raise ValueError(
                "Public registration is limited to 'farmer' and 'agronomist'. "
                "Admin accounts are seeded through SEED_ADMIN_EMAIL/SEED_ADMIN_PASSWORD."
            )
        return value

    @field_validator("password", mode="after")
    @classmethod
    def _enforce_password_policy(cls, value: str) -> str:
        validate_password(value)  # raises ValueError -> surfaced as 422
        return value

    @field_validator("full_name", mode="after")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("full_name must be at least 2 characters")
        return cleaned


class UserLogin(BaseModel):
    """JSON login payload."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    """Public representation of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    role: UserRole
    locale: str
    phone: str | None = None
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    """Bearer token pair returned by login / refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_expires_in: int = Field(description="Refresh token lifetime in seconds")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=4096)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password", mode="after")
    @classmethod
    def _enforce_password_policy(cls, value: str) -> str:
        validate_password(value)
        return value
