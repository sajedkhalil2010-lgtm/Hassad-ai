"""KeyPoolManager — smart multi-key state for every model interaction.

v3 split: this module is **pure key policy** — it holds no SDK imports and
makes no requests itself. It answers exactly three questions for the
CognitionEngine (``app.agents.cognition``, the *only* module that talks to
the google-genai SDK):

* *Which key is next, and is it still allowed to be used?*
* *This key just answered 429 — how long should it sit out?*
* *Is the whole pool spent yet?*

Policy carried over from v1/v2 hardening (all preserved):

* **Pool** — ``GEMINI_API_KEY_1..4`` first (operator's primary + backups
  in ``.env``), base ``GEMINI_API_KEY`` last; deduplicated; each field
  honours env-var > .env > default via pydantic-settings. Values are
  secrets: never logged, never repr'd, never returned to any client —
  ``/health`` sees only :meth:`KeyPoolManager.size`.
* **Rotation on 429** — a quota answer fails over to the next key
  (decided here, executed by the cognition gateway). Only quota rotates;
  any other failure surfaces immediately — never a blind retry loop.
* **Quota memo** — a spent key is skipped until Google's own reset
  window (parsed from the 429 payload) elapses; when the whole pool is
  spent, the gateway fails **instantly with zero network requests**.
* **Zero SDK retries** — enforced where clients are built: only the
  cognition gateway constructs clients, always with
  ``HttpRetryOptions(attempts=0)``.
"""

from __future__ import annotations

import logging
import re
import time

from app.agents.base import AgentFailure, failure_kind
from app.core.config import get_settings

logger = logging.getLogger("hassad.agents.keys")

#: In-process quota memo: pool position → monotonic time when Google's
#: own reset window elapses. Shared by every agent in this process.
_KEY_BLOCKED_UNTIL: dict[int, float] = {}


def quota_wait_seconds(exc: Exception) -> int | None:
    """Extract Google's own reset window from a 429 payload.

    Handles both shapes Google sends: ``"Please retry in 13h17m20.3s"``
    (message) and ``"retryDelay": "47840s"`` (details); ``None`` if absent.
    """
    text = str(exc)
    match = re.search(r"retry in (\d+)h(\d+)m", text)
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60
    match = re.search(r"retryDelay[\"']?\s*:\s*[\"']?(\d+)s", text)
    if match:
        return int(match.group(1))
    match = re.search(r"retry in (\d+)s", text)
    if match:
        return int(match.group(1))
    return None


class KeyPoolManager:
    """Key-state policy for the rotating Gemini pool (process-wide)."""

    # -- pool ---------------------------------------------------------
    @staticmethod
    def pool() -> list[str]:
        """Ordered key chain (values never leave the process)."""
        return get_settings().gemini_api_keys

    @staticmethod
    def size() -> int:
        """Count of configured keys — safe for /health (never key material)."""
        return len(get_settings().gemini_api_keys)

    @staticmethod
    def any_configured() -> bool:
        return bool(get_settings().gemini_api_keys)

    @staticmethod
    def all_exhausted() -> bool:
        """True when every pool position is memoised-blocked."""
        pool = get_settings().gemini_api_keys
        return bool(pool) and not any(
            available(i) for i in range(len(pool))
        )

    # -- rotation state ------------------------------------------------
    @staticmethod
    def available_positions() -> list[tuple[int, str]]:
        """``(position, key)`` pairs still allowed to be used, in order.

        Memoised-exhausted keys are skipped so the gateway never issues
        a request Google will only reject again (zero wasted quota).
        """
        return [
            (position, key)
            for position, key in enumerate(get_settings().gemini_api_keys)
            if available(position)
        ]

    @staticmethod
    def mark_exhausted(position: int, exc: Exception) -> None:
        """Memoise a 429 for one pool position (Google's own window)."""
        wait = min(quota_wait_seconds(exc) or 600, 24 * 3600)
        _KEY_BLOCKED_UNTIL[position] = time.monotonic() + wait
        # Position + window only — never any key material.
        logger.warning(
            "key-pool #%d quota-limited — skipping for ~%ds", position + 1, wait
        )

    # -- failure contract ----------------------------------------------
    @staticmethod
    def raise_as_failure(exc: Exception, *, agent: str) -> AgentFailure:
        """Classify an upstream error into the agent failure contract."""
        kind = failure_kind(exc)
        raise AgentFailure(
            f"{agent} model call failed: {exc}", kind=kind, agent=agent
        ) from exc


def available(position: int) -> bool:
    """Is pool position #position past its memoised quota window?"""
    return time.monotonic() >= _KEY_BLOCKED_UNTIL.get(position, 0.0)
