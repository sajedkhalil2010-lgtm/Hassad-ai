"""Reusable FastAPI dependencies: database, settings, authentication, RBAC."""

from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User, UserRole

# Bearer tokens are accepted from the Authorization header.  ``auto_error=False``
# lets us return a consistent 401 (instead of the framework's 403) and control
# the message for missing/invalid credentials.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="HassadBearerToken")

DbSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    """Resolve the authenticated user from a valid access token."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized()
    if (credentials.scheme or "").lower() != "bearer":
        raise _unauthorized("Authorization scheme must be Bearer")

    payload = decode_token(credentials.credentials, expected_type="access")

    user = await db.get(User, payload.get("sub", ""))
    if user is None:
        raise _unauthorized("Account no longer exists")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact your administrator.",
        )
    return user


async def get_current_active_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Alias kept for clarity at call sites (active users only)."""
    return user


CurrentUser = Annotated[User, Depends(get_current_active_user)]


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Role factory: ``Depends(require_roles(UserRole.AGRONOMIST, ...))``.

    Admins always pass; everybody else must match one of ``roles``.
    """
    allowed = {role.value for role in roles}

    async def checker(current: CurrentUser) -> User:
        if current.role == UserRole.ADMIN.value:
            return current
        if current.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of the roles: {', '.join(sorted(allowed))}",
            )
        return current

    return checker
