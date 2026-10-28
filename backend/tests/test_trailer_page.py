"""Static verification of the standalone cinematic trailer page.

WHY THIS PAGE HAS ITS OWN SUITE
-------------------------------
The trailer is deliberately outside the product: no module is imported, no
state is shared, and the page is never linked from the app. That isolation is
the whole point, which also means the app's suites cannot see it — they read
``frontend/js/*.js`` and ``frontend/index.html``. So the guarantees the app is
held to are restated here against this page, plus the ones that are specific to
it: a fixed frame, a forty-second clock, and numbers that are true.

The last of those matters most. The trailer says "610 conditions" and "6
domains" on screen. Those are not marketing figures typed into HTML — they are
read from the shipped taxonomy in this file and compared, so the day the
taxonomy changes, this suite fails rather than letting the trailer drift into
a claim that is no longer accurate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from agents import pathology_taxonomy as taxonomy

ROOT = Path(__file__).resolve().parents[2] / "frontend"
HTML_PATH = ROOT / "trailer.html"
CSS_PATH = ROOT / "trailer" / "trailer.css"
JS_PATH = ROOT / "trailer" / "trailer.js"

PASS = 0
FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def css_block(css: str, selector: str) -> str:
    """The declarations of one rule, by its exact leading selector."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else ""


