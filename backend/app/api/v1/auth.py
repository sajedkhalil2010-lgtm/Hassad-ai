"""Authentication endpoints: register, login, token refresh, profile.

* Passwords are hashed with bcrypt (Passlib) — never stored or logged in clear.
* Login failures always return the same generic message so the endpoint cannot
  be used to enumerate accounts.
* Public registration is limited to ``farmer``/``agronomist``; admin accounts
  are created only through ``SEED_ADMIN_EMAIL``/``SEED_ADMIN_PASSWORD``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import PasswordChange, RefreshRequest, Token, UserCreate, UserLogin, UserRead

router = APIRouter(prefix="/auth", tags=["Authentication"])

_GENERIC_LOGIN_ERROR = "Incorrect email or password"


def _normalise_email(email: str) -> str:
    return str(email).strip().lower()


async def _find_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == _normalise_email(email)))
    return result.scalar_one_or_none()


def _issue_tokens(user: User) -> Token:
    access_token, access_expires = create_access_token(subject=user.id, role=user.role)
    refresh_token, refresh_expires = create_refresh_token(subject=user.id, role=user.role)
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=access_expires,
        refresh_expires_in=refresh_expires,
    )


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: DbSession) -> User:
    """Create a new farmer/agronomist account."""
    if await _find_user_by_email(db, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        email=_normalise_email(payload.email),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role.value,
        phone=payload.phone,
        locale=payload.locale,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
async def login(payload: UserLogin, db: DbSession) -> Token:
    """Exchange email/password for an access + refresh token pair."""
    user = await _find_user_by_email(db, payload.email)

    if user is None or not verify_password(payload.password, user.hashed_password):
        # Identical message for unknown email and wrong password (no enumeration).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact your administrator.",
        )
    return _issue_tokens(user)


@router.post("/refresh", response_model=Token)
async def refresh(payload: RefreshRequest, db: DbSession) -> Token:
    """Exchange a valid refresh token for a fresh token pair."""
    claims = decode_token(payload.refresh_token, expected_type="refresh")
    user = await db.get(User, claims.get("sub", ""))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account no longer exists or is deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_tokens(user)


@router.get("/me", response_model=UserRead)
async def me(current: CurrentUser) -> User:
    """Return the authenticated user's profile."""
    return current


@router.post("/password", response_model=UserRead)
async def change_password(payload: PasswordChange, current: CurrentUser, db: DbSession) -> User:
    """Change the caller's password (requires the current one)."""
    if not verify_password(payload.current_password, current.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    current.hashed_password = hash_password(payload.new_password)
    db.add(current)
    await db.commit()
    await db.refresh(current)
    return current
