"""Smoke test for the lightweight test UI served at '/'."""

from __future__ import annotations

import re
import sys

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def main() -> int:
    from fastapi.testclient import TestClient

    from agents.config import Settings
    from main import INDEX_HTML, create_app

    isolated = Settings(_env_file=None, groq_api_key=None)
    client = TestClient(create_app(isolated))

    print("\n=== Test UI at '/' ===")
    check("frontend/index.html exists on disk", INDEX_HTML.is_file(), str(INDEX_HTML))

    r = client.get("/")
    check("GET / -> 200", r.status_code == 200, f"got {r.status_code}")
    check("served as html", "text/html" in r.headers.get("content-type", ""))
    check("charset utf-8 declared", "utf-8" in r.headers.get("content-type", ""))

    html = r.text
    check("has file input", 'type="file"' in html)
    check("has Analyze button", ">Analyze<" in html)
    check("has diagnosis container", 'id="diagnosis"' in html)
    check("has advisory container", 'id="advisory"' in html)
    check("has supervisor container", 'id="supervisor"' in html)
    check("declares utf-8 charset meta", '<meta charset="utf-8">' in html)
    check("links the stylesheet", "/assets/css/styles.css" in html)
    check("links the script", "/assets/js/app.js" in html)
    check("stylesheet kept out of the document", "<style" not in html)
    check("script kept out of the document", "<script>" not in html)
    check("no external CDN/dependencies", "http://" not in html.replace("http://127.0.0.1", "")
          and "https://cdn" not in html and "//cdn" not in html)

    print("\n=== Account panel ===")
    check("has account card", 'id="auth-card"' in html)
    check("has email input", 'id="auth-email"' in html)
    check("has password input", 'id="auth-password"' in html)
    check("has sign-in tab", 'id="tab-login"' in html)
    check("has register tab", 'id="tab-register"' in html)
    check("has submit button", 'id="auth-submit"' in html)
    check("has signed-in view", 'id="auth-signed-in"' in html)
    check("has sign-out button", 'id="auth-logout"' in html)
    check("has status region", 'id="auth-status"' in html)
    lowered = html.lower()
    check("no Google sign-in button (deliberately removed)",
          "google" not in lowered and "gapi" not in lowered, "google/gapi found in html")
    check("passwords are not auto-submitted insecurely", 'autocomplete="current-password"' in html)

    print("\n=== Split assets under /assets ===")
    css = client.get("/assets/css/styles.css")
    check("GET /assets/css/styles.css -> 200", css.status_code == 200, f"got {css.status_code}")
    check("stylesheet is text/css", "text/css" in css.headers.get("content-type", ""))
    check("stylesheet non-empty", len(css.text) > 500, f"{len(css.text)} chars")

    js = client.get("/assets/js/app.js")
    check("GET /assets/js/app.js -> 200", js.status_code == 200, f"got {js.status_code}")
    check("script is javascript", "javascript" in js.headers.get("content-type", ""))
    check("script posts to /api/v1/analyze", "/api/v1/analyze" in js.text)
    check("script non-empty", len(js.text) > 500, f"{len(js.text)} chars")

    print("\n=== Account wiring in the script ===")
    for endpoint in ("/api/register", "/api/login", "/api/logout", "/api/me"):
        check(f"script calls {endpoint}", endpoint in js.text)
    check("script persists the token", "localStorage" in js.text)
    check("script sends the bearer token", "Authorization" in js.text)
    check("script restores the session on load", "/api/me" in js.text and "restore" in js.text)
    # `innerHTML` is used only to CLEAR a container (`= ""`). Asserting on the
    # values rather than on absence is deliberate: it still passes if someone
    # clears a div a different way, but fails the moment any user- or
    # model-supplied text is rendered through it — an XSS hole.
    assignments = re.findall(r"innerHTML\s*=\s*([^;]+)", js.text)
    check("user text is rendered with textContent, never innerHTML",
          bool(assignments) and all(a.strip() == '""' for a in assignments),
          f"innerHTML assigned: {assignments}")
    check("no Google endpoint in the script",
          "google" not in js.text.lower() and "gapi" not in js.text.lower())

    check("unknown asset -> 404", client.get("/assets/nope.css").status_code == 404)
    check("traversal does not expose backend source",
          client.get("/assets/../main.py").status_code != 200)

    print("\n=== Other endpoints unaffected ===")
    h = client.get("/health")
    check("GET /health still 200", h.status_code == 200, f"got {h.status_code}")
    check("/health reports key state only", "key_state" in h.json())
    check("GET /docs still 200", client.get("/docs").status_code == 200)
    check("openapi still 200", client.get("/openapi.json").status_code == 200)
    check("root excluded from openapi schema",
          "/" not in client.get("/openapi.json").json().get("paths", {}))

    # Empty / non-image uploads must still be rejected, not swallowed by the UI route.
    check("empty upload -> 400",
          client.post("/api/v1/analyze", files={"file": ("x.png", b"", "image/png")}).status_code == 400)
    check("non-image -> 400",
          client.post("/api/v1/analyze", files={"file": ("x.png", b"nope", "image/png")}).status_code == 400)

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
