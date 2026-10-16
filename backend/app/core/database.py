"""Async SQLAlchemy engine, session factory and shared declarative base.

* SQLite (``aiosqlite``) out of the box for local/zero-cost execution.
* Swap ``DATABASE_URL`` to ``postgresql+asyncpg://…`` for PostgreSQL/Supabase —
  no code changes required.
* All timestamps are handled as **timezone-aware UTC**; ``ensure_utc()`` papers
  over SQLite's naive-datetime round-tripping so comparisons stay correct.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import MetaData, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

logger = logging.getLogger("hassad.database")

# Deterministic constraint/index names (works on SQLite and PostgreSQL).
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every ORM model of Hassad AI."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


# ----------------------------------------------------------------------
# Time & identifier helpers
# ----------------------------------------------------------------------
def utcnow() -> datetime:
    """Current UTC time (timezone aware)."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Normalise a datetime to aware-UTC (naive values are assumed to be UTC).

    SQLite returns naive datetimes even when the column was declared with
    ``DateTime(timezone=True)``; every comparison in the sync engine goes
    through this helper so last-write-wins resolution is always correct.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def new_id() -> str:
    """Generate a portable UUID4 string primary key."""
    return str(uuid4())


# ----------------------------------------------------------------------
# DATABASE_URL normalisation (Supabase dashboard strings -> asyncpg-safe)
# ----------------------------------------------------------------------
# Connection-string parameters that libpq/psycopg accept but asyncpg rejects
# outright (they crash with "unexpected keyword argument").  Only the keys
# below are forwarded; everything else is dropped with a debug log.
#
# Only *string-safe* parameters live here.  Numeric ones (``timeout``,
# ``command_timeout``, ``statement_cache_size``, …) are deliberately excluded:
# URL query values are always strings, and asyncpg compares them against
# numbers (``str < int`` -> TypeError).  They are supplied with real int types
# through ``connect_args`` instead.
_ASYNCPG_PARAMS = {
    "ssl",
    "target_session_attrs",
    "krbsrvname",
    "gsslib",
    "prepared_statement_cache_size",  # coerced to int by SQLAlchemy's dialect
}

# Supabase terminates TLS on both the direct host and the pooler endpoints.
_SUPABASE_HOST_MARKERS = (".supabase.co", ".supabase.com")

# Supavisor "transaction" pooling (port 6543) breaks named PREPARE caching.
_TRANSACTION_POOLER_PORT = 6543


def is_transaction_pooler(url: str) -> bool:
    """True when the URL targets Supavisor transaction pooling (port 6543)."""
    if not url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        return False
    from sqlalchemy.engine.url import make_url

    return make_url(url).port == _TRANSACTION_POOLER_PORT


def normalize_database_url(url: str) -> str:
    """Rewrite a pasted PostgreSQL URL so the asyncpg driver accepts it.

    Handles, without leaking the password into logs:

    * ``postgres://`` / ``postgresql://`` -> driver ``postgresql+asyncpg``;
    * libpq-only parameters (``pgbouncer``, ``sslmode``, ``options``, …) are
      mapped (``sslmode`` -> ``ssl``) or dropped instead of crashing asyncpg;
    * Supabase hosts get ``ssl=require`` when no TLS mode was given;
    * the transaction pooler (port 6543) disables prepared-statement caching
      on both asyncpg and SQLAlchemy so statements survive pooling.

    SQLite URLs (and anything else) are returned unchanged.
    """
    if not url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        return url

    from sqlalchemy.engine.url import URL, make_url

    parsed = make_url(url)

    normalised_query: dict[str, str] = {}
    raw_query = dict(parsed.query)
    for key, value in raw_query.items():
        text_value = str(value)
        if key == "sslmode":
            normalised_query.setdefault("ssl", text_value)
        elif key in _ASYNCPG_PARAMS:
            normalised_query[key] = text_value
        else:  # libpq-only parameter: asyncpg would raise TypeError
            logger.debug("Ignoring connection parameter unsupported by asyncpg: %s", key)

    host = parsed.host or ""
    if "ssl" not in normalised_query and any(marker in host for marker in _SUPABASE_HOST_MARKERS):
        normalised_query["ssl"] = "require"

    if parsed.port == _TRANSACTION_POOLER_PORT:
        # Named prepared statements do not survive Supavisor transaction
        # pooling ("prepared statement already exists").  SQLAlchemy's cache
        # is disabled here via the URL (the dialect coerces it to int); the
        # asyncpg-side cache is disabled in get_engine() with a typed int.
        normalised_query.setdefault("prepared_statement_cache_size", "0")

    rebuilt = URL.create(
        drivername="postgresql+asyncpg",
        username=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        database=parsed.database,
        query=normalised_query,
    )
    return rebuilt.render_as_string(hide_password=False)


# ----------------------------------------------------------------------
# Engine / session lifecycle
# ----------------------------------------------------------------------
def _register_sqlite_pragmas(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:  # pragma: no cover - driver hook
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
        except Exception:  # noqa: BLE001 - pragma setup must never break startup
            logger.debug("Could not apply SQLite PRAGMAs", exc_info=True)


def get_engine() -> AsyncEngine:
    """Create (once) and return the async engine for the configured URL."""
    global _engine
    if _engine is None:
        settings = get_settings()
        url = normalize_database_url(settings.DATABASE_URL)
        kwargs: dict[str, Any] = {"echo": settings.DB_ECHO, "future": True}

        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"timeout": settings.DB_CONNECT_TIMEOUT}
            if ":memory:" in url:
                from sqlalchemy.pool import StaticPool

                kwargs["poolclass"] = StaticPool
                kwargs["connect_args"] = {"check_same_thread": False}
        else:  # PostgreSQL / Supabase
            kwargs.update(
                pool_size=settings.DB_POOL_SIZE,
                max_overflow=settings.DB_MAX_OVERFLOW,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
            if is_transaction_pooler(url):
                # Typed int here on purpose - a URL string would crash asyncpg.
                kwargs["connect_args"] = {"statement_cache_size": 0}

        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _register_sqlite_pragmas(_engine)
        logger.info("Database engine created for %s", url.split("://", 1)[0] + "://…")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create (once) and return the async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped :class:`AsyncSession`."""
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create tables if they do not exist (idempotent, safe on every boot).

    Production tip: replace with Alembic migrations when the schema evolves.
    """
    # Importing the models registers them on Base.metadata.
    from app.models import (  # noqa: F401  (side effect: metadata population)
        ChatMessage,
        ChatSession,
        Diagnosis,
        Farm,
        KnowledgeBase,
        Plot,
        User,
    )

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema verified (%d tables)", len(Base.metadata.tables))


async def dispose_engine() -> None:
    """Close all pooled connections (graceful shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine disposed")


async def check_db_health() -> bool:
    """Cheap connectivity probe used by ``GET /health``."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health checks must swallow errors
        logger.warning("Database health probe failed", exc_info=True)
        return False
