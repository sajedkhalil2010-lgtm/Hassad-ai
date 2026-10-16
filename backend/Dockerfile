# ===================================================================
#  حَصاد AI — Hassad AI backend · Production image
# -------------------------------------------------------------------
#  SECURITY: only `requirements.txt` and `app/` are copied into the
#  image.  `.env` (API keys) and `*.db` are NEVER baked into a layer,
#  so keys cannot leak through `docker history` or an image registry.
#
#  Build:  docker build -t hassad-backend .
#  Run:    docker run --env-file .env -p 8000:8000 hassad-backend
# ===================================================================

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is only used by the HEALTHCHECK below
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# --- dependencies first (better layer caching) ----------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- application code ONLY (no .env, no secrets, no database) -------
COPY app ./app

# --- run as an unprivileged user ------------------------------------
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --- health check ----------------------------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

# --- runtime notes ---------------------------------------------------
#  * Pass secrets at run time:  docker run --env-file .env ...
#    (never COPY .env into the image)
#  * For PostgreSQL use:  DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
#  * Mount a volume over /app/data to persist the SQLite database.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
