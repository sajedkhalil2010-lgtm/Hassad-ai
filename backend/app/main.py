"""حَصاد AI — Hassad AI · FastAPI application factory.

Wires together:
* CORS + trusted-host + gzip middlewares and a pure-ASGI **security headers**
  middleware (adds ``X-Request-ID`` and never buffers, so SSE stays streaming).
* Global exception handlers: ``HTTPException`` (consistent shape + request id),
  ``RequestValidationError`` (422) and a catch-all 500 that logs the traceback
  but never leaks internals to the client.
* Startup lifespan: schema creation, knowledge-base seeding and the optional
  admin bootstrap — all driven by environment configuration, never hard-coded.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.v1 import agronomist, auth, chatbot, crop_doctor, sync
from app.core.config import get_settings
from app.core.database import (
    check_db_health,
    dispose_engine,
    get_session_factory,
    init_db,
)
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.agents import KeyPoolManager
from app.agents.knowledge import seed_knowledge_base

logger = logging.getLogger("hassad")

# ---- optional SPA frontend (sibling ``frontend/`` directory) -----------
# When present, ``/`` serves the Hassad AI web app and ``/assets`` serves
# its CSS/JS.  API routes and Swagger keep priority (registered first).
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# Headers applied to every response (added manually on 500s, where the
# outermost ServerErrorMiddleware sits above the middleware stack).
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(self)",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "X-DNS-Prefetch-Control": "off",
    "X-Permitted-Cross-Domain-Policies": "none",
}


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware: sets security headers + request id, no buffering.

    Implemented without ``BaseHTTPMiddleware`` so streaming responses (the RAG
    chatbot's Server-Sent Events) pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                request_id = value.decode("latin-1")[:120]
                break
        request_id = request_id or uuid.uuid4().hex

        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        is_https = scope.get("scheme") == "https"

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                existing = {name.lower() for name, _ in headers}
                extra: list[tuple[bytes, bytes]] = [
                    (name.encode("latin-1"), value.encode("latin-1"))
                    for name, value in SECURITY_HEADERS.items()
                ]
                extra.append((b"x-request-id", request_id.encode("latin-1")))
                if is_https:
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                for name, value in extra:
                    if name.lower() not in existing:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _error_payload(request: Request, detail: object, code: str) -> dict[str, object]:
    request_id = getattr(request.state, "request_id", None)
    payload: dict[str, object] = {"detail": jsonable_encoder(detail), "code": code}
    if request_id:
        payload["request_id"] = request_id
    return payload


def _status_code(code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "too_many_requests",
        502: "bad_gateway",
        503: "service_unavailable",
        504: "timeout",
    }.get(code, "error")


# ======================================================================
# Lifespan / bootstrap
# ======================================================================
async def _seed_admin_user(settings) -> None:  # noqa: ANN001 - Settings
    """Create the first admin account when SEED_ADMIN_* is configured."""
    email = (settings.SEED_ADMIN_EMAIL or "").strip().lower()
    password = settings.SEED_ADMIN_PASSWORD or ""
    if not email:
        return
    if len(password) < 8:
        logger.warning(
            "SEED_ADMIN_EMAIL is set but SEED_ADMIN_PASSWORD is missing or shorter than "
            "8 characters — admin account NOT created."
        )
        return

    async with get_session_factory()() as db:
        from sqlalchemy import select

        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            return
        db.add(
            User(
                email=email,
                hashed_password=hash_password(password),
                full_name="Administrator",
                role=UserRole.ADMIN.value,
                locale="ar",
            )
        )
        await db.commit()
        logger.info("Bootstrapped admin account for %s", email)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201 - FastAPI lifespan
    settings = get_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )
    logger.info("Starting %s v%s (%s)", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)

    if not settings.huggingface_configured:
        logger.warning(
            "HUGGINGFACE_API_KEY is not set — /crop-doctor/diagnose will answer 503. "
            "Paste a free token into .env."
        )
    if not settings.gemini_configured:
        logger.warning(
            "GEMINI_API_KEY is not set — the chatbot will serve retrieval-only fallback "
            "answers. Paste a free key into .env."
        )

    await init_db()
    async with get_session_factory()() as db:
        await seed_knowledge_base(db)
    await _seed_admin_user(settings)

    yield  # ---- application running ----

    await dispose_engine()
    logger.info("Shutdown complete")


# ======================================================================
# Application factory
# ======================================================================
def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        description=settings.APP_DESCRIPTION,
        version=settings.APP_VERSION,
        openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
        docs_url="/docs" if settings.ENABLE_DOCS else None,
        redoc_url="/redoc" if settings.ENABLE_DOCS else None,
        lifespan=lifespan,
    )

    # ---- middlewares (last added = outermost) ------------------------
    hosts = settings.allowed_hosts_list
    if hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ---- CORS: wide open for local testing & Swagger UI --------------
    # Any origin may call the API (localhost frontends, emulators, /docs).
    # Note: when credentials are enabled, Starlette mirrors the requesting
    # origin back instead of a literal "*", so this stays spec-compliant
    # even for credentialed browser requests.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # ---- exception handlers -----------------------------------------
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = dict(getattr(exc, "headers", None) or {})
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            headers.setdefault("X-Request-ID", request_id)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, exc.detail, _status_code(exc.status_code)),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_payload(request, exc.errors(), "validation_error"),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled error [request_id=%s]", request_id)
        headers = dict(SECURITY_HEADERS)
        if request_id:
            headers["X-Request-ID"] = request_id
        return JSONResponse(
            status_code=500,
            content=_error_payload(request, "Internal server error", "internal_error"),
            headers=headers,
        )

    # ---- routers -----------------------------------------------------
    prefix = settings.API_V1_PREFIX
    app.include_router(auth.router, prefix=prefix)
    app.include_router(crop_doctor.router, prefix=prefix)
    app.include_router(chatbot.router, prefix=prefix)
    app.include_router(agronomist.router, prefix=prefix)
    app.include_router(sync.router, prefix=prefix)

    # ---- static frontend assets (registered after API so they never
    #      shadow a route; unknown paths still fall through to FastAPI) ---
    _assets = FRONTEND_DIR / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    # ---- meta endpoints ----------------------------------------------
    @app.get("/", tags=["Meta"], include_in_schema=False)
    async def root():
        """Serve the Hassad AI web app when ``frontend/`` exists, else JSON."""
        index = FRONTEND_DIR / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return {
            "name": settings.APP_NAME,
            "tagline": "حَصاد AI",
            "version": settings.APP_VERSION,
            "api": prefix,
            "docs": "/docs" if settings.ENABLE_DOCS else "disabled",
        }

    @app.get("/health", tags=["Meta"])
    async def health() -> JSONResponse:
        """Liveness/readiness probe (never exposes secret values)."""
        from app.core.database import utcnow

        db_ok = await check_db_health()
        payload = {
            "status": "ok" if db_ok else "degraded",
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "database": "ok" if db_ok else "unreachable",
            "ai": {
                # Existence only — the key values are never returned.
                "huggingface_key_configured": settings.huggingface_configured,
                "gemini_key_configured": settings.gemini_configured,
                "gemini_keys_configured": len(settings.gemini_api_keys),  # count, not material
                "key_pool_size": KeyPoolManager.size(),  # v2 rotating pool (count only)
                "pipeline": "supervisor_v2",  # multi-agent architecture
                "agents": ["diagnostic", "advisor", "supervisor"],
                "vision_engine": "gemini" if settings.gemini_configured else "unconfigured",
                "vision_live_only": True,  # no mock verdicts — failures answer 429/503
                "vision_model": settings.vision_model_name,
                "vision_timeout_s": settings.VISION_TIMEOUT_SECONDS,
                "gemini_model": settings.GEMINI_MODEL_NAME,
            },
            "time": utcnow().isoformat(),
        }
        return JSONResponse(status_code=200 if db_ok else 503, content=payload)

    return app


app = create_app()
