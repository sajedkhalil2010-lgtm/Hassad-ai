"""KeyPoolManager — smart multi-key management for every Gemini call.

V2 moved *all* key handling into this single component, used by both the
DiagnosticAgent (vision) and the AdvisorAgent (text/streaming):

* **Pool** — ``GEMINI_API_KEY_1..4`` first (operator's primary + backups
  in ``.env``), base ``GEMINI_API_KEY`` last; deduplicated; each field
  honours env-var > .env > default via pydantic-settings. Values are
  secrets: never logged, never repr'd, never returned to any client —
  ``/health`` sees only :meth:`KeyPoolManager.size`.
* **Rotation on 429** — a quota answer fails over to the next key.
  Only quota triggers rotation; any other failure raises immediately
  (one honest failure — never a blind retry loop).
* **Quota memo** — a spent key is skipped until Google's own reset
  window (parsed from the 429 payload) elapses; when the whole pool is
  spent, calls fail **instantly with zero network requests**, so
  background traffic cannot keep burning quota.
* **Zero SDK retries** — every client is built with
  ``HttpRetryOptions(attempts=0)``; the google-genai default retries
  429/5XX up to 4× with backoff, which is exactly what we forbid.

Every failure surfaces as :class:`~app.agents.base.AgentFailure` with
``kind="quota"|"busy"`` so routers can answer 429/503 honestly.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentFailure, failure_kind, parse_json_response
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


def _mark_exhausted(position: int, exc: Exception) -> None:
    wait = min(quota_wait_seconds(exc) or 600, 24 * 3600)
    _KEY_BLOCKED_UNTIL[position] = time.monotonic() + wait
    # Position + window only — never any key material.
    logger.warning("key-pool #%d quota-limited — skipping for ~%ds", position + 1, wait)


def _available(position: int) -> bool:
    return time.monotonic() >= _KEY_BLOCKED_UNTIL.get(position, 0.0)


class KeyPoolManager:
    """Rotating pool of Gemini keys with quota awareness (process-wide)."""

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
        return bool(pool) and not any(_available(i) for i in range(len(pool)))

    # -- clients ------------------------------------------------------
    @staticmethod
    def _client(key: str) -> Any:
        """Build a zero-retry client for one key (SDK auto-retries off)."""
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=0)
            ),
        )

    @staticmethod
    def _raise_as_failure(exc: Exception, *, agent: str) -> AgentFailure:
        kind = failure_kind(exc)
        raise AgentFailure(f"{agent} model call failed: {exc}", kind=kind, agent=agent) from exc

    # -- structured generation (vision / JSON tasks) -------------------
    @classmethod
    async def generate_json(
        cls,
        *,
        model: str,
        contents: Any,
        config: Any,
        timeout: float,
        agent: str,
    ) -> dict[str, Any]:
        """One bounded, schema-validated generation with 429 key rotation.

        Exactly **one** HTTP attempt per key; a quota answer rotates to
        the next key and memoises this one; other failures raise
        immediately. A fully-spent pool raises instantly (zero network).
        """
        pool = cls.pool()
        if not pool:
            raise AgentFailure(
                "no Gemini API key configured", kind="busy", agent=agent
            )

        last_quota: Exception | None = None
        for position, key in enumerate(pool):
            if not _available(position):
                continue  # memoised exhausted key — no wasted request
            client = cls._client(key)
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model, contents=contents, config=config
                    ),
                    timeout=timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — classified, never looped
                if failure_kind(exc) == "quota":
                    _mark_exhausted(position, exc)
                    last_quota = exc
                    continue  # → next key in the pool (the rotation)
                cls._raise_as_failure(exc, agent=agent)
            try:
                return parse_json_response(response)
            except Exception as exc:  # noqa: BLE001 - malformed payload
                cls._raise_as_failure(exc, agent=agent)
        # Whole pool spent (429s or memoised): instant, zero-network.
        if last_quota is not None:
            cls._raise_as_failure(last_quota, agent=agent)
        raise AgentFailure(
            "all keys in the pool are quota-blocked (zero network calls made)",
            kind="quota",
            agent=agent,
        )

    # -- streaming generation (chat / advice) --------------------------
    @classmethod
    async def stream_text(
        cls,
        *,
        model: str,
        contents: str,
        config: Any,
        budget: float,
        agent: str,
    ) -> AsyncIterator[str]:
        """Streamed generation with a hard deadline and 429 rotation.

        Yields chunks; raises :class:`AgentFailure` when *no* key could
        start a stream. Once text is flowing, a mid-stream failure just
        ends the iteration (the caller keeps what was produced).
        """
        pool = cls.pool()
        if not pool:
            raise AgentFailure("no Gemini API key configured", kind="busy", agent=agent)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        last_quota: Exception | None = None

        for position, key in enumerate(pool):
            if not _available(position):
                continue
            client = cls._client(key)
            produced: AsyncIterator[str] | None = None
            try:
                stream = await asyncio.wait_for(
                    client.aio.models.generate_content_stream(
                        model=model, contents=contents, config=config
                    ),
                    timeout=budget,
                )
                produced = stream
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — classified, never looped
                if failure_kind(exc) == "quota":
                    _mark_exhausted(position, exc)
                    last_quota = exc
                    continue  # → next key in the pool
                cls._raise_as_failure(exc, agent=agent)

            # Stream opened — pump chunks under the shared deadline.
            sent = 0
            while produced is not None:
                remaining = max(deadline - loop.time(), 0.05)
                try:
                    chunk = await asyncio.wait_for(produced.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if sent:
                        return  # keep what already reached the client
                    cls._raise_as_failure(
                        TimeoutError(f"{agent} stream empty after {budget:.0f}s"),
                        agent=agent,
                    )
                except Exception as exc:  # noqa: BLE001 - mid-stream failure
                    if sent:
                        return  # caller keeps the partial answer
                    if failure_kind(exc) == "quota":
                        _mark_exhausted(position, exc)
                        last_quota = exc
                        produced = None
                        break  # → next key in the pool
                    cls._raise_as_failure(exc, agent=agent)
                text = _chunk_text(chunk)
                if text:
                    sent += len(text)
                    yield text
            if sent:
                return
            # nothing produced on this key → fall through to rotation

        if last_quota is not None:
            cls._raise_as_failure(last_quota, agent=agent)
        raise AgentFailure(
            "all keys in the pool are quota-blocked (zero network calls made)",
            kind="quota",
            agent=agent,
        )


def _chunk_text(chunk: Any) -> str:
    """Safely extract text from a streamed chunk (may be empty/blocked)."""
    try:
        return chunk.text or ""
    except Exception:  # noqa: BLE001 - blocked or empty candidates raise here
        return ""
