"""Static verification of the frontend modules.

A static test cannot prove the page renders, but it can prove the three ways
a module rewrite actually breaks in practice:
  1. an import naming a file that does not exist,
  2. an import naming a symbol the target does not export,
  3. a $() lookup for an element id that is not in the markup.

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
    check("all six modules are present on disk",
          set(modules) == {"api.js", "dom.js", "auth.js", "diagnose.js", "chat.js", "app.js"},
          str(sorted(modules)))

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
        "results", "summary", "symptoms", "advisory-body", "diagnosis",
        "advisory", "supervisor", "messages", "chat-form", "chat-input",
        "chat-send", "suggestions", "chat-reset", "chat-empty",
        "auth-modal", "auth-email", "auth-password", "tab-login",
        "tab-register", "auth-submit", "auth-signed-out", "auth-signed-in",
        "auth-status", "auth-who", "auth-avatar", "auth-admin-view",
        "admin-output", "account-trigger", "conn", "conn-label",
    }
    referenced = set()
    for src in modules.values():
        referenced.update(ID_RE.findall(src))
        referenced.update(re.findall(r'querySelector(?:All)?\(\s*["\']#([\w-]+)', src))
    unwired = sorted(WIRABLE - referenced)
    check("every interactive control is wired up", not unwired, str(unwired))

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
