"""Central configuration for the Hassad AI backend.

Security rules enforced here
----------------------------
1. **No secret is ever hard-coded.**  Every credential (``SECRET_KEY``,
   ``HUGGINGFACE_API_KEY``, ``GEMINI_API_KEY`` …) is read at *runtime* from the
   process environment or from the git-ignored ``.env`` file located next to the
   project root, using ``pydantic-settings``.
2. **Strict validation.**  Fields are range-checked / format-checked, an
   explicitly provided weak ``SECRET_KEY`` aborts startup, and a missing secret
   degrades gracefully (the feature returns ``503`` with an actionable message
   instead of crashing or silently using a fake key).
3. **Relative SQLite paths are anchored to the project root**, so the API behaves
   the same regardless of the working directory it is launched from.
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

# Template wording used in the shipped .env (e.g. "put_your_gemini_key_here").
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

    A freshly copied ``.env`` therefore boots safely: the server starts, logs one
    warning, and the affected feature reports *unconfigured* (503 / fallback)
    instead of sending a fake key upstream.  Real tokens (``hf_...``, ``AIza...``)
    never match these patterns.
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
    # AI — Hugging Face free inference (Crop Doctor)
    # ------------------------------------------------------------------
    HUGGINGFACE_API_KEY: str = Field(default="", repr=False)
    HF_API_BASE_URL: str = "https://router.huggingface.co/hf-inference"
    HF_API_FALLBACK_URLS: str = "https://api-inference.huggingface.co"
    HF_CROP_MODEL_ID: str = "google/vit-base-patch16-224"
    HF_DISEASE_MODEL_ID: str = "microsoft/resnet-50"
    HF_TIMEOUT_SECONDS: float = Field(default=45.0, ge=5.0, le=300.0)
    HF_MAX_RETRIES: int = Field(default=3, ge=0, le=6)
    HF_RETRY_BACKOFF_SECONDS: float = Field(default=2.0, ge=0.5, le=30.0)

    # Two-stage pipeline thresholds
    CROP_AUTO_THRESHOLD: float = Field(default=0.80, ge=0.0, le=1.0)
    DISEASE_CONFIRM_THRESHOLD: float = Field(default=0.75, ge=0.0, le=1.0)
    DISEASE_REVIEW_THRESHOLD: float = Field(default=0.50, ge=0.0, le=1.0)

    # Image validation (quality + privacy)
    IMAGE_MAX_BYTES: int = Field(default=8 * 1024 * 1024, ge=16 * 1024, le=64 * 1024 * 1024)
    IMAGE_MIN_BYTES: int = Field(default=1024, ge=128)
    IMAGE_MIN_DIM: int = Field(default=64, ge=32)
    IMAGE_MAX_DIM: int = Field(default=8192, ge=512)
    IMAGE_BLUR_MIN_VARIANCE: float = Field(default=25.0, ge=0.0)
    STORE_IMAGES: bool = False
    IMAGE_STORAGE_DIR: str = "uploads"

    # ------------------------------------------------------------------
    # AI — Google Gemini free tier (RAG chatbot)
    # ------------------------------------------------------------------
    GEMINI_API_KEY: str = Field(default="", repr=False)
    # Optional backup keys: automatic failover when one key hits the
    # free-tier daily 429 quota (20 req/day per model per key/project).
    # Chain order tried: GEMINI_API_KEY_1..4 first, base key last.
    # Secrets: never logged, never repr'd, never returned by any endpoint.
    GEMINI_API_KEY_1: str = Field(default="", repr=False)
    GEMINI_API_KEY_2: str = Field(default="", repr=False)
    GEMINI_API_KEY_3: str = Field(default="", repr=False)
    GEMINI_API_KEY_4: str = Field(default="", repr=False)
    # gemini-2.5-flash was retired for new API keys (404 "no longer
    # available to new users"); Google now directs callers to gemini-3.8-flash.
    GEMINI_MODEL_NAME: str = "gemini-3.8-flash"
    # Free-tier first-byte latency regularly runs 60-120s, so this is the
    # TOTAL streamed-generation budget: rag_service enforces it as a hard
    # deadline and degrades to a retrieval-only answer when it expires.
    GEMINI_TIMEOUT_SECONDS: float = Field(default=180.0, ge=5.0, le=300.0)
    GEMINI_MAX_OUTPUT_TOKENS: int = Field(default=1024, ge=64, le=8192)
    GEMINI_TEMPERATURE: float = Field(default=0.3, ge=0.0, le=2.0)
    # ------------------------------------------------------------------
    # AI — Gemini multimodal VISION (Crop Doctor / disease_detector)
    # One live shot per stage with a generous deadline: Google's free
    # tier commonly needs 20-45s under load, so early cutoffs produced
    # honest-but-useless 503s. No fallback verdict exists — only the
    # deadline is long. UI shows a staged loading state meanwhile.
    # ------------------------------------------------------------------
    # Live Gemini attempt budget (tunable 10-60s via .env). Default 45s
    # so a healthy answer is never cut off prematurely; exceeding it →
    # structured 503 (vision_busy) with retry guidance.
    VISION_TIMEOUT_SECONDS: float = Field(default=45.0, ge=10.0, le=60.0)
    VISION_MODEL_NAME: str = ""  # empty → GEMINI_MODEL_NAME
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

    @field_validator(
        "HUGGINGFACE_API_KEY",
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        mode="after",
    )
    @classmethod
    def _normalise_api_keys(cls, value: str) -> str:
        """Template placeholders count as 'not configured' (never sent upstream)."""
        cleaned = (value or "").strip()
        return "" if _is_placeholder(cleaned) else cleaned

    @field_validator("HF_API_BASE_URL", mode="after")
    @classmethod
    def _validate_hf_base(cls, value: str) -> str:
        value = (value or "").strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("HF_API_BASE_URL must be an http(s) URL")
        return value

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
        if not (0.0 <= self.GEMINI_TEMPERATURE <= 2.0):
            raise ValueError("GEMINI_TEMPERATURE must be between 0 and 2")
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
    def hf_fallback_urls(self) -> list[str]:
        if not self.HF_API_FALLBACK_URLS:
            return []
        return [
            url.strip().rstrip("/")
            for url in self.HF_API_FALLBACK_URLS.split(",")
            if url.strip().rstrip("/")
        ]

    @property
    def huggingface_configured(self) -> bool:
        """True when a Hugging Face token is available (value never exposed)."""
        return bool(self.HUGGINGFACE_API_KEY)

    @property
    def gemini_api_keys(self) -> list[str]:
        """Ordered failover chain for live Gemini calls (deduplicated).

        Numbered backups first (``GEMINI_API_KEY_1..4`` — the primary key
        the operator configures), then the base ``GEMINI_API_KEY`` as the
        last resort. Only the *count* is ever exposed (health endpoint);
        values never leave the process.
        """
        chain: list[str] = []
        for key in (
            self.GEMINI_API_KEY_1,
            self.GEMINI_API_KEY_2,
            self.GEMINI_API_KEY_3,
            self.GEMINI_API_KEY_4,
            self.GEMINI_API_KEY,
        ):
            if key and key not in chain:
                chain.append(key)
        return chain

    @property
    def gemini_configured(self) -> bool:
        """True when any Gemini API key is available (values never exposed)."""
        return bool(self.gemini_api_keys)

    @property
    def vision_model_name(self) -> str:
        """Model used by the multimodal vision pipeline (crop + disease)."""
        return self.VISION_MODEL_NAME or self.GEMINI_MODEL_NAME

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (reads .env exactly once)."""
    return Settings()
