"""Static verification of the frontend modules.

A static test cannot prove the page renders, but it can prove the four ways
a module rewrite actually breaks in practice:
  1. an import naming a file that does not exist,
  2. an import naming a symbol the target does not export,
  3. a $() lookup for an element id that is not in the markup,
  4. a class emitted by the markup or a module with no rule behind it.

Item 4 matters because it fails silently: nothing throws and nothing errors,
the farmer just gets an unstyled block where a card should be.

Plus a brace/paren balance pass, which catches the fat-fingered syntax error
that would otherwise take the whole page down with no visible symptom.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Two levels up from backend/tests/ -> the project root, then into frontend/.
#: Mirrors how main.py derives FRONTEND_DIR (from the file, never the cwd) so
#: the suite works from any working directory.
ROOT = Path(__file__).resolve().parents[2] / "frontend"
JS = ROOT / "js"

IMPORT_RE = re.compile(r'import\s*\{([^}]*)\}\s*from\s*["\']([^"\']+)["\']', re.S)
EXPORT_FN_RE = re.compile(r"export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)")
EXPORT_LIST_RE = re.compile(r"export\s*\{([^}]*)\}", re.S)
ID_RE = re.compile(r'\$\(\s*["\']([^"\']+)["\']\s*\)')
QUERY_RE = re.compile(r'querySelector(?:All)?\(\s*["\']([^"\']+)["\']')

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


def strip_comments_and_strings(src: str) -> str:
    """Blank out comments and string/template bodies so punctuation counting
    is not thrown off by braces inside prose or literal text."""
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        two = src[i : i + 2]
        if two == "//":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if two == "/*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "\"'`":
            quote, j = ch, i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    break
                j += 1
            out.append(" " * (j - i + 1))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def exports_of(src: str) -> set[str]:
    names = set(EXPORT_FN_RE.findall(src))
    for group in EXPORT_LIST_RE.findall(src):
        for item in group.split(","):
            item = item.strip()
            if not item:
                continue
            # `foo as bar` exports bar; plain `foo` exports foo.
            names.add(item.split(" as ")[-1].strip())
    return names


def _media_block(css: str, feature: str) -> str | None:
    """Return the body of the first ``@media`` whose query names *feature*.

    A non-greedy ``[^}]*`` cannot be used for this: the reduced-motion query
    holds several rules of its own, so the first inner brace would cut the
    block short before the property being looked for. Brace-count instead.
    """
    start = css.find(f"@media ({feature}")
    if start < 0:
        return None
    open_at = css.find("{", start)
    if open_at < 0:
        return None
    depth = 0
    for pos in range(open_at, len(css)):
        if css[pos] == "{":
            depth += 1
        elif css[pos] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1:pos]
    return None


def main() -> int:
    if not (ROOT / "index.html").is_file():
        print(f"  [FAIL] frontend not found at {ROOT}")
        return 1

    html = (ROOT / "index.html").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    html_classes = set()
    for attr in re.findall(r'class="([^"]+)"', html):
        html_classes.update(attr.split())

    css = (ROOT / "css" / "styles.css").read_text(encoding="utf-8")
    css_classes = set(re.findall(r"\.([A-Za-z][\w-]*)", css))

    modules = {p.name: p.read_text(encoding="utf-8") for p in sorted(JS.glob("*.js"))}
    # A superset check, not equality: the entry point legitimately grows new
    # modules as the app does. What matters is that every module the page
    # depends on is actually there — the import-resolution pass below then
    # proves each one resolves to a real export.
    CORE_MODULES = {"api.js", "dom.js", "auth.js", "diagnose.js", "chat.js",
                    "i18n.js", "history.js", "pricing.js", "subscribe.js",
                    "app.js"}
    check("all core modules are present on disk",
          CORE_MODULES <= set(modules),
          f"missing {sorted(CORE_MODULES - set(modules))}")

    print("\n=== syntax balance ===")
    for name, src in modules.items():
        bare = strip_comments_and_strings(src)
        for open_ch, close_ch in (("{", "}"), ("(", ")"), ("[", "]")):
            check(f"{name}: {open_ch}{close_ch} balanced", bare.count(open_ch) == bare.count(close_ch),
                  f"{bare.count(open_ch)} vs {bare.count(close_ch)}")

    print("\n=== imports resolve ===")
    for name, src in modules.items():
        for _alias, spec in IMPORT_RE.findall(src):
            target = spec.rsplit("/", 1)[-1]
            check(f"{name} -> {spec} exists", target in modules, f"missing {spec}")
            if target not in modules:
                continue
            imported = [part.strip() for part in _alias.split(",") if part.strip()]
            available = exports_of(modules[target])
            for item in imported:
                symbol = item.split(" as ")[0].strip()
                check(f"{name} imports {symbol} from {target}", symbol in available,
                      f"{target} exports {sorted(available)}")

    print("\n=== every exported symbol is consumed somewhere ===")
    # An export nothing else uses is dead weight. Counting references across
    # the OTHER modules (not this one) is what makes that meaningful — and a
    # plain \b boundary cannot be used, because $ is not a word character.
    for name, src in modules.items():
        elsewhere = "\n".join(
            strip_comments_and_strings(other)
            for other_name, other in modules.items()
            if other_name != name
        )
        for symbol in sorted(exports_of(src)):
            pattern = rf"(?<![\w$]){re.escape(symbol)}(?![\w$])"
            used = len(re.findall(pattern, elsewhere))
            check(f"{name}: export {symbol} is used by another module", used >= 1,
                  f"0 references outside {name}")

    print("\n=== element ids exist in the markup ===")
    missing = []
    for name, src in modules.items():
        for elem_id in ID_RE.findall(src):
            if elem_id not in html_ids:
                missing.append(f"{name} -> #{elem_id}")
    check("every $(\"id\") target exists in index.html", not missing, str(missing))

    print("\n=== querySelector targets exist ===")
    bad_sel = []
    for name, src in modules.items():
        for sel in QUERY_RE.findall(src):
            first = sel.split()[0]
            if first.startswith("#"):
                if first[1:] not in html_ids:
                    bad_sel.append(f"{name} -> {sel}")
            elif first.startswith("."):
                if first[1:] not in html_classes and first[1:] not in css_classes:
                    bad_sel.append(f"{name} -> {sel}")
    check("every querySelector target exists in markup or stylesheet", not bad_sel, str(bad_sel))

    print("\n=== ids the markup declares are actually wired up ===")
    # A control that exists in HTML but is never looked up by any module is
    # dead furniture — it will do nothing when clicked.
    # Controls a user can operate, plus regions the code must reach into.
    # Structural wrappers are deliberately absent: #auth-card is the dialog's
    # styling container and has no behaviour, so requiring it to be referenced
    # would just push code to look it up for no reason.
    WIRABLE = {
        "file", "dropzone", "preview", "clear-image", "analyze", "status",
        "results", "summary", "symptoms", "advisory-body",
        "messages", "chat-form", "chat-input",
        "chat-send", "suggestions", "chat-reset", "chat-empty",
        "auth-modal", "auth-email", "auth-password", "tab-login",
        "tab-register", "auth-submit", "auth-signed-out", "auth-signed-in",
        "auth-status", "auth-who", "auth-avatar", "auth-admin-view",
        "admin-output", "account-trigger", "conn", "conn-label",
        "plan-modal",
        # Sign-up handoff: the dialog, both step regions, every control that
        # leaves them, and the four card fields the module formats in place.
        "su-modal", "su-title", "su-who", "su-summary",
        "su-step-plan", "su-step-pay", "su-plan-step", "su-pay-step",
        "su-status", "su-continue", "su-skip", "su-back",
        "pay-form", "pay-number", "pay-expiry", "pay-cvv", "pay-holder",
        "toast",
    }
    referenced = set()
    for src in modules.values():
        referenced.update(ID_RE.findall(src))
        referenced.update(re.findall(r'querySelector(?:All)?\(\s*["\']#([\w-]+)', src))
    unwired = sorted(WIRABLE - referenced)
    check("every interactive control is wired up", not unwired, str(unwired))

    print("\n=== every class applied is styled ===")
    # A module can go on emitting a class after its rule was deleted, and
    # nothing throws — the farmer simply sees an unstyled block. Two tiers,
    # because they carry very different precision:
    #
    #   A. Tokens written where a class name is unambiguous. Any miss is a
    #      real defect.
    #   B. BEM compounds (`__` / `--`) anywhere in a JS string. These reach
    #      the DOM through helpers the tier-A patterns cannot see into —
    #      badge(text, "badge badge--crop") is the live case. Requiring a BEM
    #      separator is what separates a class from hyphenated English such
    #      as "decision-support", which is otherwise indistinguishable.
    js_all = "\n".join(modules.values())

    tier_a = set(html_classes)
    for cls in re.findall(r'el\(\s*"[a-z0-9]+"\s*,\s*"([^"]*)"', js_all):
        tier_a.update(cls.split())
    for fn in ("add", "remove", "toggle", "contains"):
        for call in re.findall(rf'classList\.{fn}\(\s*"([^"]+)"', js_all):
            tier_a.update(call.split())
    for call in re.findall(r'\.className\s*=\s*"([^"]*)"', js_all):
        tier_a.update(call.split())
    tier_a.discard("")

    strings = " ".join(a or b
                       for a, b in re.findall(r'"([^"\n]*)"|\'([^\'\n]*)\'', js_all))
    # Not every compound string is a class. Ids handed to $(), attribute
    # names, and values compared against data all share the shape.
    ids = set(html_ids) | set(ID_RE.findall(js_all))
    ids.update(re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', js_all))
    attrs = set(re.findall(r'\[([\w-]+)[\]=~^$*|]', css))
    attrs.update(re.findall(r'setAttribute\(\s*"([^"]+)"', js_all))
    data_values = set(re.findall(r'===\s*"([^"]+)"', js_all))
    data_values.update(re.findall(r'"([^"]+)"\s*===', js_all))

    tier_b = {t for t in re.findall(r'[a-z][\w-]*(?:__|--)[\w-]+', strings)
              if t not in ids and t not in attrs and t not in data_values
              and not t.startswith(("--", "data-", "aria-"))}

    unstyled_a = sorted(c for c in tier_a if c not in css_classes)
    check("every class written in a class position is styled",
          not unstyled_a, f"unstyled: {unstyled_a}")
    unstyled_b = sorted(c for c in tier_b if c not in css_classes)
    check("every compound class token in the modules is styled",
          not unstyled_b, f"unstyled: {unstyled_b}")
    print(f"       ({len(tier_a)} class-position tokens, {len(tier_b)} compounds, "
          f"{len(css_classes)} rules defined)")

    # --- cross-layer contract --------------------------------------------
    # Every field the renderer touches must exist in the payload the backend
    # actually emits. This is the regression test for a whole class of bug: the
    # UI reading a value the backend masks, blanks or renames, which renders a
    # placeholder exactly where a farmer expected an answer.
    print("\n--- cross-layer: the payload carries what the renderer reads ---")
    from agents.config import get_settings
    from agents.diagnostic_agent import DiagnosticAgent

    norm = DiagnosticAgent(get_settings())._normalise
    seed = {"crop": "Mango", "crop_ar": "مانجو", "species_confidence": 0.8,
            "is_leaf_image": True, "image_quality": "usable", "meta": {},
            "pathogen": "Colletotrichum gloeosporioides"}

    def norm_payload(**kw):
        return norm(dict(seed, **kw), mime="image/jpeg", model="m",
                    elapsed_ms=1, filename=None, language="ar")

    verdicts = {
        "healthy": norm_payload(status="healthy", disease="Healthy Plant",
                                disease_ar="نبات سليم", pathogen="None", confidence=0.95),
        "diseased": norm_payload(status="diseased", disease="Anthracnose",
                                 disease_ar="الأنثراكنوز", confidence=0.9,
                                 severity="moderate", severity_pct=0.4),
        "uncertain": norm_payload(status="uncertain", disease="Anthracnose",
                                  disease_ar="الأنثراكنوز", confidence=0.55,
                                  severity="low", severity_pct=0.2),
    }
    advisory_stub = {"title": "t", "title_ar": "ت", "region": "Tébessa", "kind": "k",
                     "actions": [], "treatments": [], "prevention": [],
                     "disclaimer": "d", "irrigation_schedule": {},
                     "meta": {"source": "local_kb"}}
    report_stub = {"diagnosis": verdicts["uncertain"], "status": "provisional",
                   # Stamped by the SUPERVISOR (supervisor_agent.py), not by
                   # _normalise — listed here so a missing key is not mistaken
                   # for a field the diagnostic agent forgot to emit.
                   "run_id": "0" * 32,
                   "created_at": "2026-10-23T00:00:00+00:00",
                   "confidence": {"overall": 0.55, "provisional": True},
                   "timing": {"total_ms": 100}, "advisory": advisory_stub}

    # Fields the renderer reads TOLERANTLY — behind `x.prop ? … : …`,
    # `.filter(Boolean)` or `x.prop || …`. Their absence is a supported case,
    # not a contract break; pinning them as required would make this check cry
    # wolf and hide a real drift behind the noise.
    optional = {"meta": {"fallback_reason"},
                "irrig": {"schedule", "target", "note"}}

    def unseen(refs, obj, opt=frozenset()):
        return sorted({r for r in refs if r not in obj and r not in opt})

    # Scoped to the renderer alone. `meta` is ALSO bound to a DOM element in
    # auth.js (`els.meta.textContent`), so scanning every module would report a
    # false break the instant an unrelated file touched an element of that name.
    renderer = modules.get("diagnose.js", "")
    i18n_src = modules.get("i18n.js", "")

    diag_refs = set(re.findall(r"\bd\.(\w+)", renderer)) | \
        set(re.findall(r"\bdiagnosis\.(\w+)", renderer))
    report_refs = set(re.findall(r"\breport\.(\w+)", renderer))
    advisory_refs = set(re.findall(r"\ba\.(\w+)", renderer))
    meta_refs = set(re.findall(r"\bmeta\.(\w+)", renderer))
    irrig_refs = set(re.findall(r"\birrig\.(\w+)", renderer))

    check("renderer reads the provisional fields",
          "provisional_disease" in renderer and "provisional_disease_ar" in renderer)
    lost = sorted({r for r in diag_refs if not any(r in v for v in verdicts.values())})
    check("every diagnosis field the renderer reads exists in the payload",
          not lost, f"unbacked: {lost}")
    lost = unseen(report_refs, report_stub)
    check("every report field the renderer reads exists", not lost, f"unbacked: {lost}")
    lost = unseen(advisory_refs, advisory_stub)
    check("every advisory field the renderer reads exists", not lost, f"unbacked: {lost}")
    lost = unseen(meta_refs, advisory_stub["meta"], optional["meta"])
    check("every meta field the renderer reads exists", not lost, f"unbacked: {lost}")
    lost = unseen(irrig_refs, advisory_stub["irrigation_schedule"], optional["irrig"])
    check("every irrigation field the renderer reads exists", not lost, f"unbacked: {lost}")

    # The specific behaviour behind the provisional badge: an undetermined
    # verdict must still hand the UI a name to display, while the masked label
    # stays masked and no lead leaks onto the other two verdicts.
    check("an undetermined verdict still masks the disease name",
          verdicts["uncertain"]["disease_ar"] == "ظهور مرض غير محدد")
    check("an undetermined verdict exposes the model's lead separately",
          bool(verdicts["uncertain"]["provisional_disease"])
          and verdicts["uncertain"]["provisional_disease_ar"] == "الأنثراكنوز",
          repr(verdicts["uncertain"]["provisional_disease_ar"]))
    check("a healthy plant offers no lead to show",
          verdicts["healthy"]["provisional_disease"] == ""
          and verdicts["healthy"]["provisional_disease_ar"] == "")
    check("a confirmed diagnosis offers no lead to show",
          verdicts["diseased"]["provisional_disease"] == ""
          and verdicts["diseased"]["provisional_disease_ar"] == "")
    check("the probable badge is translated in both locales",
          "badge_probable" in renderer and "تشخيص محتمل" in i18n_src
          and "Probable diagnosis" in i18n_src)

    # --- navigation must survive every viewport --------------------------
    # Two bugs the markup could not show on its own: the section links were
    # display:none below 1040px with no hamburger or drawer anywhere in the
    # document standing in for them (so a phone had no route to #how at all),
    # and the header is sticky, so a jump that did work parked the heading
    # underneath a translucent bar. Both are pinned here because neither
    # produces a broken element — they produce a working element that goes
    # nowhere useful.
    print("\n--- navigation survives the small viewports ---")
    nav_links = re.findall(r'<a\s+href="(#[^"]+)"[^>]*data-i18n="nav_', html)
    check("the three section links are still declared",
          nav_links == ["#how", "#modules", "#why"], repr(nav_links))
    for anchor in nav_links:
        check(f"anchor {anchor} resolves to a real id",
              f'id="{anchor[1:]}"' in html)
    check("the section nav is never hidden by its own rule",
          re.search(r"\.lnav\s*\{[^}]*display:\s*none", css) is None,
          ".lnav{display:none} leaves phones with no navigation at all")
    check("the small viewport gets the nav row instead of a removal",
          "@media (max-width: 1040px)" in css
          and re.search(r"\.lnav\s*\{[^}]*overflow-x:\s*auto", css) is not None)
    check("the sticky header clears an anchor target",
          css.count("scroll-margin-top") >= 3
          and "position: sticky" in css,
          "without clearance the heading lands behind the sticky bar")
    check("smooth scrolling is enabled for in-page jumps",
          "scroll-behavior: smooth" in css)
    reduced = _media_block(css, "prefers-reduced-motion") or ""
    check("a reduced-motion user still gets instant jumps",
          "scroll-behavior: auto" in reduced,
          "the reduced-motion query must override the smooth glide")

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
