#!/usr/bin/env python3
"""Hassad AI — one-click local launcher.

Starts the FastAPI/Uvicorn backend on an available port and prints the exact
URL to open.

    python run.py            # auto-pick a free port, starting at 8000
    python run.py --port 8000
    python run.py --no-open  # don't launch the browser

Why this file exists
--------------------
It pins the process to THIS project directory so that sibling checkouts on the
machine (for example other Hassad folders on the Desktop) can never be
imported by accident. Python resolves ``import agents`` / ``import main``
against ``sys.path``; without pinning, a stray folder named ``agents`` or
``main`` on the path would shadow ours silently.
"""

from __future__ import annotations

import argparse
import importlib.util
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# The project lives in a folder whose name is non-ASCII, and this launcher
# prints PROJECT_ROOT. A Windows console defaults to a legacy codepage
# (cp1252) that cannot encode it, so the moment output is redirected to a
# file or pipe the launcher dies with UnicodeEncodeError on a *print*.
# Force UTF-8, and degrade to replacement characters rather than ever
# aborting startup over a log line.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - exotic streams (frozen exe, etc)
        pass

#: Anything above this counts as "the server never came up".
STARTUP_TIMEOUT_S = 30.0

REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic_settings", "groq", "PIL")


# ---------------------------------------------------------------------------
# Environment hygiene
# ---------------------------------------------------------------------------


def pin_to_project_root() -> None:
    """Make THIS directory the only source of ``agents`` / ``main`` imports."""
    # Drop anything that could shadow us, then put the project root first.
    sys.path = [p for p in sys.path if Path(p or ".").resolve() != PROJECT_ROOT]
    sys.path.insert(0, str(PROJECT_ROOT))

    # Tell site.py / user site not to prepend other locations we don't control.
    import site

    site.addsitedir(str(PROJECT_ROOT))


def verify_no_shadowing() -> list[str]:
    """Return a list of human-readable problems if imports could be hijacked."""
    problems: list[str] = []

    for module_name, expected_file in (("main", "main.py"), ("agents", None)):
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            problems.append(f"module '{module_name}' not found under {PROJECT_ROOT}")
            continue
        origin = Path(spec.origin or (spec.submodule_search_locations or [""])[0]).resolve()
        if PROJECT_ROOT not in origin.parents and origin != PROJECT_ROOT:
            problems.append(f"module '{module_name}' resolves OUTSIDE this project: {origin}")

    # The installed third-party SDK must not shadow our package name.
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            problems.append(f"missing dependency '{name}': {exc}")

    return problems


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def check_dependencies() -> bool:
    missing: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)

    if not missing:
        print(f"[1/5] Dependencies ......... OK ({len(REQUIRED_MODULES)} packages)")
        return True

    print(f"[1/5] Dependencies ......... MISSING: {', '.join(missing)}")
    print(f"      Install them with:  {sys.executable} -m pip install -r requirements.txt")
    return False


def check_key() -> tuple[bool, str]:
    """Report key state. Never prints the key."""
    try:
        from agents.config import get_settings

        settings = get_settings()
    except Exception as exc:  # pragma: no cover - config unreadable
        return False, f"could not load settings: {exc}"

    if settings.is_configured:
        return True, f"configured (masked {settings.masked_key}, vision {settings.vision_model})"
    return False, "NOT SET — .env still holds the placeholder. /api/v1/analyze will return auth error."


def check_models() -> tuple[bool, str]:
    """Probe Groq's live model list for the two configured ids.

    Guards against a retired model id — the exact failure that otherwise
    costs a full debugging cycle to discover. Model docs go stale:
    `llama-3.3-70b-versatile` was still listed as a Production model on
    Groq's own site after they had stopped serving it, so the only
    trustworthy validation is asking the API what it will actually run.

    Best-effort by design. Offline, rate-limited or auth problems degrade to
    a WARNING rather than blocking startup: the advisor has a local-KB
    fallback, and the server is still useful for the UI and diagnostics.
    """
    try:
        from agents.config import get_settings

        settings = get_settings()
    except Exception as exc:  # pragma: no cover - config unreadable
        return False, f"could not load settings: {exc}"

    if not settings.is_configured:
        return False, "skipped (GROQ_API_KEY not configured)"

    try:
        from groq import Groq

        client = Groq(api_key=settings.api_key, timeout=15.0, max_retries=0)
        available = {model.id for model in client.models.list().data}
    except Exception as exc:
        return False, f"could not verify ({type(exc).__name__}) — server still starts"

    wanted = (
        ("vision", settings.vision_model),
        ("advisor", settings.advisor_model),
    )
    missing = [f"{label} {mid}" for label, mid in wanted if mid not in available]
    if missing:
        return False, (
            "NOT SERVED by Groq: " + ", ".join(missing)
            + "  -> edit agents/config.py (the API would return model-not-found)"
        )

    return True, f"vision {settings.vision_model} + advisor {settings.advisor_model}"


