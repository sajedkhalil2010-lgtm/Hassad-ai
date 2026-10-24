"""Scope audit of the frontend ES modules: a dead module ships no listeners.

WHY A SEPARATE CHECK FROM THE SYNTAX TEST
------------------------------------------
A `const` declared twice in one block is an *early* SyntaxError: the browser
refuses to compile the module at all, so its exports never exist. In
``app.js`` that means the import of ``diagnose.js`` fails, the whole graph is
rejected, and not one event listener attaches — every button and nav link on
the page goes dead while the markup looks perfect.

esprima does not implement that particular early-error rule. This suite
already proves each module *parses*, and it would have reported "8/8 valid"
over a module a browser discards on sight. So the two checks are different:
one asks "is this grammatical", this one asks "would a browser accept it".

The detector below blanks comments, strings, template literals (including
nested ``${}``) and regex bodies first, then walks the real brace tree to
group declarations by scope. It distinguishes the one shape that is legal but
looks identical — two sibling ``for (const node of ...)`` loops — from a true
redeclaration, because the for-head binds its own names.

Every check runs twice: once over in-memory fixtures that prove the detector
still detects, and once over the shipped modules. A detector that has quietly
stopped catching anything would otherwise pass forever.
"""

from __future__ import annotations

import glob
import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_JS = ROOT / "frontend" / "js"

PASS = 0
FAIL = 0

WORD = r"[A-Za-z_$][\w$]*"
DECL = re.compile(r"\b(const|let|var|function)\s+")
IDENT = re.compile(WORD)
FOR_HEAD = re.compile(r"\bfor\s*\(")
ARROW_PARAMS = re.compile(r"\(([^()]*?)\)\s*=>")
FUNC_PARAMS = re.compile(r"\bfunction\s*" + WORD + r"\s*\(([^()]*?)\)")
METHOD_PARAMS = re.compile(r"\bfunction\s*\(([^()]*?)\)")


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def blank_literals(src: str) -> str:
    """Replace comment/string/template/regex bodies with spaces.

    Blank rather than delete, so every offset and line number still points at
    the same character in the original file.
    """
    out = list(src)
    i, n = 0, len(src)
    prev_sig = ""  # last significant char: tells a regex from a division
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                if src[i] != "\n":
                    out[i] = " "
                i += 1
            for k in range(i, min(i + 2, n)):
                out[k] = " "
            i += 2
            continue
        if c in "\"'":
            quote, j = c, i + 1
            while j < n:
                if src[j] == "\\":
                    out[j] = " "
                    if j + 1 < n and src[j + 1] != "\n":
                        out[j + 1] = " "
                    j += 2
                    continue
                if src[j] == quote:
                    break
                if src[j] != "\n":
                    out[j] = " "
                j += 1
            i = j + 1
            prev_sig = '"'
            continue
        if c == "`":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    out[j] = " "
                    j += 2
                    continue
                if src[j] == "`":
                    break
                if src[j] == "$" and j + 1 < n and src[j + 1] == "{":
                    depth, k = 1, j + 2
                    out[j] = " "
                    out[j + 1] = " "
                    while k < n and depth:
                        if src[k] == "{":
                            depth += 1
                        elif src[k] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        k += 1
                    j = k
                    continue
                if src[j] != "\n":
                    out[j] = " "
                j += 1
            i = j + 1
            prev_sig = "`"
            continue
        if c == "/" and prev_sig not in ")]}\"'`" and prev_sig not in WORD:
            j, in_class, ok = i + 1, False, False
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    ok = True
                    break
                elif src[j] == "\n":
                    break
                j += 1
            if ok:
                for k in range(i, j + 1):
                    if out[k] != "\n":
                        out[k] = " "
                i = j + 1
                prev_sig = "/"
                continue
        if not c.isspace():
            prev_sig = c
        i += 1
    return "".join(out)


def split_params(raw: str) -> list[str]:
    """Names bound by a parameter list: 'a, {b, c}, [d], e = 1' -> ids."""
    parts, depth, buf = [], 0, ""
    for ch in raw:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    names: list[str] = []
    for part in parts:
        part = part.strip().lstrip(".")
        if not part:
            continue
        if part[0] in "{[":
            names.extend(m.group(0) for m in IDENT.finditer(part))
        else:
            first = IDENT.match(part)
            if first:
                names.append(first.group(0))
    return [nm for nm in names if nm not in ("of", "in", "as")]


def destructured_names(raw: str) -> list[str]:
    """Names in a binding pattern. `key: value` keeps only the value."""
    names, i, n, expect_value = [], 0, len(raw), False
    while i < n:
        ch = raw[i]
        if ch in "{[":
            expect_value = False
        elif ch in "}]":
            expect_value = False
        elif ch == ":":
            expect_value = True
        match = IDENT.match(raw, i)
        if match:
            name = match.group(0)
            rest = raw[match.end():].lstrip()
            if name not in ("of", "in", "as", "true", "false", "null", "undefined"):
                if rest.startswith(":"):
                    pass  # the key is not a new binding
                elif expect_value:
                    names.append(name)
                    expect_value = False
                else:
                    names.append(name)
            i = match.end()
            continue
        i += 1
    return names


