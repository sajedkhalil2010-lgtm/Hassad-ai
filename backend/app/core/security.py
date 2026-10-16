"""JWT issuing/verification and password hashing (Passlib + bcrypt).

Security properties
-------------------
* Tokens are HMAC-SHA256 signed with ``SECRET_KEY`` read from the environment
  at runtime (never hard-coded) and carry ``iss``, ``iat``, ``exp``, ``jti``,
  ``type`` (access|refresh) and the user's role.
* Passwords are hashed with bcrypt (12 rounds by default) through Passlib.
  A defensive fallback to the raw ``bcrypt`` library is kept for platforms
  where the legacy Passlib release cannot introspect the installed bcrypt
  version — hash format and verification stay 100% compatible ($2b$).
* Verification is constant-time (bcrypt's own comparison) and never raises on
  malformed stored hashes, so a corrupted row cannot take the API down.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, status
from passlib.context import CryptContext

from app.core.config import get_settings

logger = logging.getLogger("hassad.security")

# bcrypt truncates input at 72 bytes; longer secrets must be rejected loudly.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# --- Passlib <-> bcrypt compatibility probe -------------------------------
# Newer bcrypt releases (>= 4.1) removed ``bcrypt.__about__``, which legacy
# Passlib 1.7.x introspects at backend load.  Instead of guessing, we run a
# cheap (rounds=4) self-test once per process and then commit to one backend.
# Both produce/verify the same ``$2b$`` hashes, so accounts stay portable.
_PASSLIB_USABLE: bool | None = None


def _passlib_available() -> bool:
    """Return True when Passlib+bcrypt work together on this platform."""
    global _PASSLIB_USABLE
    if _PASSLIB_USABLE is not None:
        return _PASSLIB_USABLE

    try:
        probe_context = _pwd_context.using(bcrypt__rounds=4)
        probe_hash = probe_context.hash("hassad-probe")
        _PASSLIB_USABLE = bool(probe_context.verify("hassad-probe", probe_hash))
    except Exception as exc:  # noqa: BLE001 - environment incompatibility
        logger.warning(
            "Passlib/bcrypt are incompatible on this platform (%s: %s) — "
            "using the raw bcrypt library instead (identical $2b$ hashes).",
            type(exc).__name__,
            exc,
        )
        _PASSLIB_USABLE = False

    if _PASSLIB_USABLE:
        logger.debug("Password hashing backend: passlib(bcrypt)")
    return _PASSLIB_USABLE


# ----------------------------------------------------------------------
# Password policy
# ----------------------------------------------------------------------
def validate_password(password: str) -> None:
    """Raise ``ValueError`` when the password violates the strength policy."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes long (bcrypt limit)"
        )
    if password.isalpha():
        raise ValueError("Password must contain at least one number or symbol")
    if password.isdigit():
        raise ValueError("Password must contain at least one letter")


# ----------------------------------------------------------------------
# Hashing (Passlib / bcrypt)
# ----------------------------------------------------------------------
def _bcrypt_hash(password: str, rounds: int) -> str:
    """Direct bcrypt fallback (identical $2b$ output as Passlib)."""
    import bcrypt

    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=rounds, prefix=b"2b"),
    ).decode("utf-8")


_hash_context: CryptContext | None = None


def _get_hash_context() -> CryptContext:
    """Build (once) the bcrypt CryptContext with the configured cost."""
    global _hash_context
    if _hash_context is None:
        _hash_context = CryptContext(
            schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=get_settings().BCRYPT_ROUNDS
        )
    return _hash_context


def hash_password(password: str) -> str:
    """Hash a plain password with bcrypt (Passlib primary, bcrypt fallback)."""
    rounds = get_settings().BCRYPT_ROUNDS
    if _passlib_available():
        try:
            return _get_hash_context().hash(password)
        except Exception as exc:  # noqa: BLE001 - unexpected runtime failure
            logger.warning("Passlib hash failed (%s); using raw bcrypt", exc)
    return _bcrypt_hash(password, rounds)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time password check; returns False instead of raising."""
    if not plain_password or not hashed_password:
        return False

    if _passlib_available():
        try:
            return bool(_get_hash_context().verify(plain_password, hashed_password))
        except (ValueError, TypeError):
            # Malformed/unknown stored hash -> authentication failure.
            return False
        except Exception as exc:  # noqa: BLE001 - never let crypto take the API down
            logger.warning("Passlib verify failed (%s); using raw bcrypt", exc)

    try:
        import bcrypt

        return bool(
            bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
        )
    except Exception:  # noqa: BLE001 - malformed hash -> simply no match
        return False


def passwords_match(plain_password: str, hashed_password: str) -> bool:
    """Alias kept for readability at call sites."""
    return verify_password(plain_password, hashed_password)


# ----------------------------------------------------------------------
# JWT
# ----------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_token(
    *,
    subject: str,
    role: str,
    token_type: str = "access",
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """Create a signed JWT. Returns ``(token, expires_in_seconds)``."""
    settings = get_settings()
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            if token_type == "access"
            else timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        )

    now = _now()
    expires_at = now + expires_delta
    claims: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "nbf": int(now.timestamp()),
        "iss": settings.JWT_ISSUER,
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        claims.update(extra_claims)

    token = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, int(expires_delta.total_seconds())


def create_access_token(*, subject: str, role: str, extra_claims: dict | None = None) -> tuple[str, int]:
    """Short-lived access token."""
    return create_token(subject=subject, role=role, token_type="access", extra_claims=extra_claims)


def create_refresh_token(*, subject: str, role: str, extra_claims: dict | None = None) -> tuple[str, int]:
    """Long-lived refresh token."""
    return create_token(subject=subject, role=role, token_type="refresh", extra_claims=extra_claims)


def decode_token(token: str, *, expected_type: str | None = "access") -> dict[str, Any]:
    """Decode & validate a JWT.

    Raises:
        HTTPException: 401 for expired, tampered, wrong-issuer or wrong-type tokens.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "type", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if expected_type and payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected a {expected_type} token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def token_subject(payload: dict[str, Any]) -> str:
    """Return the user id stored in ``sub``."""
    return str(payload.get("sub", ""))


def constant_time_equals(left: str, right: str) -> bool:
    """Helper for comparing opaque tokens (e.g. sync cursors)."""
    return hmac.compare_digest(left, right)
