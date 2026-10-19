"""Hassad AI — FastAPI backend.

Exposes::

    GET  /                  lightweight single-file test UI
    GET  /health            system verification (never leaks the key)
    POST /api/v1/analyze    upload a leaf image -> full supervisor report

Run from the project root::

    uvicorn main:app --reload
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from agents import AgentError, SupervisorAgent, Settings, get_settings

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


def create_app(settings: Settings | None = None, supervisor: SupervisorAgent | None = None) -> FastAPI:
    """Build the FastAPI application.

    An explicit factory (rather than module-level wiring) lets tests inject
    isolated settings so they never read the live `.env` or spend real quota.
    """
    settings = settings or get_settings()
    supervisor = supervisor or SupervisorAgent(settings)

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
            "agents": ["diagnostic", "advisor", "supervisor"],
            "region": "Tébessa, Algeria",
            "vision_model": settings.gemini_model,
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

    return app


#: Default ASGI app used by `uvicorn main:app`.
app = create_app()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