def audit_source(src: str) -> list[tuple[int, str]]:
    """-> [(line, message)] for every early redeclaration error found."""
    code = blank_literals(src)
    problems: list[tuple[int, str]] = []

    pending: list[tuple[str, int]] = []
    for rx in (FUNC_PARAMS, METHOD_PARAMS, ARROW_PARAMS):
        for match in rx.finditer(code):
            pending.append((match.group(1), match.start(1)))

    pending_by_brace: dict[int, list[str]] = {}
    for raw, pos in pending:
        # Bind to the next '{', but only if no ';' or '}' intervenes —
        # otherwise a paramless arrow would donate its names to an unrelated
        # block further down and false-flag it.
        brace, cursor = -1, pos
        while cursor < len(code):
            if code[cursor] == "{":
                brace = cursor
                break
            if code[cursor] in ";}":
                break
            cursor += 1
        if brace >= 0:
            pending_by_brace.setdefault(brace, []).extend(split_params(raw))

    # Each scope maps a name to (first line, declared in a for-head).
    stack: list[dict[str, tuple[int, bool]]] = [{}]
    i, n, line = 0, len(code), 1
    paren = 0
    for_anchor: int | None = None

    while i < n:
        c = code[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "(":
            paren += 1
            i += 1
            continue
        if c == ")":
            paren -= 1
            if for_anchor is not None and paren < for_anchor:
                for_anchor = None
            i += 1
            continue
        if c == "{":
            scope: dict[str, tuple[int, bool]] = {}
            for nm in pending_by_brace.get(i, []):
                scope[nm] = (line, False)
            stack.append(scope)
            i += 1
            continue
        if c == "}":
            if len(stack) > 1:
                stack.pop()
            i += 1
            continue

        head = FOR_HEAD.match(code, i)
        if head:
            for_anchor = paren + 1
            paren += 1
            i = head.end()
            continue

        match = DECL.match(code, i)
        if not match:
            i += 1
            continue

        keyword, j = match.group(1), match.end()
        while j < n and code[j] in " \t":
            j += 1

        names: list[str] = []
        if keyword == "function":
            first = IDENT.match(code, j)
            if first:
                names, j = [first.group(0)], first.end()
        elif j < n and code[j] in "{[":
            depth, k = 0, j
            while k < n:
                if code[k] in "{[":
                    depth += 1
                elif code[k] in "}]":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            names, j = destructured_names(code[j:k + 1]), k + 1
        else:
            first = IDENT.match(code, j)
            if first:
                names, j = [first.group(0)], first.end()

        # A for-head binds its own names, so two sibling loops reusing
        # `node` is legal — only a clash with a block-level declaration is
        # an early error.
        in_for_head = for_anchor is not None and paren >= for_anchor
        top = stack[-1]
        for nm in names:
            if nm in top:
                first_line, first_for = top[nm]
                if in_for_head and first_for:
                    continue
                problems.append(
                    (line, f"redeclared {keyword} '{nm}' (first at line {first_line})"))
            else:
                top[nm] = (line, in_for_head)
        i = j

    return problems


def audit(path: str) -> list[tuple[int, str]]:
    return audit_source(io.open(path, encoding="utf-8").read())


def main() -> int:
    print("\n=== the detector still detects ===")
    # Fixtures, both directions. If a refactor ever stops this from failing
    # on the bad three, the real scan below is worthless and must not be
    # trusted to be green.
    bad = {
        "duplicate const in one block":
            "function f(r) {\n  const x = Boolean(r.a);\n  const x = r.b;\n  return x;\n}",
        "const shadowing a parameter":
            "function f(r) {\n  const r = 1;\n  return r;\n}",
        "destructuring redeclaration":
            "const { a, b } = one;\nconst { c, a } = two;",
        "for-head clashing with a block binding":
            "const node = 1;\nfor (const node of list) { node.x = 2; }",
    }
    good = {
        "sibling for-of loops may reuse a name":
            "for (const n of a) { n.x = 1; }\nfor (const n of b) { n.x = 2; }",
        "same name in separate scopes":
            "function o() {\n  const x = 1;\n  const inner = () => { const x = 2; return x; };\n  return x + inner();\n}",
        "names inside literals are not declarations":
            "const note = \"const x = 1; const x = 2;\";\n// const x = 3;\nconst rx = /const x = 4/;\nconst x = 5;",
        "a comment about a duplicate is not a duplicate":
            "// const provisional declared twice above\nconst provisional = 1;",
    }
    for label, src in bad.items():
        found = audit_source(src)
        check(f"detector rejects: {label}", bool(found),
              "the detector no longer catches this shape")
    for label, src in good.items():
        found = audit_source(src)
        check(f"detector allows: {label}", not found,
              f"false positive: {found}")

    print("\n=== every shipped module compiles in a browser ===")
    if not FRONTEND_JS.is_dir():
        check("frontend/js exists", False, str(FRONTEND_JS))
    else:
        files = sorted(glob.glob(str(FRONTEND_JS / "**" / "*.js"), recursive=True))
        check("modules found", len(files) >= 8, f"only {len(files)}")
        for path in files:
            name = os.path.basename(path)
            problems = audit(path)
            check(f"{name} has no redeclaration",
                  not problems,
                  "; ".join(f"line {ln}: {msg}" for ln, msg in problems))

    # The specific regression this file exists for: a duplicate `provisional`
    # inside renderVerdict() killed diagnose.js, which killed app.js, which
    # left every button on the page inert while the markup looked correct.
    print("\n=== the original defect stays fixed ===")
    diag = FRONTEND_JS / "diagnose.js"
    if diag.is_file():
        src = diag.read_text(encoding="utf-8")
        check("diagnose.js declares each name once", not audit(str(diag)))
        check("renderVerdict keeps the flag and the name separate",
              "const lowConfidence" in src and src.count("const provisional") == 1,
              f"provisional declared {src.count('const provisional')}x")

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
