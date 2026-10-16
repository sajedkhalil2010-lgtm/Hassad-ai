"""Uvicorn entry point — lets ``uvicorn main:app --reload`` boot instantly.

The real application lives in ``app.main`` (module ``app``); this thin shim
re-exports the ASGI app so the classic single-file command works from the
backend directory. Both commands are equivalent:

    uvicorn main:app --reload
    uvicorn app.main:app --reload
"""

from app.main import app

__all__ = ["app"]
