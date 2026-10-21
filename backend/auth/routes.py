"""Registration, login and session routes.

Built by a factory rather than a module-level ``router`` so each app instance
gets its own throttle state. Tests construct several apps in one process; a
single shared limiter would let one test's failed logins lock out another.

Responses reuse the ``{ok, error:{kind, message, retryable}}`` envelope that
``/api/v1/analyze`` and ``/api/v1/chat`` already emit, so a client renders
every failure the same way and never has to special-case auth. The one
addition is ``409`` for a duplicate registration, which is genuinely a
conflict rather than bad input.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .store import AuthStore, DuplicateEmail

logger = logging.getLogger("hassad.auth")

#: A deliberate sanity check, not RFC 5322. Full email validation needs a
#: parser (and a dependency); over-strict hand-rolled regexes are worse than
#: under-strict ones because they reject addresses that genuinely work. The
#: database is the final arbiter of uniqueness anyway.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Cap on password length. scrypt cost scales with input size, so an
#: unbounded field is a cheap way to make the server burn CPU — 128
#: characters is far past anything a person types and far short of abuse.
_MAX_PASSWORD_CHARS = 128


class RegisterRequest(BaseModel):
    """Body for ``POST /api/register``."""

    email: str = Field(min_length=3, max_length=254)
    #: `min_length=1` only — the real floor lives in settings so an operator
    #: can change policy without editing code, and it is enforced in the
    #: handler where the message can name the actual number.
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_CHARS)
    name: str | None = Field(default=None, max_length=80)


class LoginRequest(BaseModel):
    """Body for ``POST /api/login``."""

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_CHARS)


def _fail(status: int, kind: str, message: str, *, retryable: bool = False) -> JSONResponse:
    """One error shape for every auth failure."""
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"kind": kind, "message": message, "retryable": retryable}},
    )


class LoginThrottle:
    """Failed-login limiter, keyed by ``(email, ip)``.

    Deliberately process-local and in-memory: this app runs as a single
    uvicorn process, so a shared or persistent store would be machinery
    without a consumer. It is a speed bump against online guessing — the
    defence against an *offline* attack on the database file is the scrypt
    cost, which this does not replace.

    ``clock`` is injectable so tests can advance time without sleeping.
    """

    def __init__(self, max_attempts: int, window_seconds: int, *, clock=time.monotonic) -> None:
        self._max_attempts = max(1, int(max_attempts))
        self._window = max(1, int(window_seconds))
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float, failures: deque[float]) -> None:
        cutoff = now - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(key, None)

    def retry_after(self, key: str) -> int:
        """Seconds until this key may try again; ``0`` when not locked."""
        now = self._clock()
        with self._lock:
            failures = self._failures.get(key)
            if not failures:
                return 0
            self._prune(key, now, failures)
            failures = self._failures.get(key)
            if not failures or len(failures) < self._max_attempts:
                return 0
            # `max(1, ...)` rather than `+ 1`: rounding up with ceil would do
            # the same, and it avoids telling the caller to wait 61s for a
            # 60s window.
            return max(1, int(self._window - (now - failures[0])))

    def fail(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._failures.setdefault(key, deque()).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def build_auth_router(store: AuthStore, settings: Any) -> APIRouter:
    """Create the auth routes bound to one store and one settings object."""
    router = APIRouter(prefix="", tags=["auth"])
    throttle = LoginThrottle(settings.login_max_attempts, settings.login_lockout_seconds)
    min_password = settings.min_password_length

    def _bearer(request: Request) -> str | None:
        """Extract a token from ``Authorization: Bearer <token>``, if any."""
        scheme, _, value = request.headers.get("Authorization", "").partition(" ")
        if scheme.strip().lower() != "bearer":
            return None
        return value.strip() or None

    def _current_user(request: Request) -> dict[str, Any] | None:
        token = _bearer(request)
        return store.resolve_session(token) if token else None

    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # -- registration ------------------------------------------------------

    @router.post("/api/register")
    def register(payload: RegisterRequest) -> JSONResponse:
        """Create an account and return a session token.

        ``409`` rather than ``422`` for a repeat address: the input was
        well-formed, it simply already exists.
        """
        email = payload.email.strip().lower()
        if not _EMAIL_RE.match(email):
            return _fail(422, "input", "That does not look like a valid email address.")

        if len(payload.password) < min_password:
            return _fail(
                422,
                "input",
                f"The password must be at least {min_password} characters long.",
            )

        name = payload.name.strip() if payload.name else None
        try:
            user = store.create_user(email, payload.password, name=name or None)
        except DuplicateEmail:
            # Same generic framing as a failed login: registration must not
            # become an oracle for which addresses are already registered.
            logger.info("Register rejected — address already in use: %s", email)
            return _fail(409, "input", "An account already exists for that email address.")

        token = store.create_session(user["id"])
        logger.info("Registered new user %s (admin=%s)", email, user["is_admin"])
        return JSONResponse(status_code=201, content={"ok": True, "token": token, "user": user})

    # -- login -------------------------------------------------------------

    @router.post("/api/login")
    def login(payload: LoginRequest, request: Request) -> JSONResponse:
        """Verify credentials and return a fresh session token."""
        email = payload.email.strip().lower()
        key = f"{email}|{_client_ip(request)}"

        wait = throttle.retry_after(key)
        if wait:
            # `kind="quota"` reuses the existing 429 classification: it is the
            # closest match in the shared taxonomy, it maps to the right
            # status, and it is already treated as "back off, then retry" by
            # every client that speaks this envelope.
            logger.info("Login throttled for %s (%ss)", email, wait)
            return _fail(429, "quota", f"Too many attempts. Please try again in {wait} seconds.", retryable=True)

        user = store.verify_login(email, payload.password)
        if user is None:
            throttle.fail(key)
            logger.info("Failed login for %s", email)
            return _fail(401, "auth", "Incorrect email or password.")

        throttle.clear(key)
        store.record_login(user["id"])
        token = store.create_session(user["id"])
        logger.info("Login succeeded for %s", email)
        return JSONResponse(status_code=200, content={"ok": True, "token": token, "user": user})

    # -- sessions ----------------------------------------------------------

    @router.post("/api/logout")
    def logout(request: Request) -> JSONResponse:
        """Revoke the calling token. Always reports success.

        Logout is idempotent on purpose: a user who signs out twice, or whose
        token already expired, still wants the same reassuring answer.
        """
        token = _bearer(request)
        if token:
            store.revoke_session(token)
        return JSONResponse(status_code=200, content={"ok": True})

    @router.get("/api/me")
    def me(request: Request) -> JSONResponse:
        """Report who the calling token belongs to, if anyone.

        This is what the page calls on load to decide between showing the
        sign-in form and the signed-in state.
        """
        user = _current_user(request)
        if user is None:
            return _fail(401, "auth", "You are not signed in.")
        return JSONResponse(status_code=200, content={"ok": True, "user": user})

    # -- admin -------------------------------------------------------------

    @router.get("/api/admin/users")
    def admin_users(request: Request) -> JSONResponse:
        """List every registered account — admin only.

        Built entirely from ``PUBLIC_USER_FIELDS``, so a password hash cannot
        reach this response even if a future column is added: new columns are
        simply not selected.
        """
        user = _current_user(request)
        if user is None:
            return _fail(401, "auth", "You are not signed in.")
        if not user["is_admin"]:
            logger.warning("Non-admin %s attempted /api/admin/users", user["email"])
            return _fail(403, "auth", "Administrator access is required.")

        users = store.list_users()
        logger.info("Admin %s listed %d user(s)", user["email"], len(users))
        return JSONResponse(status_code=200, content={"ok": True, "users": users, "total": len(users)})

    return router
