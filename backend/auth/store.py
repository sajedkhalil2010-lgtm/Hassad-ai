"""SQLite-backed user and session storage.

Stdlib ``sqlite3`` only — no ORM. The whole schema is nine columns across two
tables; an ORM would be more code than the thing it wraps, and would add the
one dependency this project has worked to avoid.

One connection per operation rather than a shared one. A ``sqlite3.Connection
`` is bound to the thread that created it, while FastAPI runs sync routes
across a threadpool — so handing one connection around would either raise or
need ``check_same_thread=False`` plus a hand-rolled lock over every query.
Opening per operation keeps each call self-contained, and at this scale the
cost is noise.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .passwords import hash_password, new_session_token, token_digest, verify_password

logger = logging.getLogger("hassad.auth")

#: Exactly the columns ever returned to a caller. ``password_hash`` is
#: deliberately absent — nothing outside this module should see it, and
#: building responses from this tuple makes that structural rather than a
#: matter of remembering each time.
PUBLIC_USER_FIELDS = (
    "id",
    "email",
    "name",
    "is_admin",
    "is_active",
    "created_at",
    "last_login_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL,
    name          TEXT,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    last_login_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    token_hash TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
"""

#: Placeholder hashed once per store and used to burn the same CPU on an
#: unknown email as on a real one.
_TIMING_DUMMY_SECRET = "timing-equalizer-not-a-real-password"


class DuplicateEmail(Exception):
    """Raised when registration collides with an address already in the db."""

    def __init__(self, email: str) -> None:
        super().__init__(email)
        self.email = email


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    """Render a UTC timestamp in a single lexicographically-sortable form.

    ISO-8601 from one fixed timezone compares correctly as plain text, which
    lets SQLite filter on ``expires_at > ?`` without a date function.
    """
    return value.astimezone(timezone.utc).isoformat()