def is_port_free(host: str, port: int) -> bool:
    """Robustly probe whether `host:port` can be bound.

    On Windows, ``SO_REUSEADDR`` does NOT mean "the port is free" — it
    explicitly permits binding over an existing listener, which would make a
    naive probe report a busy port as free. Binding with
    ``SO_EXCLUSIVEADDRUSE`` (Windows-only) instead fails outright when
    anything else holds the port, which is exactly what we want to know.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass  # non-Windows or unsupported; plain bind still validates
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, preferred: int) -> int:
    """Return the first genuinely free port from `preferred` upward."""
    for port in range(preferred, preferred + 40):
        if is_port_free(host, port):
            return port
    raise RuntimeError(f"No free port found in {preferred}..{preferred + 39} on {host}.")


def port_owner(port: int) -> str:
    """Best-effort description of whatever is listening on `port` (Windows)."""
    try:
        import subprocess

        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000,
            encoding="utf-8", errors="replace",
        ).stdout
        pids: set[str] = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
                local = parts[1]
                if local.endswith(f":{port}") and ("127.0.0.1" in local or local.endswith(f"0.0.0.0:{port}")):
                    pids.add(parts[4])
        if not pids:
            return "unknown process"
        descriptions = []
        for pid in sorted(pids):
            try:
                proc = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10, creationflags=0x08000000,
                    encoding="utf-8", errors="replace",
                ).stdout.strip()
                descriptions.append(f"PID {pid} ({proc.split(',')[0].strip('\"')})")
            except Exception:
                descriptions.append(f"PID {pid}")
        return ", ".join(descriptions)
    except Exception:
        return "unknown process"


def wait_until_up(base_url: str, deadline_s: float = STARTUP_TIMEOUT_S) -> bool:
    """Poll /health until the server answers or we time out."""
    target = f"{base_url}/health"
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(target, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.3)
    return False


def open_browser(url: str, delay_s: float = 1.2) -> None:
    """Open the dashboard in the default browser without blocking startup."""

    def _worker() -> None:
        time.sleep(delay_s)
        import webbrowser

        webbrowser.open(url)

    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    # Line-buffer stdout/stderr so every status line is visible immediately,
    # even when output is redirected to a file or a `.bat` launches us.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description="Launch the Hassad AI backend.")
    parser.add_argument("--port", type=int, default=8000, help="Preferred port (default 8000).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser tab.")
    parser.add_argument("--open-docs", action="store_true", help="Open /docs instead of /health.")
    args = parser.parse_args()

    # Always operate from this directory, regardless of the caller's cwd.
    import os

    os.chdir(PROJECT_ROOT)
    pin_to_project_root()

    print("=" * 62)
    print("  HASSAD AI  —  backend launcher")
    print(f"  project : {PROJECT_ROOT}")
    print("=" * 62)

    if not check_dependencies():
        return 1

    problems = verify_no_shadowing()
    if problems:
        print("[2/5] Import isolation ...... FAILED")
        for problem in problems:
            print(f"      ! {problem}")
        return 1
    print(f"[2/5] Import isolation ...... OK (agents/main resolve inside this folder)")

    key_ok, key_detail = check_key()
    print(f"[3/5] Groq API key .......... {'OK' if key_ok else 'WARNING'}")
    print(f"      {key_detail}")

    models_ok, models_detail = check_models()
    print(f"[4/5] Model availability ... {'OK' if models_ok else 'WARNING'}")
    print(f"      {models_detail}")

    # Report the port we actually land on; if the preferred one is taken, say
    # by what, so the user is never confused about which server they reached.
    if not is_port_free(args.host, args.port):
        print(f"[5/5] Port {args.port} ........... BUSY ({port_owner(args.port)})")
        port = pick_port(args.host, args.port)
        base_url = f"http://{args.host}:{port}"
        print(f"      -> falling back to {base_url}")
    else:
        port = args.port
        base_url = f"http://{args.host}:{port}"
        print(f"[5/5] Port {args.port} ........... free")

    print(f"      Starting Uvicorn ...... {base_url}")

    def _serve() -> None:
        import uvicorn

        uvicorn.run("main:app", host=args.host, port=port, log_level="warning", access_log=False)

    threading.Thread(target=_serve, daemon=True).start()

    if not wait_until_up(base_url):
        print("\n  ! Server did not answer /health in time. Check the output above.")
        return 1

    docs_url = f"{base_url}/docs" if args.open_docs else base_url
    print("\n" + "-" * 62)
    print("  Backend is UP. Open one of these:")
    print(f"    Test UI      : {base_url}/")
    print(f"    Health check : {base_url}/health")
    print(f"    API docs     : {base_url}/docs")
    print(f"    Analyze POST : {base_url}/api/v1/analyze")
    print("-" * 62)
    if not key_ok:
        print("  NOTE: set GROQ_API_KEY in .env, then restart, before analyzing.")
    print("  Press Ctrl+C in this window to stop the server.\n")

    if not args.no_open:
        open_browser(docs_url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Hassad AI backend...")
        return 0


if __name__ == "__main__":
    sys.exit(main())
