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
           "i18n.js", "history.js", "pricing.js", "subscribe.js", "app.js")

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

    print("\n=== Pricing panel (demo) ===")
    # A price shown next to no checkout is a promise the app cannot keep,
    # so the demo framing has to live in the markup itself — not be
    # inferred from the absence of a payment form.
    check("pricing panel present", 'data-view-panel="pricing"' in html)
    check("pricing reachable from the sidebar",
          'data-panel-target="pricing"' in html)
    check("advertised as a demo", "pricing_demo" in html)
    for tier in ("free", "pro", "ent"):
        check(f"{tier} tier rendered", f'data-i18n="plan_{tier}_t"' in html)
        check(f"{tier} tier has a call to action",
              f'data-i18n="plan_{tier}_cta"' in html)
    check("every tier carries a status badge",
          all(f'data-i18n="plan_{tier}_badge"' in html for tier in ("free", "pro", "ent")))
    check("paid tiers open the notice, not a checkout",
          'data-plan="pro"' in html and 'data-plan="enterprise"' in html)
    check("free tier routes to the studio",
          'data-panel-target="studio"' in html)
    check("notice dialog present and wired",
          'id="plan-modal"' in html and '"plan-modal"' in modules["pricing.js"])
    check("notice uses the required copy",
          "early preview with free open access" in modules["i18n.js"])
    # The sign-up flow gained a payment step on purpose, so the old "no
    # payment form anywhere" rule no longer describes the product. What that
    # rule was protecting still has to hold, and is now asserted directly
    # rather than inferred from an absence: the step says it is a demo where
    # the reader can see it, and nothing in it can be posted anywhere.
    check("payment step is labelled a demo in the markup",
          'id="pay-form"' in html and "su_pay_demo" in html)
    check("no form has an action to post to",
          not re.search(r'<form[^>]*\baction\s*=', html, re.I))
    check("notice is not the only closed dialog",
          "Escape" in modules["pricing.js"])

    print("\n=== Sign-up handoff (demo checkout) ===")
    sub = modules["subscribe.js"]
    check("subscription dialog present", 'id="su-modal"' in html)
    check("registration is what opens it",
          'mode === "register"' in modules["auth.js"]
          and '"su-modal"' in sub)
    check("sign-in still goes straight to the workspace",
          "onRegistered" in modules["auth.js"]
          and "signup: pendingSignup" in modules["auth.js"])
    for ctrl in ("su-continue", "su-skip", "su-back", "su-submit"):
        check(f"control #{ctrl} exists", f'id="{ctrl}"' in html)
    check("all three tiers are offered", all(
        f'value="{tier}"' in html for tier in ("free", "pro", "enterprise")))
    check("card fields are present", all(
        f'id="{i}"' in html for i in
        ("pay-number", "pay-expiry", "pay-cvv", "pay-holder")))

    # The property the replaced guard existed to protect: nothing typed into
    # the card fields can leave the page. Asserted on the module directly —
    # it pulls in no HTTP helper and names no browser request API, so there
    # is no code path through which the digits could be transmitted.
    check("subscription module imports no HTTP helper",
          'from "./api.js"' not in sub)
    check("subscription module names no browser request API",
          not re.search(r"\b(fetch|sendBeacon|XMLHttpRequest|WebSocket)\b", sub))
    check("card fields are cleared rather than kept",
          'els.number.value = ""' in sub and 'els.holder.value = ""' in sub)
    check("card fields opt out of browser autofill", all(
        re.search(rf'id="{i}"[^>]*autocomplete="off"', html)
        for i in ("pay-number", "pay-expiry", "pay-cvv", "pay-holder")))
    check("the confirmation outlives the dialog that made it",
          'id="toast"' in html and '"toast"' in sub)
    check("either exit opens the workspace", re.search(
        r"initSubscribe\(\{\s*onFinish:\s*\(\)\s*=>\s*enterDashboard",
        modules["app.js"]) is not None)

    # The two screens that name a price were written from different briefs
    # and did drift — Enterprise read "Custom" on the pricing panel and "$49"
    # at sign-up. Asserting the same figure against both keys means the next
    # change has to be made twice on purpose, not once by accident.
    for tier, price in (("pro", "$9"), ("ent", "$49")):
        check(f"{tier} tier is priced the same on both surfaces",
              f'plan_{tier}_amount: "{price}"' in modules["i18n.js"]
              and f'su_{tier}_amt: "{price}"' in modules["i18n.js"])

    # A farmer has to recognise the link before they can use it, so the
    # sidebar names the section rather than only pointing at it.
    check("sidebar link names the section",
          "Pricing & Business Model" in modules["i18n.js"])

    # The feature lists are the terms being compared, not decoration —
    # assert the promises that distinguish each tier are actually shipped.
    for key, phrase in (
        ("plan_free_badge", "Current / Free"),
        ("plan_pro_badge", "Preview"),
        ("plan_free_f1", "each month"),
        ("plan_free_f2", "Sub-second"),
        ("plan_pro_f2", "Historical field diagnostic reports"),
        ("plan_pro_f3", "Priority cloud AI inference"),
        ("plan_ent_f1", "Customized seasonal contracts"),
        ("plan_ent_f2", "Multi-field monitoring"),
        ("plan_ent_f3", "Regional data API"),
        ("plan_ent_cta", "Contact sales"),
    ):
        check(f"{key} carries its promised value", phrase in modules["i18n.js"])

    # The badge is absolutely positioned by default and the widest one we
    # ship measures ~108px against a 74px corner reserve, so it would reach
    # back over the tier name. It now occupies a row of its own.
    flag_css = re.search(r"\.plan__flag\s*\{([^}]*)\}", css.text)
    check("badge sits in flow, clear of the tier name",
          bool(flag_css) and "position: absolute" not in flag_css.group(1))

    print("\n=== Upload preparation + honest progress ===")
    diag = modules["diagnose.js"]
    check("photos are re-encoded before upload",
          "createImageBitmap" in diag and "canvas.toBlob" in diag)
    check("the client resizes to the edge the backend keeps anyway",
          re.search(r"MAX_UPLOAD_SIDE\s*=\s*1600", diag) is not None)
    check("an encode can never make the upload larger",
          "blob.size >= file.size" in diag)
    check("EXIF orientation is applied, so re-encoding cannot rotate the leaf",
          'imageOrientation: "from-image"' in diag)
    check("anything that goes wrong falls back to the original file",
          diag.count("return file") >= 4)
    check("the encoder is awaited before the request leaves",
          "await preparing" in diag)
    check("the size under the preview shows what encoding saved",
          "→" in diag and "els.previewSize" in diag)
    # Progress is a number that keeps moving, not a label that sits still —
    # and it has to be torn down again or it repaints the outcome away.
    check("progress is elapsed time, not a frozen label",
          "setInterval" in diag and "clearInterval" in diag)
    check("the busy tone survives each tick",
          re.search(r'setStatus\(`\$\{label\}[^)]*"busy"', diag) is not None)
    # One definition, one call — and the call sits in the success branch of
    # analyze(). Nothing renders an object literal, so a request that fails
    # leaves the results panel untouched rather than filling it with a
    # plausible verdict no model produced.
    definitions = len(re.findall(r"function render\(", diag))
    calls = len(re.findall(r"(?<!function )render\(report\)", diag))
    check("a failed request produces no verdict",
          definitions == 1 and calls == 1
          and re.search(r"render\(\s*\{", diag) is None,
          f"{definitions} definitions, {calls} calls")
    check("the button label is translated in both directions",
          't("analyze")' in diag and 't("analyze_busy")' in diag)

    print("\n=== bilingual coverage ===")
    # data-i18n resolves through t(), which falls back to the key itself —
    # so a key missing from one pack never throws, it renders "plan_pro_t"
    # in front of a farmer. Asserting both packs is what stops a string
    # being added in one language only.
    src = modules["i18n.js"]
    ar_at, en_at = src.index("ar: {"), src.index("en: {")
    ar_block = src[ar_at + 5:en_at]          # skip "ar: {"
    en_block = src[en_at + 5:]               # skip "en: {"
    keys = sorted(set(re.findall(r'data-i18n="([^"]+)"', html)))
    pat = lambda block: set(re.findall(r"\b([a-z0-9_]+):\s*\"", block))
    missing_ar = [k for k in keys if k not in pat(ar_block)]
    missing_en = [k for k in keys if k not in pat(en_block)]
    check(f"all {len(keys)} data-i18n keys exist in the Arabic pack",
          not missing_ar, str(missing_ar))
    check(f"all {len(keys)} data-i18n keys exist in the English pack",
          not missing_en, str(missing_en))
    check("the two packs carry the same keys",
          pat(ar_block) == pat(en_block),
          f"only-ar={sorted(pat(ar_block) - pat(en_block))} "
          f"only-en={sorted(pat(en_block) - pat(ar_block))}")

    # Assertions about the app as a whole rather than one file: splitting into
    # modules must not let a guarantee quietly evaporate into a file the test
    # no longer reads.
    agg = "\n".join(modules.values())

    # Keys reached only through t() in JS never appear in the markup, so the
    # two checks above cannot see them — a typo there renders its own name in
    # front of a farmer, and only in one of the two languages.
    used = set(re.findall(r"\bt\(['\"]([a-z0-9_]+)['\"]\)", agg))
    missed_ar = sorted(used - pat(ar_block))
    missed_en = sorted(used - pat(en_block))
    check(f"all {len(used)} keys passed to t() exist in the Arabic pack",
          not missed_ar, str(missed_ar))
    check(f"all {len(used)} keys passed to t() exist in the English pack",
          not missed_en, str(missed_en))

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
