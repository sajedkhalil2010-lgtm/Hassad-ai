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
    "replace_me",
    "changeme",
    "example",
    "xxxxxxxx",
)


def _normalize_email(value: object) -> str:
    """Lowercase and strip an email address for storage and comparison.

    Email addresses are case-insensitive in the part before the ``@``, so
    ``Farmer@Example.com`` and ``farmer@example.com`` must be one account —
    otherwise the second registration looks like a duplicate to the human but
    succeeds as a separate row, and that person then "loses" their history.
    """
    if value is None:
        return ""
    return str(value).strip().lower()


# ---------------------------------------------------------------------------
# Model IDs
# ---------------------------------------------------------------------------
# NOTE: `llama-3.2-11b-vision-preview` was DEPRECATED by Groq on 2025-04-14
# and is no longer served — requests to it return a model-not-found error.
# `qwen/qwen3.8-27b` is Groq's currently documented production vision model
# and, importantly for us, it supports JSON mode alongside image input.
VISION_MODEL_DEFAULT = "qwen/qwen3.8-27b"

# NOTE: `llama-3.3-70b-versatile` was not merely flagged for deprecation — it
# was RETIRED. Verified against a live key via `client.models.list()` on
# 2026-10-21: the id is absent and every call returned model-not-found. Docs
# listing it as a Production model were stale, so a doc-only check is NOT
# sufficient to validate a model id here; `run.py` now probes the live
# models endpoint at startup.
# `openai/gpt-oss-120b` is the strongest reasoning model actually served on
# the account and supports the JSON mode the advisory schema depends on.
# `NotFoundError` stays classified non-retryable so a future retirement
# surfaces as a clear message naming this field, not a silent retry loop.
ADVISOR_MODEL_DEFAULT = "openai/gpt-oss-120b"

# NOTE: the chat model was requested as `llama-3.3-70b-versatile`, but that id
# is NOT servable — a live call on 2026-10-21 returned HTTP 404 ("does not
# exist or you do not have access to it") and it is absent from
# models.list(). Same retired id the advisor originally hit.
# Chat is latency-sensitive — a farmer is sitting there typing — so the
# default favours the fast gpt-oss-20b (~0.8s measured) over the heavier
# gpt-oss-120b (~3.0s). Set CHAT_MODEL to `openai/gpt-oss-120b` in `.env` to
# trade ~2s per turn for maximum reasoning quality. `NotFoundError` is
# classified non-retryable, so a bad id fails immediately with a message
# naming this field instead of hanging.
CHAT_MODEL_DEFAULT = "openai/gpt-oss-20b"


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
    #: Interactive agronomist chat (ChatAgent).
    chat_model: str = CHAT_MODEL_DEFAULT
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

    # --- malformed-completion retry ----------------------------------------
    #: Regenerations when Groq rejects the completion as unparseable JSON
    #: (HTTP 400 "Failed to validate JSON"). That fault is in the model's
    #: answer rather than in our request, so one more draw is cheap. The delay
    #: is deliberately much shorter than the quota backoff: this mostly bites
    #: Arabic chat turns, where a farmer is waiting on a live conversation and
    #: two seconds of silence reads as a hang rather than as politeness.
    output_retry_attempts: int = 2
    output_retry_base_delay_s: float = 0.8

    # --- input limits ------------------------------------------------------
    max_image_bytes: int = 8 * 1024 * 1024
    max_image_side: int = 1600
    #: Chat turn limits. These bound cost AND blunt prompt-injection via a
    #: wall of pasted text; the server is the only thing that can enforce
    #: them, so they live here rather than in the frontend.
    max_chat_chars: int = 2000
    #: How many prior turns are replayed to the model on each request.
    max_chat_history: int = 12

    # --- advisor -----------------------------------------------------------
    #: When the API is unavailable, fall back to the embedded Tébessa KB.
    advisor_fallback_enabled: bool = True

    # --- local database & authentication -----------------------------------
    #: SQLite file holding accounts and sessions. A relative path resolves
    #: against the backend package, so the db lands next to main.py no matter
    #: which directory the server was started from (mirrors how the frontend
    #: path is derived).
    database_path: str = "hassad.db"
    #: A session token older than this is rejected on use.
    session_ttl_days: int = 30
    #: Minimum password length. Deliberately modest — demanding symbols and
    #: digits mostly teaches people to write the password on the keyboard,
    #: and the real defence is the scrypt cost below, not a checklist.
    min_password_length: int = 8
    #: Emails (comma-separated) promoted to admin when they register. Admins
    #: are the only ones allowed to call /api/admin/users. A user already in
    #: the db is re-promoted on their next login, so editing `.env` is enough
    #: — no manual db surgery.
    admin_emails: str = ""
    #: Brute-force throttle for /api/login, per email+IP pair.
    login_max_attempts: int = 5
    login_lockout_seconds: int = 300

    # --- password hashing cost ---------------------------------------------
    #: scrypt work factor. Memory usage is roughly 128*N*r bytes, so N=2**15
    #: with r=8 costs ~32 MiB and tens of ms per verification — that cost is
    #: the whole point: it makes an offline attack on a stolen db expensive.
    #: Raise for stronger hardware; hashes are self-describing, so raising it
    #: never invalidates existing accounts. Tests lower it to stay fast.
    scrypt_n: int = 2 ** 15
    scrypt_r: int = 8
    scrypt_p: int = 1

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
    def database_file(self) -> Path:
        """Absolute path to the SQLite database file.

        A relative ``database_path`` resolves against the backend package
        rather than the cwd, so the file is in the same place whether the
        server was started from the project root, from ``backend\\``, or by
        a double-clicked launcher.
        """
        path = Path(self.database_path).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def admin_email_list(self) -> list[str]:
        """Normalized (lowercased, de-duplicated) admin email addresses."""
        seen: dict[str, None] = {}
        for raw in self.admin_emails.split(","):
            email = _normalize_email(raw)
            if email:
                seen.setdefault(email, None)
        return list(seen)

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
