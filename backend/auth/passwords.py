"""Password hashing and session-token helpers — stdlib only.

Deliberately free of third-party crypto: ``hashlib.scrypt`` is memory-hard,
is on the OWASP approved list, ships inside CPython and has no build step —
which matters on a Windows laptop, where compiling a C extension such as
``bcrypt`` is a common and hard-to-diagnose stumble.

Two properties here are load-bearing:

* **Self-describing hashes.** Every stored string carries its own work
  factor (``scrypt$N$r$p$salt$hash``), so an operator can raise the cost for
  future logins without invalidating a single existing account.
* **Only a digest of a session token is stored.** A database file that walks
  off the machine cannot be replayed against the API; only the holder of the
  plaintext token can authenticate.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

#: Algorithm tag written at the front of every hash.
ALGORITHM = "scrypt"

#: Salt length in bytes. 128 bits is the standard floor for a password salt.
_SALT_BYTES = 16

#: Derived-key length in bytes.
_KEY_BYTES = 32

#: Session tokens are 32 random bytes rendered URL-safe — 256 bits, far past
#: anything worth guessing, and still short enough to sit in a header.
_TOKEN_BYTES = 32


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _maxmem(n: int, r: int) -> int:
    """Peak memory for these scrypt parameters, with headroom.

    scrypt needs roughly ``128 * N * r`` bytes, so the default cost (N=2**15,
    r=8) wants ~32 MiB. OpenSSL's own ceiling is also 32 MiB, so without an
    explicit ``maxmem`` the setting would land exactly on a limit that varies
    by platform — the same parameters would work on one machine and raise on
    another. Setting it here makes the cost a property of the configuration
    rather than of whichever OpenSSL happened to be linked.
    """
    return 128 * n * r * 2 + (1024 * 1024)


def hash_password(password: str, *, n: int, r: int, p: int) -> str:
    """Hash a password into a self-describing ``scrypt$N$r$p$salt$hash`` string.

    Raises ``ValueError`` on an empty password; callers are expected to apply
    a length policy *before* reaching this layer, so a short password is a
    validation decision, not a hashing one.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_KEY_BYTES,
        maxmem=_maxmem(n, r),
    )
    return f"{ALGORITHM}${n}${r}${p}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash.

    Returns ``False`` rather than raising on a malformed record: a corrupted
    or truncated database row must fail that one login, not take the request
    down with a 500 that tells an attacker the schema is readable.
    """
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != ALGORITHM:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = _b64d(parts[4])
        expected = _b64d(parts[5])
    except (ValueError, TypeError):
        return False
    # Reject nonsensical parameters instead of handing them to OpenSSL.
    if n < 2 or (n & (n - 1)) != 0 or r < 1 or p < 1 or not expected:
        return False
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=_maxmem(n, r),
        )
    except ValueError:
        return False
    return secrets.compare_digest(actual, expected)


def new_session_token() -> str:
    """Mint a fresh opaque bearer token.

    Opaque rather than a JWT on purpose: this app has exactly one resource
    server, so there is nothing to gain from a self-contained token and
    something to lose — a JWT cannot be revoked without a denylist, which is
    just a session table with extra steps.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(token: str) -> str:
    """SHA-256 of a token, hex-encoded — the form that gets persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
