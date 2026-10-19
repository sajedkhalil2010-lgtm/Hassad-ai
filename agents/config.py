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
PLACEHOLDER_KEY = "your_single_gemini_api_key_here"


class Settings(BaseSettings):
    """Runtime settings. Env vars override `.env` values."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- single credential -------------------------------------------------
    gemini_api_key: SecretStr | None = None

    # --- model behaviour ---------------------------------------------------
    # NOTE: `gemini-2.5-flash` was retired for new API users and now returns
    # 404 NOT_FOUND. `gemini-3.8-flash` is the current recommended flash model.
    gemini_model: str = "gemini-3.8-flash"
    gemini_temperature: float = 0.2
    gemini_max_output_tokens: int = 4096
    #: Live vision passes measured ~45-50 s; keep generous headroom so real
    #: leaf photos do not trip the client timeout.
    request_timeout_s: float = 120.0

    # --- input limits ------------------------------------------------------
    max_image_bytes: int = 8 * 1024 * 1024
    max_image_side: int = 1600

    # --- advisor -----------------------------------------------------------
    #: When the API is unavailable, fall back to the embedded Tébessa KB.
    advisor_fallback_enabled: bool = True

    # --- HTTP --------------------------------------------------------------
    #: Comma-separated in `.env`. `*` allows any origin (fine for local dev).
    cors_origins: str = "*"

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _reject_placeholder(cls, value: object) -> str | None:
        """Treat the shipped placeholder as 'not configured'."""
        if value is None:
            return None
        text = str(value).strip()
        if not text or text == PLACEHOLDER_KEY or text.lower() in {"none", "null", "unset"}:
            return None
        return text

    @property
    def is_configured(self) -> bool:
        """True only when a real (non-placeholder) key is present."""
        return self.gemini_api_key is not None

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse the comma-separated CORS setting into a list."""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()] or ["*"]

    @property
    def api_key(self) -> str | None:
        """The raw key, or ``None`` when unconfigured. Use sparingly."""
        if self.gemini_api_key is None:
            return None
        return self.gemini_api_key.get_secret_value()

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
