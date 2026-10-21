"""Hassad AI authentication — email/password accounts in a local SQLite file.

Public surface::

    from auth import AuthStore, build_auth_router

Everything here is stdlib: ``hashlib.scrypt`` for password hashing and
``sqlite3`` for storage. No OAuth provider is wired up — Google Sign-In was
deliberately dropped, because it cannot function without a Client ID from
Google Cloud Console and this deployment has none.

The store is injected into the router rather than imported by it, so tests
point auth at a throwaway database instead of the real one.
"""

from __future__ import annotations

from .passwords import hash_password, new_session_token, token_digest, verify_password
from .routes import LoginRequest, LoginThrottle, RegisterRequest, build_auth_router
from .store import PUBLIC_USER_FIELDS, AuthStore, DuplicateEmail

__all__ = [
    "AuthStore",
    "DuplicateEmail",
    "LoginRequest",
    "LoginThrottle",
    "PUBLIC_USER_FIELDS",
    "RegisterRequest",
    "build_auth_router",
    "hash_password",
    "new_session_token",
    "token_digest",
    "verify_password",
]
