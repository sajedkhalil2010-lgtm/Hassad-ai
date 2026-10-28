"""Hassad AI — FastAPI backend.

Exposes::

    GET  /                  lightweight single-file test UI
    GET  /health            system verification (never leaks the key)
    POST /api/v1/analyze    upload a leaf image -> full supervisor report
    POST /api/v1/chat       one conversational turn with the agronomist
    POST /api/register      create an account, returns a session token
    POST /api/login         verify credentials, returns a session token
    POST /api/logout        revoke the calling token
    GET  /api/me            who the calling token belongs to
    GET  /api/admin/users   every registered account (admin only)

Auth runs *alongside* the analysis endpoints rather than in front of them:
``/api/v1/analyze`` and ``/api/v1/chat`` still work without a token. Enforcing
a session there is a one-line dependency change once the UI is ready for it.

Run from ``backend/`` (or use ``start_stable.bat`` from the project root)::

    uvicorn main:app
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from agents import AgentError, ChatAgent, SupervisorAgent, Settings, get_settings
from auth import AuthStore, build_auth_router

#: Resolved from THIS file, never the cwd: the frontend lives outside the
#: backend package (one level up), so the page and its assets load no matter
#: where uvicorn was started from.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"
TRAILER_HTML = FRONTEND_DIR / "trailer.html"

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

#: The mirror of KIND_TO_STATUS: status code -> the `kind` the transport
#: layer already speaks. Declared separately rather than inverted at runtime
#: because a status can legitimately map to several kinds on the way out
#: (400 is both "bad upload" and "bad body"), but must mean exactly one
#: thing on the way back.
STATUS_TO_KIND: dict[int, str] = {
    400: "input",
    404: "input",
    405: "input",
    413: "input",
    415: "input",
    401: "auth",
    403: "auth",
    429: "quota",
    500: "api",
    502: "upstream",
    503: "busy",
    504: "upstream",
}

#: Codes where retrying is the caller's correct next move: nothing was wrong
#: with the request, only this attempt. Everything else under 500 is the
#: caller's problem, and telling a farmer to "try again" would loop them.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


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
    #: Optional response-language override (``ar`` / ``fr`` / ``en``). When it
    #: is absent or unusable, the language is detected from ``message``, so the
    #: farmer is always answered in the language they wrote in.
    language: str | None = None


def create_app(
    settings: Settings | None = None,
    supervisor: SupervisorAgent | None = None,
    chat_agent: ChatAgent | None = None,
    auth_store: AuthStore | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    An explicit factory (rather than module-level wiring) lets tests inject
    isolated settings so they never read the live `.env` or spend real quota.
    ``auth_store`` exists for the same reason: a test must be able to point
    auth at a throwaway SQLite file instead of the developer's real accounts.
    """
    settings = settings or get_settings()
    supervisor = supervisor or SupervisorAgent(settings)
    chat_agent = chat_agent or ChatAgent(settings)
    auth_store = auth_store or AuthStore.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Deferred to startup rather than run inline here: constructing an
        # app must not touch the filesystem, so that merely importing this
        # module (as the test suite does) cannot create a database file.
        auth_store.init_schema()
        yield

    app = FastAPI(
        title="Hassad AI",
        description="Multi-agent crop-leaf diagnosis and localized advisory for Tébessa, Algeria.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.include_router(build_auth_router(auth_store, settings))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Serve the split frontend assets (css/js) at /assets/*. index.html is
    # returned by the explicit "/" route below with a pinned charset; this
    # mount handles everything else. Mounting the directory delegates
    # path-traversal protection to Starlette instead of hand-rolling it.
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="frontend")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """Serve the lightweight single-file test UI."""
        if not INDEX_HTML.is_file():
            raise HTTPException(status_code=404, detail="Test UI not found (frontend/index.html).")
        return FileResponse(INDEX_HTML, media_type="text/html; charset=utf-8")

    # A standalone presentation page, deliberately outside the product: it loads
    # none of the app's modules and talks to no API. Kept on its own path so it
    # can be opened on a projector without the workspace's state anywhere near
    # it. The page itself also resolves at /assets/trailer.html via the mount.
    @app.get("/trailer", include_in_schema=False)
    def trailer() -> FileResponse:
        """Serve the self-contained cinematic trailer page."""
        if not TRAILER_HTML.is_file():
            raise HTTPException(status_code=404, detail="Trailer not found (frontend/trailer.html).")
        return FileResponse(TRAILER_HTML, media_type="text/html; charset=utf-8")

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
    async def analyze(
        request: Request,
        file: UploadFile = File(...),
        language: str | None = Form(None),
    ) -> JSONResponse:
        """Run the full multi-agent pipeline on an uploaded leaf image.

        ``language`` is optional and lenient: an unreadable value is ignored
        rather than rejected, because a farmer who cannot analyse their leaf
        because they spelled the locale wrong is worse than one who gets the
        answer in the default script.
        """
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
            # Off the event loop, for the reason the chat endpoint below is a
            # plain ``def``: ``supervisor.run`` blocks for as long as the
            # vision and advisory calls take. Awaiting it here would stall the
            # loop, and EVERY request behind it — the stylesheet, the health
            # check, the next farmer's upload — waits out this one analysis
            # first. ``file.read()`` above still needs an ``await``, so the
            # upload stays async and only the pipeline moves to the threadpool.
            report = await run_in_threadpool(
                supervisor.run,
                image_bytes,
                filename=file.filename,
                declared_mime=declared,
                language=language,
            )
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
            language=payload.language,
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

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Wrap every HTTPException in the SAME envelope as a validation error.

        FastAPI's default body is ``{"detail": "..."}``, and ``api.js`` reads
        ``payload.error.message`` — so with the default every message written
        here was silently discarded and the farmer saw
        ``Request failed (HTTP 400)`` instead of *"The uploaded file is
        empty."* or *"File too large. Maximum size is 10.0 MB."* The two
        failure shapes existed on purpose for the validation path above;
        this brings the rest of them into line.

        Registered against the **Starlette** base class rather than
        ``fastapi.HTTPException`` because that is what the exception
        middleware resolves by walking the MRO — and Starlette is what
        ``StaticFiles`` and the router's own not-found raise. Registering on
        the subclass alone would leave every 404 and 405 on the old shape.
        """
        status = exc.status_code
        detail = exc.detail
        message = detail if isinstance(detail, str) else str(detail)
        logger.info(
            "HTTP %s for %s %s: %s", status, request.method, request.url.path, message
        )
        return JSONResponse(
            status_code=status,
            # Preserved, not dropped: WWW-Authenticate on a 401 and friends
            # are part of the contract this envelope rides on.
            headers=getattr(exc, "headers", None),
            content={
                "ok": False,
                "error": {
                    "kind": STATUS_TO_KIND.get(status, "api" if status >= 500 else "input"),
                    "message": message,
                    "retryable": status in RETRYABLE_STATUS,
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
