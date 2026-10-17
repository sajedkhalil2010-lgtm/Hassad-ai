"""Central configuration for the Hassad AI backend.

v4 — 100% local autonomy: the diagnosis pipeline runs entirely on
OpenCV + NumPy inside this process. **No external AI API exists in the
runtime** (no Gemini, no Hugging Face), hence no API keys, no rate
limits and no 429s. An old ``.env`` that still carries key templates is
ignored (``extra="ignore"``).

Security rules enforced here
----------------------------
1. **No secret is ever hard-coded.**  Every credential (``SECRET_KEY`` …)
   is read at *runtime* from the process environment or from the
   git-ignored ``.env`` file located next to the project root, using
   ``pydantic-settings``.
2. **Strict validation.**  Fields are range-checked / format-checked, an
   explicitly provided weak ``SECRET_KEY`` aborts startup.
3. **Relative SQLite paths are anchored to the project root**, so the API
   behaves the same regardless of the working directory it is launched
   from.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("hassad.config")

# Project root:  <...>/v1 hassad/backend   (…/app/core/config.py -> parents[2])
BASE_DIR: Path = Path(__file__).resolve().parents[2]
ENV_FILE: Path = BASE_DIR / ".env"

_PLACEHOLDER_SECRETS = {
    "",
    "change_me",
    "change-me",
    "changeme",
    "secret",
    "replace_me",
    "replace-me",
    "your-secret-key",
    "your_secret_key",
    "todo",
    "xxx",
}

# Template wording that may still appear in a stale .env.
_PLACEHOLDER_PREFIXES = (
    "put_your_",
    "your_",
    "paste_",
    "replace_",
    "change_",
    "insert_",
    "example_",
)
_PLACEHOLDER_SUFFIXES = ("_here", "-here")


def _is_placeholder(value: str) -> bool:
    """True for empty/template values that must never be used as real credentials.

    A freshly copied ``.env`` therefore boots safely: the server starts,
    logs one warning, and the affected feature reports *unconfigured*
    instead of ever using a fake key.
    """
    candidate = (value or "").strip().lower()
    if candidate in _PLACEHOLDER_SECRETS:
        return True
    if candidate.startswith(_PLACEHOLDER_PREFIXES):
        return True
    return candidate.endswith(_PLACEHOLDER_SUFFIXES)

_DEFAULT_CORS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:8000"
)


class Settings(BaseSettings):
    """Typed, validated application settings loaded from environment + .env."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = "Hassad AI"
    APP_DESCRIPTION: str = (
        "حَصاد AI — Two-stage crop disease diagnosis, agronomy RAG chatbot "
        "and offline-first sync for smallholder farms."
    )
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development", description="development|staging|production")
    DEBUG: bool = True
    ENABLE_DOCS: bool = True
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_DIR: str = str(BASE_DIR)

    # ------------------------------------------------------------------
    # Security / JWT
    # ------------------------------------------------------------------
    SECRET_KEY: str = Field(default="", description="HMAC secret for JWT signing (>= 32 chars)")
    ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "hassad-ai"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=5, le=43_200)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=14, ge=1, le=90)
    BCRYPT_ROUNDS: int = Field(default=12, ge=4, le=15)
    PASSWORD_MIN_LENGTH: int = Field(default=8, ge=6, le=64)
    PASSWORD_MAX_BYTES: int = 72  # bcrypt hardware limit

    # ------------------------------------------------------------------
    # HTTP / CORS
    # ------------------------------------------------------------------
    CORS_ORIGINS: str = _DEFAULT_CORS
    ALLOWED_HOSTS: str = "*"

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str = "sqlite+aiosqlite:///./hassad.db"
    DB_ECHO: bool = False
    DB_CONNECT_TIMEOUT: float = Field(default=30.0, ge=1.0, le=300.0)
    DB_POOL_SIZE: int = Field(default=5, ge=1, le=50)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=100)

    # ------------------------------------------------------------------
    # Two-stage pipeline thresholds (decision policy)
    # ------------------------------------------------------------------
    CROP_AUTO_THRESHOLD: float = Field(default=0.80, ge=0.0, le=1.0)
    DISEASE_CONFIRM_THRESHOLD: float = Field(default=0.75, ge=0.0, le=1.0)
    DISEASE_REVIEW_THRESHOLD: float = Field(default=0.50, ge=0.0, le=1.0)

    # Agentic budget: wall-clock ceiling the supervisor grants one full
    # diagnostic run (local vision + matching + plan). Sub-second in
    # practice; kept as a safety net for the coordination loop.
    AGENT_DIAGNOSTIC_BUDGET_SECONDS: float = Field(default=30.0, ge=5.0, le=120.0)

    # ------------------------------------------------------------------
    # Local quantitative vision engine (OpenCV + NumPy)
    # ------------------------------------------------------------------
    #: longest image side processed by the segmentation engine
    CV_MAX_SIDE: int = Field(default=960, ge=320, le=4096)
    #: severity categorisation thresholds on affected_area_percentage
    SEVERITY_EARLY_MAX_PCT: float = Field(default=5.0, ge=0.0, le=100.0)
    SEVERITY_MODERATE_MAX_PCT: float = Field(default=15.0, ge=0.0, le=100.0)

    # Image validation (quality + privacy)
    IMAGE_MAX_BYTES: int = Field(default=8 * 1024 * 1024, ge=16 * 1024, le=64 * 1024 * 1024)
    IMAGE_MIN_BYTES: int = Field(default=1024, ge=128)
    IMAGE_MIN_DIM: int = Field(default=64, ge=32)
    IMAGE_MAX_DIM: int = Field(default=8192, ge=512)
    IMAGE_BLUR_MIN_VARIANCE: float = Field(default=25.0, ge=0.0)
    STORE_IMAGES: bool = False
    IMAGE_STORAGE_DIR: str = "uploads"

    # ------------------------------------------------------------------
    # AI — local RAG chatbot (retrieval over the local knowledge base)
    # ------------------------------------------------------------------
    RAG_TOP_K: int = Field(default=4, ge=1, le=10)
    CHAT_HISTORY_MAX_MESSAGES: int = Field(default=12, ge=0, le=50)
    CHAT_MAX_INPUT_CHARS: int = Field(default=4000, ge=50, le=32_000)

    # ------------------------------------------------------------------
    # Bootstrap (optional — filled in .env only)
    # ------------------------------------------------------------------
    SEED_ADMIN_EMAIL: str = ""
    SEED_ADMIN_PASSWORD: str = ""

    # ==================================================================
    # Validators
    # ==================================================================
    @field_validator("CORS_ORIGINS", "ALLOWED_HOSTS", mode="after")
    @classmethod
    def _strip_csv(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("ENVIRONMENT", mode="after")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        allowed = {"development", "staging", "production"}
        normalised = (value or "development").strip().lower()
        if normalised not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {sorted(allowed)}, got {value!r}")
        return normalised

    @field_validator("SECRET_KEY", mode="after")
    @classmethod
    def _strip_secret(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("DATABASE_URL", mode="after")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Normalise driver and anchor relative SQLite paths to the project root."""
        url = (value or "").strip()

        # Convenience: allow plain `postgres://` / `postgresql://` URLs.
        scheme = url.split("://", 1)[0] if "://" in url else ""
        if scheme in {"postgres", "postgresql"}:
            url = "postgresql+asyncpg://" + url.split("://", 1)[1]
        elif scheme.startswith("sqlite") and "+" not in scheme:
            url = "sqlite+aiosqlite://" + url.split("://", 1)[1]

        if url.startswith("sqlite"):
            driver, _, path = url.partition(":///")
            if path and not path.startswith(":") and not Path(path).is_absolute():
                url = f"{driver}:///{(BASE_DIR / path).as_posix()}"
        return url

    @model_validator(mode="after")
    def _ensure_strong_secret_key(self) -> "Settings":
        """Generate an ephemeral secret when none is supplied; reject weak ones."""
        if _is_placeholder(self.SECRET_KEY):
            # Never hard-code a fallback secret: derive a random per-process one.
            self.SECRET_KEY = secrets.token_urlsafe(64)
            logger.warning(
                "SECRET_KEY is not set — generated an EPHEMERAL key for this process. "
                "All JWTs will be invalidated on restart. Set SECRET_KEY in .env for "
                "production (python -c \"import secrets; print(secrets.token_urlsafe(64))\")."
            )
        elif len(self.SECRET_KEY) < 32:
            raise ValueError(
                "SECRET_KEY is too short (minimum 32 characters). Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return self

    @model_validator(mode="after")
    def _threshold_sanity(self) -> "Settings":
        if not (0.0 <= self.DISEASE_REVIEW_THRESHOLD <= self.DISEASE_CONFIRM_THRESHOLD <= 1.0):
            raise ValueError(
                "Thresholds must satisfy 0 <= DISEASE_REVIEW_THRESHOLD "
                "<= DISEASE_CONFIRM_THRESHOLD <= 1"
            )
        if not (0.0 <= self.CROP_AUTO_THRESHOLD <= 1.0):
            raise ValueError("CROP_AUTO_THRESHOLD must be between 0 and 1")
        if not (0.0 < self.SEVERITY_EARLY_MAX_PCT < self.SEVERITY_MODERATE_MAX_PCT <= 100.0):
            raise ValueError(
                "Severity thresholds must satisfy 0 < SEVERITY_EARLY_MAX_PCT "
                "< SEVERITY_MODERATE_MAX_PCT <= 100"
            )
        return self

    # ==================================================================
    # Derived helpers
    # ==================================================================
    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins parsed from the comma separated ``CORS_ORIGINS`` value."""
        if not self.CORS_ORIGINS:
            return []
        return [origin.strip().strip("/") for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        if not self.ALLOWED_HOSTS:
            return ["*"]
        return [host.strip() for host in self.ALLOWED_HOSTS.split(",") if host.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (reads .env exactly once)."""
    return Settings()
