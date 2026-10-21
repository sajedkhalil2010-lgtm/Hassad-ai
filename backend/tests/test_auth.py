"""Offline tests for the account database and auth routes.

Hermetic: every test runs against a throwaway SQLite file in a temp
directory, and builds its own ``Settings`` with ``_env_file=None`` so it can
never read the developer's ``.env`` or touch the real account database.

scrypt is configured down to a trivial cost here. The parameters are
self-describing and read back out of each stored hash, so lowering them for
tests exercises the same code path the production cost does — only faster.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def _settings(tmp: Path, **overrides):
    from agents.config import Settings

    defaults = dict(
        _env_file=None,
        groq_api_key=None,
        database_path=str(tmp / "test.db"),
        scrypt_n=2**4,
        scrypt_r=1,
        scrypt_p=1,
        login_max_attempts=3,
        login_lockout_seconds=60,
        admin_emails="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _store(settings):
    from auth import AuthStore

    store = AuthStore.from_settings(settings)
    store.init_schema()
    return store


def section(title):
    print(f"\n=== {title} ===")


def main() -> int:
    from fastapi.testclient import TestClient

    from agents.config import Settings
    from auth import hash_password, verify_password
    from auth.store import DuplicateEmail
    from main import create_app

    tmp = Path(tempfile.mkdtemp(prefix="hassad-auth-"))

    # ------------------------------------------------------------------
    section("A. password hashing")
    h = hash_password("correct horse battery staple", n=2**4, r=1, p=1)
    parts = h.split("$")
    check("hash is self-describing (alg$n$r$p$salt$hash)", len(parts) == 6 and parts[0] == "scrypt", h[:40])
    check("work factor is stored verbatim", parts[1] == "16")
    check("salt is present", len(parts[4]) > 8)
    check("verify accepts the right password", verify_password("correct horse battery staple", h))
    check("verify rejects a wrong password", not verify_password("wrong", h))
    check("verify rejects an empty password", not verify_password("", h))
    check("verify rejects a whitespace-only password", not verify_password("   ", h))

    h2 = hash_password("same password", n=2**4, r=1, p=1)
    check("salts differ between hashes", h.split("$")[4] != h2.split("$")[4])

    section("B. corrupt / hostile stored hashes")
    check("malformed string -> False, never raises", not verify_password("x", "not-a-hash"))
    check("wrong algorithm -> False", not verify_password("x", "bcrypt$16$8$1$AAAA$BBBB"))
    check("too few fields -> False", not verify_password("x", "scrypt$16$8$1$AAAA"))
    check("non-numeric cost -> False", not verify_password("x", "scrypt$a$b$c$AAAA$BBBB"))
    check("non-power-of-two N -> False", not verify_password("x", "scrypt$15$8$1$AAAA$BBBB"))
    check("non-base64 salt -> False", not verify_password("x", "scrypt$16$8$1$!!!!$BBBB"))
    check("empty password against empty stored -> False", not verify_password("", ""))

    # ------------------------------------------------------------------
    section("C. settings resolve the database path and admin list")
    s = _settings(tmp, database_path="accounts.db")
    check("relative path resolves inside backend/", s.database_file.name == "accounts.db", str(s.database_file))
    check("parent is the backend package", s.database_file.parent.name == "backend")
    s2 = _settings(tmp, admin_emails=" Boss@X.com , ,boss@x.com, other@y.com ")
    check("admin emails are lowercased, trimmed, de-duplicated",
          s2.admin_email_list == ["boss@x.com", "other@y.com"], str(s2.admin_email_list))

    # ------------------------------------------------------------------
    section("D. store: accounts")
    settings = _settings(tmp)
    store = _store(settings)
    check("database file was created", store.path.is_file(), str(store.path))

    user = store.create_user("Farmer@Example.com", "password123", name="Amine")
    check("email is normalized to lowercase", user["email"] == "farmer@example.com", user["email"])
    check("new user is not an admin by default", user["is_admin"] is False)
    check("new user is active", user["is_active"] is True)

    try:
        store.create_user("farmer@example.com", "password123")
        check("case-insensitive duplicate is refused", False, "second insert succeeded")
    except DuplicateEmail:
        check("case-insensitive duplicate is refused", True)

    check("count is 1 after the duplicate was refused", store.count_users() == 1, str(store.count_users()))

    section("E. store: login")
    check("correct password -> user", store.verify_login("farmer@example.com", "password123") is not None)
    check("wrong password -> None", store.verify_login("farmer@example.com", "WRONG") is None)
    check("unknown email -> None", store.verify_login("nobody@example.com", "password123") is None)
    check("uppercase email still logs in", store.verify_login("FARMER@EXAMPLE.COM", "password123") is not None)

    section("F. store: admin seeding and promotion")
    admin_settings = _settings(tmp, admin_emails="boss@example.com")
    admin_store = _store(admin_settings)
    boss = admin_store.create_user("boss@example.com", "password123")
    check("email in ADMIN_EMAILS becomes admin on registration", boss["is_admin"] is True)
    other = admin_store.create_user("worker@example.com", "password123")
    check("other users stay non-admin", other["is_admin"] is False)
    check("set_admin(True) works", admin_store.set_admin("worker@example.com", True))
    check("promoted user is now admin", admin_store.verify_login("worker@example.com", "password123")["is_admin"] is True)
    check("set_admin on unknown email returns False", admin_store.set_admin("ghost@example.com", True) is False)
    check("set_admin(False) works", admin_store.set_admin("worker@example.com", False))

    # Editing ADMIN_EMAILS promotes an existing account on next login.
    promoted_settings = _settings(tmp, admin_emails="worker@example.com")
    promoted_store = _store(promoted_settings)
    check("already-admin user stays admin on re-login",
          promoted_store.verify_login("worker@example.com", "password123")["is_admin"] is True)

    section("G. store: sessions")
    token = store.create_session(user["id"])
    check("token is opaque and long enough", len(token) >= 40, f"{len(token)} chars")
    check("token is not the email or password", "password123" not in token)
    resolved = store.resolve_session(token)
    check("resolve returns the user", resolved is not None and resolved["email"] == "farmer@example.com")
    check("tampered token is rejected", store.resolve_session(token[:-3] + "AAA") is None)
    check("empty token is rejected", store.resolve_session("") is None)
    check("unrelated token is rejected", store.resolve_session("not-a-real-token") is None)

    check("revoke returns True", store.revoke_session(token) is True)
    check("revoked token no longer resolves", store.resolve_session(token) is None)
    check("second revoke is idempotent", store.revoke_session(token) is False)

    # An expired token must not resolve.
    expired_settings = _settings(tmp, session_ttl_days=0)
    expired_store = _store(expired_settings)
    eu = expired_store.create_user("expired@example.com", "password123")
    et = expired_store.create_session(eu["id"])
    check("expired session no longer resolves", expired_store.resolve_session(et) is None)

    section("H. store: list_users never exposes a hash")
    rows = store.list_users()
    # Sections D-G all share this one database file on purpose (that is how
    # the app itself behaves), so assert against the live count rather than a
    # hardcoded number.
    check("lists every account", len(rows) == store.count_users(), f"{len(rows)} vs {store.count_users()}")
    keys = set(rows[0])
    check("no password field of any name", not any("password" in k or "hash" in k for k in keys), str(sorted(keys)))
    check("active_sessions is reported", "active_sessions" in keys)
    # Section G revoked the earlier token, so issue a fresh one, re-read the
    # list, and confirm it is counted — this is what the admin panel uses to
    # show "who is currently signed in".
    live_token = store.create_session(user["id"])
    fresh = [row for row in store.list_users() if row["email"] == "farmer@example.com"][0]
    check("a live session is counted", fresh["active_sessions"] >= 1, str(fresh["active_sessions"]))
    store.revoke_session(live_token)
    after = [row for row in store.list_users() if row["email"] == "farmer@example.com"][0]
    check("a revoked session stops being counted", after["active_sessions"] < fresh["active_sessions"],
          f"{after['active_sessions']} vs {fresh['active_sessions']}")

    # ------------------------------------------------------------------
    section("I. routes: registration")
    http_settings = _settings(tmp, admin_emails="")
    http_store = _store(http_settings)
    app = create_app(http_settings, auth_store=http_store)
    with TestClient(app) as client:
        r = client.post("/api/register", json={"email": "new@x.com", "password": "password123"})
        check("register -> 201", r.status_code == 201, f"got {r.status_code}")
        body = r.json()
        check("register returns ok", body.get("ok") is True)
        check("register returns a token", bool(body.get("token")))
        check("register returns the user", body.get("user", {}).get("email") == "new@x.com")
        check("register body has no password", "password" not in r.text.lower().replace("password123", ""),
              r.text[:200])
        first_token = body["token"]

        r = client.post("/api/register", json={"email": "new@x.com", "password": "password123"})
        check("duplicate registration -> 409", r.status_code == 409, f"got {r.status_code}")
        check("duplicate says the address is in use", "already exists" in r.json()["error"]["message"])

        r = client.post("/api/register", json={"email": "new@x.com", "password": "OTHERpass1"})
        check("case-insensitive duplicate also 409", r.status_code == 409, f"got {r.status_code}")

        r = client.post("/api/register", json={"email": "short@x.com", "password": "1234567"})
        check("short password -> 422", r.status_code == 422, f"got {r.status_code}")
        check("short password names the minimum", "at least 8" in r.json()["error"]["message"])

        r = client.post("/api/register", json={"email": "not-an-email", "password": "password123"})
        check("invalid email -> 422", r.status_code == 422, f"got {r.status_code}")
        check("invalid email uses the {ok,error} envelope", r.json().get("error", {}).get("kind") == "input")

        r = client.post("/api/register", json={"email": "", "password": "password123"})
        check("missing email -> 422", r.status_code == 422, f"got {r.status_code}")

        section("J. routes: login")
        r = client.post("/api/login", json={"email": "new@x.com", "password": "password123"})
        check("login -> 200", r.status_code == 200, f"got {r.status_code}")
        check("login returns a token", bool(r.json().get("token")))
        login_token = r.json()["token"]

        r = client.post("/api/login", json={"email": "new@x.com", "password": "WRONGPASS1"})
        check("wrong password -> 401", r.status_code == 401, f"got {r.status_code}")
        check("wrong password says 'Incorrect email or password'", "Incorrect email or password" in r.json()["error"]["message"])
        check("wrong password is not retryable", r.json()["error"]["retryable"] is False)

        r = client.post("/api/login", json={"email": "ghost@x.com", "password": "password123"})
        check("unknown email -> 401", r.status_code == 401, f"got {r.status_code}")
        check("unknown email uses the same message as a wrong password",
              r.json()["error"]["message"] == "Incorrect email or password.")

        section("K. routes: sessions and identity")
        r = client.get("/api/me")
        check("/api/me without a token -> 401", r.status_code == 401, f"got {r.status_code}")

        r = client.get("/api/me", headers={"Authorization": f"Bearer {login_token}"})
        check("/api/me with a token -> 200", r.status_code == 200, f"got {r.status_code}")
        check("/api/me returns the email", r.json()["user"]["email"] == "new@x.com")
        check("/api/me does not return a hash", "hash" not in r.text)

        r = client.get("/api/me", headers={"Authorization": "Bearer made-up-token"})
        check("bogus token -> 401", r.status_code == 401, f"got {r.status_code}")

        r = client.get("/api/me", headers={"Authorization": "Basic abc"})
        check("wrong auth scheme -> 401", r.status_code == 401, f"got {r.status_code}")

        r = client.post("/api/logout", headers={"Authorization": f"Bearer {login_token}"})
        check("logout -> 200", r.status_code == 200, f"got {r.status_code}")
        r = client.get("/api/me", headers={"Authorization": f"Bearer {login_token}"})
        check("token no longer valid after logout", r.status_code == 401, f"got {r.status_code}")
        r = client.post("/api/logout", headers={"Authorization": f"Bearer {login_token}"})
        check("logout twice still reports ok", r.status_code == 200, f"got {r.status_code}")

        r = client.get("/api/me", headers={"Authorization": f"Bearer {first_token}"})
        check("a separate session survives that logout", r.status_code == 200, f"got {r.status_code}")

        section("L. routes: brute-force throttle")
        for _ in range(http_settings.login_max_attempts):
            client.post("/api/login", json={"email": "throttle@x.com", "password": "wrongpass1"})
        r = client.post("/api/login", json={"email": "throttle@x.com", "password": "wrongpass1"})
        check("locked account -> 429", r.status_code == 429, f"got {r.status_code}")
        check("429 is marked retryable", r.json()["error"]["retryable"] is True)
        check("429 names a wait time", "try again" in r.json()["error"]["message"].lower())
        r = client.post("/api/login", json={"email": "other@x.com", "password": "wrongpass1"})
        check("a different account is not locked out", r.status_code == 401, f"got {r.status_code}")

        section("M. routes: admin")
        admin_target = "new@x.com"
        http_store.set_admin(admin_target, True)
        r = client.get("/api/admin/users")
        check("admin route without a token -> 401", r.status_code == 401, f"got {r.status_code}")

        client.post("/api/register", json={"email": "plain@x.com", "password": "password123"})
        plain_token = client.post("/api/login", json={"email": "plain@x.com", "password": "password123"}).json()["token"]

        r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {plain_token}"})
        check("non-admin -> 403", r.status_code == 403, f"got {r.status_code}")
        check("non-admin message says administrator", "Administrator" in r.json()["error"]["message"])

        admin_token = client.post("/api/login", json={"email": admin_target, "password": "password123"}).json()["token"]
        r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
        check("admin -> 200", r.status_code == 200, f"got {r.status_code}")
        check("admin listing has a total", r.json().get("total", 0) >= 3, str(r.json().get("total")))
        check("admin listing has no hash anywhere", "password_hash" not in r.text and "scrypt$" not in r.text)

        section("N. existing endpoints stay open without a token")
        # Empty upload is rejected by the size guard before any model call, so
        # this proves the route is REACHABLE without auth rather than testing
        # the pipeline (which would spend quota).
        r = client.post("/api/v1/analyze", files={"file": ("x.png", b"", "image/png")})
        check("analyze is not blocked by auth (400, not 401/403)", r.status_code == 400, f"got {r.status_code}")
        r = client.post("/api/v1/chat", json={"message": ""})
        check("chat is not blocked by auth (422, not 401/403)", r.status_code == 422, f"got {r.status_code}")
        r = client.get("/health")
        check("/health still open", r.status_code == 200, f"got {r.status_code}")

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
