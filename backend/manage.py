"""Developer tooling for the local account database.

Usage, from ``backend\\``::

    python -m manage list-users
    python -m manage promote farmer@example.com
    python -m manage demote farmer@example.com
    python -m manage db-info

Exists so that "who is registered?" is a command rather than a hand-written
SELECT or a running server. It reads the same ``Settings`` as the app, so it
always finds the database the server actually uses.
"""

from __future__ import annotations

import sys
from typing import Sequence

# The project path contains non-ASCII characters, and this file is commonly
# run with output redirected to a file or pipe — where stdout defaults to a
# code page such as cp1252 and the first printed path raises UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):  # pragma: no cover - interpreter setup
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agents.config import get_settings
from auth import AuthStore


def _table(rows: Sequence[Sequence[object]], headers: Sequence[str]) -> str:
    """Render a plain ASCII table without a dependency."""
    body = [[("" if cell is None else str(cell)) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    out = [line(list(headers)), line(["-" * w for w in widths])]
    out.extend(line(row) for row in body)
    return "\n".join(out)


def _display(value: object) -> object:
    """Render a SQLite integer flag as a readable yes/no."""
    if isinstance(value, int) and value in (0, 1):
        return "yes" if value else "no"
    return value if value is not None else "-"


def _store() -> AuthStore:
    settings = get_settings()
    store = AuthStore.from_settings(settings)
    store.init_schema()
    return store


def cmd_list_users(store: AuthStore) -> int:
    users = store.list_users()
    if not users:
        print("No accounts registered yet.")
        print(f"Database: {store.path}")
        return 0

    rows = [
        (
            user["id"],
            user["email"],
            user["name"] or "-",
            _display(user["is_admin"]),
            _display(user["is_active"]),
            user["created_at"][:19].replace("T", " "),
            (user["last_login_at"] or "-")[:19].replace("T", " "),
            user["active_sessions"],
        )
        for user in users
    ]
    print(_table(rows, ("id", "email", "name", "admin", "active", "created (UTC)", "last login", "sessions")))
    print(f"\n{len(users)} account(s) in {store.path}")
    return 0


def cmd_promote(store: AuthStore, email: str, is_admin: bool) -> int:
    verb = "Promoted" if is_admin else "Demoted"
    if store.set_admin(email, is_admin):
        print(f"{verb} {email}")
        return 0
    print(f"No account found for {email}")
    return 1


def cmd_db_info(store: AuthStore) -> int:
    path = store.path
    size = path.stat().st_size if path.is_file() else 0
    print(f"  Path            : {path}")
    print(f"  Exists          : {'yes' if path.is_file() else 'no'}")
    print(f"  Size            : {size} bytes")
    print(f"  Accounts        : {store.count_users()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(__doc__.strip())
        return 0

    command, *rest = args
    store = _store()

    if command == "list-users":
        return cmd_list_users(store)
    if command in {"promote", "demote"}:
        if not rest:
            print(f"Usage: python -m manage {command} <email>")
            return 2
        return cmd_promote(store, rest[0], command == "promote")
    if command == "db-info":
        return cmd_db_info(store)

    print(f"Unknown command: {command}")
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
