"""Hassad AI — FastAPI backend.

Exposes::

    GET  /                  lightweight single-file test UI
    GET  /health            system verification (never leaks the key)
    POST /api/v1/analyze    upload a leaf image -> full supervisor report
    POST /api/v1/chat       one conversational turn with the agronomist

Run from the project root::

    uvicorn main:app --reload
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from agents import AgentError, ChatAgent, SupervisorAgent, Settings, get_settings

#: Resolved from THIS file, not the cwd, so the page loads even when uvicorn
#: is started from a different working directory.
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("hassad")

#: Failure kind -> HTTP status code.
KIND_TO_STATUS: dict[str, int] = {
    "input": 400,
    "auth": 500,
    "quota": 429,
    "busy": 503,
    "upstream": 502,
    "api": 502,
}


def _validation_message(exc: RequestValidationError) -> str:
    """Turn a pydantic error list into one readable, farmer-facing sentence.

    FastAPI's raw form is a list of dicts with machine locs; the frontend
    should never have to render that.
    """
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "body"]
        where = ".".join(loc) or "request"
        message = str(err.get("msg", "is invalid"))
        for prefix in ("Value error, ", "Assertion failed, "):
            message = message.removeprefix(prefix)
        return f"{where}: {message}"
    return "Invalid request."


# ---------------------------------------------------------------------------
# Chat request/response contract
# ---------------------------------------------------------------------------


class ChatTurn(BaseModel):
    """One replayed turn of conversation history."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    """Body for ``POST /api/v1/chat``.

    ``diagnosis`` is optional: when a farmer chats right after analyzing an
    image, passing the diagnosis back lets the agronomist answer in the
    context of that specific plant rather than generically.
    """

    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    diagnosis: dict[str, Any] | None = None


def create_app(
    settings: Settings | None = None,
    supervisor: SupervisorAgent | None = None,
    chat_agent: ChatAgent | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    An explicit factory (rather than module-level wiring) lets tests inject
    isolated settings so they never read the live `.env` or spend real quota.
    """
    settings = settings or get_settings()
    supervisor = supervisor or SupervisorAgent(settings)
    chat_agent = chat_agent or ChatAgent(settings)

    app = FastAPI(
        title="Hassad AI",
        description="Multi-agent crop-leaf diagnosis and localized advisory for Tébessa, Algeria.",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """Serve the lightweight single-file test UI."""
        if not INDEX_HTML.is_file():
            raise HTTPException(status_code=404, detail="Test UI not found (static/index.html).")
        return FileResponse(INDEX_HTML, media_type="text/html; charset=utf-8")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, Any]:
        """System verification. Reports key *state* only — never the value."""
        return {
            "status": "ok",
            "service": "hassad-ai",
            "version": "1.0.0",
            "pipeline": "supervisor_v1",
            "agents": ["diagnostic", "advisor", "chat", "supervisor"],
            "region": "Tébessa, Algeria",
            "vision_model": settings.vision_model,
            "advisor_model": settings.advisor_model,
            "chat_model": settings.chat_model,
            "provider": "groq",
            "configured": settings.is_configured,
            "key_state": settings.masked_key,
            "max_upload_mb": round(settings.max_image_bytes / (1024 * 1024), 2),
        }

    @app.post("/api/v1/analyze", tags=["analysis"])
    async def analyze(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        """Run the full multi-agent pipeline on an uploaded leaf image."""
        declared = (file.content_type or "").split(";")[0].strip().lower() or None

        try:
            image_bytes = await file.read()
        except Exception as exc:  # pragma: no cover - transport level
            logger.exception("Failed to read upload")
            raise HTTPException(status_code=400, detail="Could not read the uploaded file.") from exc

        if not image_bytes:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

        if len(image_bytes) > settings.max_image_bytes:
            limit_mb = round(settings.max_image_bytes / (1024 * 1024), 2)
            raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {limit_mb} MB.")

        try:
            report = supervisor.run(image_bytes, filename=file.filename, declared_mime=declared)
        except AgentError as exc:
            logger.error("Supervisor raised AgentError: %s", exc.message)
            return JSONResponse(status_code=KIND_TO_STATUS.get(exc.kind, 500), content={"ok": False, "error": exc.to_dict()})
        except Exception as exc:
            logger.exception("Unhandled pipeline failure")
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": {"kind": "api", "message": "Internal pipeline failure.", "retryable": False, "detail": str(exc)[:160]}},
            )

        if not report.get("ok", False):
            error = report.get("error") or {}
            kind = error.get("kind", "api")
            logger.warning("Pipeline failed [%s]: %s", kind, error.get("message"))
            return JSONResponse(status_code=KIND_TO_STATUS.get(kind, 500), content=report)

        return JSONResponse(status_code=200, content=report)

    @app.post("/api/v1/chat", tags=["chat"])
    def chat(payload: ChatRequest) -> JSONResponse:
        """Answer one farmer question as a local agronomist.

        Deliberately declared as a plain ``def`` rather than ``async def``: the
        Groq call is blocking, so FastAPI runs this in its threadpool instead
        of stalling the event loop — other requests (including an in-flight
        image upload) keep being served while an answer is generated.
        """
        result = chat_agent.ask(
            payload.message,
            history=[turn.model_dump() for turn in payload.history],
            diagnosis=payload.diagnosis,
        )

        if not result.ok:
            error = (result.error or AgentError("api", "Chat failure.")).to_dict()
            kind = error.get("kind", "api")
            logger.warning("Chat failed [%s]: %s", kind, error.get("message"))
            return JSONResponse(status_code=KIND_TO_STATUS.get(kind, 500), content={"ok": False, "error": error})

        return JSONResponse(status_code=200, content=result.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Return validation failures in the SAME envelope as every other error.

        FastAPI's default is `{"detail": [...]}` — a second error shape the
        chat UI would otherwise have to special-case. With this, a client can
        render `body.error.message` uniformly for API failures and bad input
        alike, and `error.kind == "input"` marks it as the caller's problem
        rather than something to retry.
        """
        logger.info("Validation rejected %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "error": {
                    "kind": "input",
                    "message": _validation_message(exc),
                    "retryable": False,
                },
            },
        )

    return app


#: Default ASGI app used by `uvicorn main:app`.
app = create_app()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