class AuthStore:
    """Accounts and sessions in a SQLite file."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        scrypt_n: int = 2**15,
        scrypt_r: int = 8,
        scrypt_p: int = 1,
        session_ttl_days: int = 30,
        admin_emails: Sequence[str] = (),
    ) -> None:
        self._path = Path(database_path)
        self._n, self._r, self._p = scrypt_n, scrypt_r, scrypt_p
        self._ttl_days = session_ttl_days
        #: Emails that become admin. Seeded at registration *and* re-checked
        #: on login, so adding an address to `.env` promotes an existing
        #: account on their next sign-in — no manual database surgery.
        self._admin = {str(e).strip().lower() for e in admin_emails if str(e).strip()}
        self._dummy_hash: str | None = None
        self._dummy_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: Any) -> "AuthStore":
        """Build a store from a ``Settings`` instance."""
        return cls(
            settings.database_file,
            scrypt_n=settings.scrypt_n,
            scrypt_r=settings.scrypt_r,
            scrypt_p=settings.scrypt_p,
            session_ttl_days=settings.session_ttl_days,
            admin_emails=settings.admin_email_list,
        )

    def init_schema(self) -> None:
        """Create the file and tables if they do not exist. Idempotent."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        logger.info("Auth database ready at %s", self._path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @property
    def path(self) -> Path:
        return self._path

    # -- password helpers --------------------------------------------------

    @property
    def _dummy(self) -> str:
        """A real hash used to equalise work on an unknown email.

        Without this, "no such account" returns in microseconds while "wrong
        password" pays for a full scrypt — a measurable difference that lets
        an attacker enumerate which addresses are registered.
        """
        if self._dummy_hash is None:
            with self._dummy_lock:
                if self._dummy_hash is None:
                    self._dummy_hash = hash_password(
                        _TIMING_DUMMY_SECRET, n=self._n, r=self._r, p=self._p
                    )
        return self._dummy_hash

    # -- users -------------------------------------------------------------

    @staticmethod
    def _to_public(row: sqlite3.Row) -> dict[str, Any]:
        user: dict[str, Any] = {field: row[field] for field in PUBLIC_USER_FIELDS}
        user["is_admin"] = bool(user["is_admin"])
        user["is_active"] = bool(user["is_active"])
        return user

    def create_user(self, email: str, password: str, *, name: str | None = None) -> dict[str, Any]:
        """Insert an account. Raises :class:`DuplicateEmail` if it exists."""
        normalized = str(email).strip().lower()
        password_hash = hash_password(password, n=self._n, r=self._r, p=self._p)
        is_admin = 1 if normalized in self._admin else 0
        now = _iso(_utcnow())

        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO users (email, name, password_hash, is_admin, is_active, created_at)"
                    " VALUES (?, ?, ?, ?, 1, ?)",
                    (normalized, name, password_hash, is_admin, now),
                )
            except sqlite3.IntegrityError as exc:
                # The UNIQUE index is the authority on collisions, not an
                # earlier SELECT: a check-then-insert would race here.
                raise DuplicateEmail(normalized) from exc
            user_id = int(cursor.lastrowid)

        return {
            "id": user_id,
            "email": normalized,
            "name": name,
            "is_admin": bool(is_admin),
            "is_active": True,
            "created_at": now,
            "last_login_at": None,
        }

    def verify_login(self, email: str, password: str) -> dict[str, Any] | None:
        """Check credentials. Returns the public user, or ``None``.

        The same ``None`` covers every failure mode — unknown address, wrong
        password, deactivated account — so callers cannot accidentally tell
        the three apart in what they show the user.
        """
        normalized = str(email).strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (normalized,)
            ).fetchone()

        if row is None:
            verify_password(password, self._dummy)
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        if not row["is_active"]:
            return None

        # Promote on login if `.env` now names this address as an admin.
        if normalized in self._admin and not row["is_admin"]:
            self.set_admin(normalized, True)
            return {**self._to_public(row), "is_admin": True}
        return self._to_public(row)

    def record_login(self, user_id: int) -> None:
        """Stamp ``last_login_at`` so the admin list shows real activity."""
        with self._connect() as conn:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(_utcnow()), user_id))

    def set_admin(self, email: str, is_admin: bool) -> bool:
        """Toggle the admin flag. Returns whether a row was updated."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET is_admin = ? WHERE email = ? COLLATE NOCASE",
                (1 if is_admin else 0, str(email).strip().lower()),
            )
        return cursor.rowcount > 0

    def list_users(self) -> list[dict[str, Any]]:
        """Every account plus how many live sessions it currently holds."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT u.*,"
                "  (SELECT COUNT(*) FROM sessions s"
                "     WHERE s.user_id = u.id AND s.revoked_at IS NULL AND s.expires_at > ?)"
                "    AS active_sessions"
                "  FROM users u ORDER BY u.created_at, u.id",
                (_iso(_utcnow()),),
            ).fetchall()
        return [{**self._to_public(row), "active_sessions": int(row["active_sessions"])} for row in rows]

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    # -- sessions ----------------------------------------------------------

    def create_session(self, user_id: int) -> str:
        """Issue a token for a user. The plaintext is returned exactly once."""
        token = new_session_token()
        now = _utcnow()
        expires = now + timedelta(days=self._ttl_days)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (user_id, token_hash, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (user_id, token_digest(token), _iso(now), _iso(expires)),
            )
        return token

    def resolve_session(self, token: str) -> dict[str, Any] | None:
        """Look up the user behind a token, enforcing revocation and expiry."""
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.* FROM sessions s"
                "  JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash = ?"
                "   AND s.revoked_at IS NULL"
                "   AND s.expires_at > ?"
                "   AND u.is_active = 1",
                (token_digest(token), _iso(_utcnow())),
            ).fetchone()
        return self._to_public(row) if row else None

    def revoke_session(self, token: str) -> bool:
        """Sign a token out. Idempotent; unknown tokens are not an error."""
        if not token:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET revoked_at = ?"
                " WHERE token_hash = ? AND revoked_at IS NULL",
                (_iso(_utcnow()), token_digest(token)),
            )
        return cursor.rowcount > 0
