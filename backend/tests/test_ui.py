"""Smoke test for the dashboard UI served at '/'.

Covers three separate things, because they fail for different reasons:
the page markup, the static assets and their routing, and the design system
itself (so a palette or typeface change is caught rather than shipped).
"""

from __future__ import annotations

import re
import sys

PASS = 0
FAIL = 0

#: ES modules the page loads. app.js is the entry; it imports the rest.
MODULES = ("api.js", "dom.js", "auth.js", "diagnose.js", "chat.js",
           "i18n.js", "history.js", "app.js")

#: Markers of a Google *identity* integration. Deliberately not the bare word
#: "google": the typefaces are served from fonts.googleapis.com, which is a
#: font CDN and not a sign-in flow. Asserting on "google" would conflate the
#: two, which is exactly how a real sign-in could sneak back in unnoticed.
GOOGLE_SIGNIN_MARKERS = (
    "accounts.google.com",
    "gapi",
    "gsi/client",
    "oneTap",
    "google_auth",
    "id_token",
)


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
    check("declares utf-8 charset meta", '<meta charset="utf-8">' in html)
    check("links the stylesheet", "/assets/css/styles.css" in html)
    check("links the script", "/assets/js/app.js" in html)
    check("stylesheet kept out of the document", "<style" not in html)
    check("entry is an ES module, not a classic script", 'type="module"' in html)

    # The document may carry EXACTLY ONE inline script: the safety net at the
    # bottom of <body> that wires the primary controls when the module graph
    # never arrives (an import 404, a syntax error, a rejected file — all of
    # which leave every boot() listener silently absent). Asserting the COUNT
    # rather than dropping the rule keeps a second inline block from creeping
    # in unnoticed, and the guards below pin what that one block may be:
    # wrapped so it cannot throw, deferred past the module, standing down
    # once app.js has booted, and binding by listener rather than attribute.
    inline = re.findall(r"<script>(.*?)</script>", html, re.S)
    check("exactly one inline safety-net script", len(inline) == 1,
          f"found {len(inline)}")
    if inline:
        net = inline[0]
        check("safety net cannot throw out of its own wrapper",
              "try {" in net and "catch" in net)
        check("safety net stands down once app.js has booted",
              "data-app-ready" in net)
        check("safety net defers to DOMContentLoaded (modules run first)",
              "DOMContentLoaded" in net)
        check("safety net binds by listener, not by onclick attribute",
              "onclick=" not in net)
        try:
            import esprima

            esprima.parseScript(net)
            check("safety net parses", True)
        except Exception as exc:  # noqa: BLE001 - the message is the finding
            check("safety net parses", False, str(exc))
    check("no external CDN/dependencies", "http://" not in html.replace("http://127.0.0.1", "")
          and "https://cdn" not in html and "//cdn" not in html)

    print("\n=== Workspace structure ===")
    check("has drag-and-drop zone", 'id="dropzone"' in html)
    check("has local preview image", 'id="preview"' in html)
    check("has image-clear control", 'id="clear-image"' in html)
    check("has results grid", 'id="summary"' in html)
    check("has symptoms list", 'id="symptoms"' in html)
    check("has guidance container", 'id="advisory-body"' in html)
    # The debug raw-response disclosure was removed: a farmer reading the
    # structured grid gains nothing from a JSON dump of the same payload, and
    # it pushed the guidance card up out of view. Asserting its ABSENCE stops
    # the debug furniture coming back unnoticed.
    check("debug raw-response viewer is gone",
          'id="diagnosis"' not in html and 'id="supervisor"' not in html
          and "الردود الخام" not in html)
    check("no dead disclosure shell left behind", "<details" not in html)

    print("\n=== Assistant panel ===")
    check("has message log", 'id="messages"' in html)
    check("has composer", 'id="chat-form"' in html)
    check("has message input", 'id="chat-input"' in html)
    check("has send button", 'id="chat-send"' in html)
    check("has suggestion chips region", 'id="suggestions"' in html)
    check("has clear-conversation control", 'id="chat-reset"' in html)
    check("log is an aria-live region", 'role="log"' in html)

    print("\n=== Account panel ===")
    check("has account dialog", 'id="auth-modal"' in html)
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
    leaked = [m for m in GOOGLE_SIGNIN_MARKERS if m.lower() in lowered]
    check("no Google sign-in integration", not leaked, f"found {leaked}")
    check("passwords are not auto-submitted insecurely", 'autocomplete="current-password"' in html)

    print("\n=== Landing page + shell ===")
    # The page is one document with two stages. Both must be present in the
    # served markup: the landing is what a visitor sees, and the dashboard
    # must already be in the DOM (hidden, not unmounted) so its module wiring
    # survives the transition without a reload.
    check("landing stage present", 'id="landing"' in html)
    check("dashboard stage present", 'id="app"' in html)
    check("Arabic is the default direction", '<html lang="ar" dir="rtl">' in html)
    check("landing hero title present", 'class="hero__title"' in html)
    check("landing product mockup present", 'class="mock"' in html)
    check("sidebar navigation present", 'class="sidebar"' in html)
    check("metrics grid present", 'id="metric-total"' in html)
    check("recent diagnoses list present", 'id="recent-list"' in html)
    check("floating assistant dialog present", 'id="chat-modal"' in html)
    check("guest route offered (auth is never a toll gate)", 'id="auth-guest"' in html)
    check("no inline event handlers", " onclick=" not in html and " onload=" not in html)

    print("\n=== Typefaces ===")
    # Requested faces, loaded as progressive enhancement: display=swap means
    # text paints immediately in the fallback stack, so a slow or absent
    # connection costs the typeface and nothing else.
    check("requests Tajawal", "Tajawal" in html)
    check("requests IBM Plex Sans Arabic", "IBM+Plex+Sans+Arabic" in html)
    check("font stylesheet uses display=swap", "display=swap" in html)
    check("preconnects to the font host", 'rel="preconnect"' in html)
    # The document must still be fully readable if those two links never load.
    check("html declares a fallback font stack", "font-family" in client.get("/assets/css/styles.css").text)

    print("\n=== Design system (css) ===")
    css = client.get("/assets/css/styles.css")
    check("GET /assets/css/styles.css -> 200", css.status_code == 200, f"got {css.status_code}")
    check("stylesheet is text/css", "text/css" in css.headers.get("content-type", ""))
    check("stylesheet non-empty", len(css.text) > 2000, f"{len(css.text)} chars")
    for token, label in (
        # The palette IS the design, so it is asserted rather than implied:
        # an obsidian field (never #000 — pure black makes the emerald edges
        # read as if floating in a void), an eerie-green panel, a neon
        # emerald signal ramp, and light ink.
        ("#070f0c", "obsidian field"),
        ("#0a1410", "eerie green panel"),
        ("#10b981", "emerald signal"),
        ("#22c55e", "neon emerald"),
        ("#e8f2ee", "light ink"),
    ):
        check(f"palette includes {label} ({token})", token in css.text)
    # Scoped to the --bg token on purpose: the PRINT block deliberately sets
    # black text on white paper, which is a different medium, not a palette
    # lapse.
    check("screen field is not pure black", not re.search(r"--bg:\s*#0{3,6}\b", css.text))
    check("defines Tajawal for Arabic", "Tajawal" in css.text)
    check("defines IBM Plex Sans Arabic", "IBM Plex Sans Arabic" in css.text)
    # Dark is the design, not a mode: the field token sets it outright. Assert
    # that rather than a media query, which no longer applies.
    check("field is dark by design, not by media query",
          "--bg:" in css.text and "#070f0c" in css.text
          and "background: var(--bg)" in css.text)
    # The light theme is real, but it must not be able to reach the workspace:
    # it is gated to .landing only, so the tested dark workspace cannot be
    # silently given a second variant nobody maintains.
    check("light theme is scoped to the landing page",
          'body[data-theme="light"] .landing' in css.text
          and not re.search(r'body\[data-theme="light"\]\s+\.app\b', css.text))
    check("glass surfaces are translucent", "backdrop-filter" in css.text)
    check("honours reduced motion", "prefers-reduced-motion: reduce" in css.text)
    check("responds to narrow viewports", "@media (max-width" in css.text)
    check("no CSS framework imported", "@import" not in css.text and "tailwind" not in css.text.lower())

    print("\n=== Modules under /assets/js ===")
    modules = {}
    for name in MODULES:
        res = client.get(f"/assets/js/{name}")
        check(f"GET /assets/js/{name} -> 200", res.status_code == 200, f"got {res.status_code}")
        check(f"{name} served as javascript",
              "javascript" in res.headers.get("content-type", ""),
              f"got {res.headers.get('content-type')}")
        modules[name] = res.text

    # Assertions about the app as a whole rather than one file: splitting into
    # modules must not let a guarantee quietly evaporate into a file the test
    # no longer reads.
    agg = "\n".join(modules.values())

    for endpoint in ("/api/v1/analyze", "/api/v1/chat",
                     "/api/register", "/api/login", "/api/logout", "/api/me"):
        check(f"modules call {endpoint}", endpoint in agg)

    check("script persists the token", "localStorage" in agg)
    check("script sends the bearer token", "Authorization" in agg)
    check("script restores the session on load", "restoreSession" in agg)
    check("script submits image as multipart", "FormData" in agg)
    check("script keeps chat history", "history" in agg)
    check("script passes diagnosis context to chat", "getContext" in agg)
    check("no Google endpoint in the modules",
          not [m for m in GOOGLE_SIGNIN_MARKERS if m.lower() in agg.lower()])

    # `innerHTML` may only ever be assigned the empty string (clearing a
    # container). Asserting on the values rather than on absence is
    # deliberate: it still passes if someone clears a div a different way,
    # but fails the moment any user- or model-supplied text is rendered
    # through it — an XSS hole.
    assignments = re.findall(r"innerHTML\s*=\s*([^;]+)", agg)
    offenders = [a for a in assignments if a.strip() != '""']
    check("no innerHTML assignment writes content", not offenders, f"innerHTML <- {offenders}")
    check("dynamic text is rendered with textContent", "textContent" in agg)

    print("\n=== Routing guards ===")
    check("unknown asset -> 404", client.get("/assets/nope.css").status_code == 404)
    check("unknown module -> 404", client.get("/assets/js/nope.js").status_code == 404)
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