def main() -> int:
    print("=== cinematic trailer page ===")

    for path in (HTML_PATH, CSS_PATH, JS_PATH):
        check(f"{path.name} exists", path.is_file(), str(path))
    if not all(p.is_file() for p in (HTML_PATH, CSS_PATH, JS_PATH)):
        print(f"\nTOTAL: {PASS} passed, {FAIL} failed")
        return 1

    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")

    # ---------------------------------------------------------------- isolation
    print("\n=== isolation ===")

    # The page loads its own two files and nothing from the application. An
    # import of an app module would drag the product's login state, its API
    # client and its language store onto a projector screen.
    check("the page loads only its own stylesheet and script",
          "/assets/trailer/trailer.css" in html
          and "/assets/trailer/trailer.js" in html
          and "/assets/css/styles.css" not in html
          and "/assets/js/" not in html)

    check("the script is a single deferred classic script",
          html.count("<script") == 1 and "defer" in html
          and re.search(r"<script[^>]+src=", html) is not None)

    check("the page imports no application module",
          re.search(r'from\s*["\']\./|import\s', js) is None
          and "api.js" not in js and "i18n.js" not in js)

    # The product page must not link here: the trailer is a presentation tool,
    # not a feature of the workspace.
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    check("the product page does not link to the trailer",
          "trailer" not in index.lower())

    # --------------------------------------------------------------- no recording
    print("\n=== the page records nothing ===")

    recording = re.search(
        r"MediaRecorder|getDisplayMedia|captureStream|createObjectURL|"
        r"showSaveFilePicker|\.download\b",
        js + html,
    )
    check("no recording or capture primitive is present", recording is None,
          str(recording.group(0)) if recording else "")

    check("nothing is written as markup",
          re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", js)
          is None)

    check("no inline event handlers in the markup",
          re.search(r"\son[a-z]+\s*=\s*[\"']", html) is None)

    check("no dynamic code evaluation",
          re.search(r"\beval\s*\(|new\s+Function\s*\(", js) is None)

    # ------------------------------------------------------------------- language
    print("\n=== english, left to right ===")

    check("the document is declared English and LTR",
          'lang="en"' in html and 'dir="ltr"' in html)

    # The Arabic line is product content being demonstrated, not interface
    # chrome — every label, heading and control around it stays English.
    check("the one Arabic line is scoped as content",
          re.search(r'id="i-ar"[^>]*lang="ar"[^>]*dir="rtl"', html) is not None
          or re.search(r'id="i-ar"[^>]*dir="rtl"[^>]*lang="ar"', html) is not None)

    check("the preview control is labelled exactly as asked",
          "Play Cinematic Trailer" in html and 'id="play"' in html)

    # ------------------------------------------------------------ fixed framing
    print("\n=== a frame that never moves ===")

    check("the stage is a fixed 16:9 frame",
          "aspect-ratio: 16 / 9" in css)

    # Resolution independence comes from one unit, not from scaling the stage.
    # A transform on the frame is exactly the jarring zoom this must not have.
    # Read from the declarations only: the rule's own comment explains *why*
    # there is no transform, and a check that cannot tell an explanation from a
    # declaration would fail on its own documentation.
    css_code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    stage_block = css_block(css_code, ".stage")
    check("the stage is never scaled, panned or zoomed",
          bool(stage_block) and "transform" not in stage_block
          and "zoom" not in stage_block and "scale" not in stage_block,
          stage_block[:80])

    check("every dimension derives from one stage-width unit",
          re.search(r"--u:\s*calc\(min\(100vw,\s*100vh\s*\*\s*16\s*/\s*9\)\s*/\s*100\)", css)
          is not None)

    check("the pointer is composited, never laid out",
          "translate3d" in js and "pointer.style.transform" in js)

    # ------------------------------------------------------------- the 40 seconds
    print("\n=== forty seconds ===")

    check("the clock is exactly forty seconds", "TOTAL = 40000" in js)

    times = [int(t) for t in re.findall(r"\{\s*at:\s*(\d+)", js)]
    check(f"the timeline has {len(times)} beats, in order",
          len(times) >= 30 and times == sorted(times),
          f"beats={len(times)}")
    check("no beat is scheduled past the end",
          bool(times) and max(times) < 40000, f"last={max(times) if times else '-'}")

    for label in ("The hook", "The diagnosis", "Global impact"):
        check(f"the '{label}' chapter is named", label in js)

    # The three acts the brief asks for, in the order it asks for them.
    order = [js.find(s) for s in ('chapter("The hook")',
                                  'chapter("The diagnosis")',
                                  'chapter("Global impact")')]
    check("the acts play in the brief's order",
          all(i >= 0 for i in order) and order == sorted(order), str(order))

    # --------------------------------------------------------------- the numbers
    print("\n=== the numbers on screen are true ===")

    entries = len(taxonomy.ENTRIES)
    domains = len({entry.domain for entry in taxonomy.ENTRIES})

    targets = [int(x) for x in re.findall(r'countTo\("st-\d",\s*(\d+)', js)]
    shown = targets[:4]
    check(f"the trailer's {entries} conditions match the shipped taxonomy",
          shown[0:1] == [entries], f"shown={shown}, taxonomy={entries}")
    check(f"the trailer's {domains} domains match the shipped taxonomy",
          shown[1:2] == [domains], f"shown={shown}, domains={domains}")
    check("the trailer claims bilingual, and ships two languages",
          shown[2:3] == [2] and 'id="i-en"' in html and 'id="i-ar"' in html)
    check("the trailer claims full openness, and the stat reads 100",
          shown[3:4] == [100], f"shown={shown}")
    check("the reduced-motion path shows the same numbers",
          targets[4:] == targets[:4], f"{targets}")

    # The sample diagnosis is labelled as a sample wherever a viewer would look
    # for provenance: on the card itself.
    check("the demonstration diagnosis is marked as a sample",
          re.search(r'class="tag tag--mute"[^>]*>\s*Sample', html, re.I) is not None)

    # --------------------------------------------------------------- the wiring
    print("\n=== the wiring holds ===")

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r'\$\(\s*["\']([^"\']+)["\']\s*\)', js))
    missing_ids = sorted(js_ids - html_ids)
    check(f"all {len(js_ids)} ids the script looks up exist in the page",
          not missing_ids, str(missing_ids))

    # Every class in the markup, and every class the script toggles, must have
    # a rule. A control that is toggled but never styled looks like a dead
    # button — the failure this project has already paid for once.
    html_classes = set()
    for attr in re.findall(r'class="([^"]+)"', html):
        html_classes.update(attr.split())

    js_classes = set(re.findall(
        r'classList\.(?:add|remove|toggle)\(\s*["\']([A-Za-z][\w-]*)["\']', js))
    js_classes.update(re.findall(
        r'\["((?:is-)[a-z-]+)"\s*,', js))
    js_classes.update({"is-playing", "is-done", "is-static"})

    css_classes = set(re.findall(r"\.([A-Za-z][\w-]*)", css))

    missing_html = sorted(html_classes - css_classes)
    missing_js = sorted(js_classes - css_classes)
    check(f"all {len(html_classes)} classes in the page are styled",
          not missing_html, str(missing_html))
    check(f"all {len(js_classes)} classes the script toggles are styled",
          not missing_js, str(missing_js))

    # The cursor is hidden while the piece plays and restored afterwards; the
    # replay control has to be reachable once it ends.
    check("the real cursor returns when the piece is over",
          "body.is-playing { cursor: none; }" in css
          and "body.is-done .endcard" in css)

    check("a viewer who asked for stillness is shown the finished frame",
          "prefers-reduced-motion" in css and "REDUCED" in js
          and "is-static" in css)

    # --------------------------------------------------------------- it parses
    print("\n=== the script parses ===")

    try:
        import esprima
        esprima.parseScript(js)
        parsed, why = True, ""
    except Exception as exc:                      # noqa: BLE001 - reported below
        parsed, why = False, f"{type(exc).__name__}: {exc}"
    check("trailer.js is syntactically valid", parsed, why)

    # ------------------------------------------------------------------- route
    print("\n=== the route exists ===")

    main_py = (ROOT.parent / "backend" / "main.py").read_text(encoding="utf-8")
    check("the page is served at /trailer",
          'TRAILER_HTML = FRONTEND_DIR / "trailer.html"' in main_py
          and '@app.get("/trailer"' in main_py)

    print("\n" + "=" * 52)
    print(f"TOTAL: {PASS} passed, {FAIL} failed")
    print("=" * 52)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
