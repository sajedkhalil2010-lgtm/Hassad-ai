"""Single-key configuration for Hassad AI.

One credential, one source of truth. The key is loaded through
pydantic-settings (process environment > ``.env`` > default) and stored as a
``SecretStr`` so it can never be accidentally printed, logged or repr'd.

The project intentionally uses a **single** key: there is no key pool and no
rotation logic. ``SecretStr.__repr__`` renders ``**********`` and
``str(secret)`` does the same, so passing the settings object around is safe.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

#: The literal placeholder shipped in `.env` / `.env.example`. It is treated
#: as "not configured" rather than as a real credential.
PLACEHOLDER_KEY = "gsk_your_actual_key_here"

#: Substrings that mark a value as "not filled in yet". Checked case-insensitively
#: so hand-edited placeholders (e.g. a real-looking `gsk_` prefix glued to
#: sample text) are still rejected instead of being sent to Groq as a bad key.
PLACEHOLDER_MARKERS = (
    "your_actual_key",
    "your_real_key",
    "your_groq_api_key",
    "your_key_here",
    "your_single_gemini_api_key_here",
    "replace_me",
    "changeme",
    "example",
    "xxxxxxxx",
)

# ---------------------------------------------------------------------------
# Model IDs
# ---------------------------------------------------------------------------
# NOTE: `llama-3.2-11b-vision-preview` was DEPRECATED by Groq on 2025-04-14
# and is no longer served — requests to it return a model-not-found error.
# `qwen/qwen3.8-27b` is Groq's currently documented production vision model
# and, importantly for us, it supports JSON mode alongside image input.
VISION_MODEL_DEFAULT = "qwen/qwen3.8-27b"

# NOTE: `llama-3.3-70b-versatile` carries a Groq deprecation notice (shutdown
# date 2026-08-16) but is still listed as a Production model. Kept as the
# user's explicit choice; `groq.NotFoundError` is classified as non-retryable
# so a future retirement surfaces as a clear message rather than a silent hang.
ADVISOR_MODEL_DEFAULT = "llama-3.3-70b-versatile"


class Settings(BaseSettings):
    """Runtime settings. Env vars override `.env` values."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- single credential -------------------------------------------------
    groq_api_key: SecretStr | None = None

    # --- model behaviour ---------------------------------------------------
    #: Image analysis (DiagnosticAgent).
    vision_model: str = VISION_MODEL_DEFAULT
    #: Text reasoning (AdvisorAgent).
    advisor_model: str = ADVISOR_MODEL_DEFAULT
    temperature: float = 0.2
    max_output_tokens: int = 4096
    #: Groq is fast, but keep generous headroom so real leaf photos never trip
    #: the client timeout on a congested connection.
    request_timeout_s: float = 120.0

    # --- quota (HTTP 429) retry -------------------------------------------
    #: Total attempts on a 429, including the first one.
    quota_retry_attempts: int = 3
    #: First backoff delay; doubles each retry (2s, 4s, ...).
    quota_retry_base_delay_s: float = 2.0

    # --- input limits ------------------------------------------------------
    max_image_bytes: int = 8 * 1024 * 1024
    max_image_side: int = 1600

    # --- advisor -----------------------------------------------------------
    #: When the API is unavailable, fall back to the embedded Tébessa KB.
    advisor_fallback_enabled: bool = True

    # --- HTTP --------------------------------------------------------------
    #: Comma-separated in `.env`. `*` allows any origin (fine for local dev).
    cors_origins: str = "*"

    @field_validator("groq_api_key", mode="before")
    @classmethod
    def _reject_placeholder(cls, value: object) -> str | None:
        """Treat the shipped placeholder as 'not configured'.

        Deliberately tolerant: a key that merely *looks* filled in (a `gsk_`
        prefix glued to sample text) must still be rejected, otherwise the app
        reports "configured" and then fails downstream with a confusing 401.
        """
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"none", "null", "unset"}:
            return None
        if text == PLACEHOLDER_KEY:
            return None
        lowered = text.lower()
        if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
            return None
        return text

    @property
    def is_configured(self) -> bool:
        """True only when a real (non-placeholder) key is present."""
        return self.groq_api_key is not None

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse the comma-separated CORS setting into a list."""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()] or ["*"]

    @property
    def api_key(self) -> str | None:
        """The raw key, or ``None`` when unconfigured. Use sparingly."""
        if self.groq_api_key is None:
            return None
        return self.groq_api_key.get_secret_value()

    @property
    def masked_key(self) -> str:
        """A log-safe rendering: first/last 4 chars only."""
        if not self.is_configured:
            return "not configured"
        raw = self.api_key or ""
        if len(raw) < 12:
            return "***"
        return f"{raw[:4]}...{raw[-4:]}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached process-wide settings instance."""
    return Settings()
