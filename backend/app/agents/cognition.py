"""CognitionEngine — the single gateway between agents and model cognition.

**v3 invariant: this is the only file in the backend that imports
``google.genai``.** Agents never build clients, never assemble HTTP, and
never see an SDK type; they ask the engine for *reasoning* and get typed
results or a classified :class:`~app.agents.base.AgentFailure`. Key policy
(rotation windows, memo) lives in :mod:`app.agents.key_manager` — the
engine executes it.

Engine primitives:

* :meth:`CognitionEngine.structured_json` — one bounded,
  schema-enforced generation. Exactly **one** HTTP attempt per key; a 429
  rotates to the next key (memoising this one); any other failure
  raises immediately; a fully-spent pool raises instantly with **zero
  network calls**.
* :meth:`CognitionEngine.stream_text` — streamed generation under a hard
  total deadline with the same rotation rules; once text flows, a
  mid-stream failure just ends the iteration (the caller keeps what
  arrived).

Zero hidden retries: every client is built with
``HttpRetryOptions(attempts=0)`` — the SDK's default retries up to 4× on
429/5XX with backoff are exactly what we forbid.

No mock/fake cognition exists: every failure propagates so routers can
answer honest structured 429 (quota) / 503 (busy) errors.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentFailure, failure_kind, parse_json_response
from app.agents.key_manager import KeyPoolManager

logger = logging.getLogger("hassad.agents.cognition")


class CognitionEngine:
    """Stateless reasoning gateway over the rotating key pool."""

    # -- the ONLY SDK touchpoint in the backend ------------------------
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
    def config(
        schema: type[BaseModel] | None = None,
        *,
        temperature: float,
        max_tokens: int,
        top_p: float | None = None,
    ) -> Any:
        """Generation config — the last place SDK types exist.

        ``schema`` enforces rigorous JSON output; thinking is always
        disabled (free-tier flash otherwise burns its token budget on
        invisible reasoning).
        """
        from google.genai import types

        kwargs: dict[str, Any] = dict(
            temperature=temperature,
            max_output_tokens=max_tokens,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        if top_p is not None:
            kwargs["top_p"] = top_p
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def image_part(data: bytes, mime: str) -> Any:
        """Binary image payload part (encode off the event loop, then call)."""
        from google.genai import types

        return types.Part.from_bytes(data=data, mime_type=mime)

    # -- structured reasoning (vision / JSON tasks) ---------------------
    @classmethod
    async def structured_json(
        cls,
        *,
        model: str,
        contents: Any,
        config: Any,
        timeout: float,
        agent: str,
    ) -> dict[str, Any]:
        """One bounded, schema-validated reasoning pass with 429 rotation.

        Exactly one HTTP attempt per key; quota answers rotate through
        the pool; every other failure raises immediately. A fully-spent
        pool raises ``AgentFailure(kind="quota")`` instantly (zero
        network calls).
        """
        chain = KeyPoolManager.available_positions()
        if not KeyPoolManager.any_configured():
            raise AgentFailure("no model key configured", kind="busy", agent=agent)

        last_quota: Exception | None = None
        for position, key in chain:
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
                    KeyPoolManager.mark_exhausted(position, exc)
                    last_quota = exc
                    continue  # → next key in the pool (the rotation)
                KeyPoolManager.raise_as_failure(exc, agent=agent)
            try:
                return parse_json_response(response)
            except Exception as exc:  # noqa: BLE001 - malformed payload
                KeyPoolManager.raise_as_failure(exc, agent=agent)

        # Whole pool spent (429s or memoised): instant, zero-network.
        if last_quota is not None:
            KeyPoolManager.raise_as_failure(last_quota, agent=agent)
        raise AgentFailure(
            "all keys in the pool are quota-blocked (zero network calls made)",
            kind="quota",
            agent=agent,
        )

    # -- streamed reasoning (chat / advice) -----------------------------
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
        """Streamed reasoning under a hard total deadline + 429 rotation.

        Yields chunks; raises :class:`AgentFailure` when *no* key could
        start a stream. Once text is flowing, a mid-stream failure just
        ends the iteration (the caller keeps what was produced).
        """
        if not KeyPoolManager.any_configured():
            raise AgentFailure("no model key configured", kind="busy", agent=agent)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        last_quota: Exception | None = None

        for position, key in KeyPoolManager.available_positions():
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
                    KeyPoolManager.mark_exhausted(position, exc)
                    last_quota = exc
                    continue  # → next key in the pool
                KeyPoolManager.raise_as_failure(exc, agent=agent)

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
                    KeyPoolManager.raise_as_failure(
                        TimeoutError(f"{agent} stream empty after {budget:.0f}s"),
                        agent=agent,
                    )
                except Exception as exc:  # noqa: BLE001 - mid-stream failure
                    if sent:
                        return  # caller keeps the partial answer
                    if failure_kind(exc) == "quota":
                        KeyPoolManager.mark_exhausted(position, exc)
                        last_quota = exc
                        produced = None
                        break  # → next key in the pool
                    KeyPoolManager.raise_as_failure(exc, agent=agent)
                text = _chunk_text(chunk)
                if text:
                    sent += len(text)
                    yield text
            if sent:
                return
            # nothing produced on this key → fall through to rotation

        if last_quota is not None:
            KeyPoolManager.raise_as_failure(last_quota, agent=agent)
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


#: Process-wide engine (stateless — key memo lives in key_manager).
engine = CognitionEngine()
