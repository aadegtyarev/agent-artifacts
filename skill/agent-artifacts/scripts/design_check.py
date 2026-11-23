#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""design_check: does the code still match docs/design? Python standard library only.

Run it with `uv run design_check.py [repo]` or `python design_check.py [repo]`.

Checks, each problem printed as `file:line: message`:

- colours: every colour literal (#rgb, #rgba, #rrggbb, #rrggbbaa, rgb(),
  rgba(), hsl(), hsla()) in a declaration of a CSS file, or in a colour
  property of a UI file (a <style> block, a style attribute, CSS in a JS
  string, `fill="…"`, `.style.color = "…"`), is written somewhere in
  tokens.md. Spelling does not matter: #FFF, #ffffff, rgb(255,255,255),
  rgb(100% 100% 100% / 1) are one colour, and so are rgba(0,0,0,.5) and
  #00000080 (alpha is compared to two decimals). oklch(), hwb(),
  color-mix() and named colours are not parsed, so not checked.
- words: tone-of-voice.md has a Markdown table with a column named "Avoid";
  its cells list phrases separated by "," or "·" (backticks and quotes
  around them are dropped). None of them may appear, as whole words in any
  case, in the interface's text: HTML text nodes; the attributes title,
  alt, placeholder, aria-label, label and value; string literals in JS
  (and in <script> blocks). It is a heuristic that prefers missing a word
  to flagging code: a JS string is skipped when it is the first argument
  of addEventListener, querySelector, closest, getElementById, CustomEvent
  and the like, an import or require, or one word that is all lowercase,
  all capitals or holds one of # . : / [ _ - = (a selector, id, path, URL
  or key). {{ }}, {% %} and ${ } expressions are skipped.
- icons: every .svg in icons/ is referenced in icons.md, and every image
  icons.md references exists.

Comments (/* */, <!-- -->, {# #}, and // after a space or at a line start)
are skipped everywhere. Exit status: 0 clean, 1 problems found, 2 usage
error or no design folder.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
from pathlib import Path

CSS = [f"**/*.{e}" for e in ("css", "scss", "less")]
UI = [f"**/*.{e}" for e in "html js mjs ts jsx tsx vue svelte".split()]
SKIP_DIRS = set(
    "node_modules .venv venv env dist build docs vendor third_party staticfiles "
    "tests test __tests__".split()
)
SKIP_FILES = re.compile(r"\.(min|umd|bundle)\.(css|js)$")  # built or vendored
MARKUP = {".html", ".vue", ".svelte", ".jsx", ".tsx"}  # files with text nodes
SCRIPT = {".js", ".mjs", ".ts", ".jsx", ".tsx"}  # files that are code

HEX = r"#[0-9a-fA-F]{3,8}(?![\w-])"
FUNC = r"(?:rgba?|hsla?)\([^()]*\)"
COLOUR = re.compile(rf"{HEX}|{FUNC}", re.I)
URL = re.compile(r"url\([^)]*\)", re.I)
# A CSS declaration: `prop: value` that ends in ";" or "}" (so not a selector).
DECL = re.compile(r"[\w-]+\s*:([^;{}]*)(?=[;}]|\Z)")
# A colour property in a UI file: CSS, a JS object or style, an SVG attribute.
UI_DECL = re.compile(
    r"(?i)(?:--[\w-]+|[\w-]*(?:colou?r|background|border|fill|stroke|shadow|outline|"
    r"caret|decoration|filter|rule|stop|flood)[\w-]*)\s*[:=]\s*[\"'`]?([^;{}\"'`<>\n]*)"
)
EXPR = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\$\{[^}]*\}", re.S)
BLOCKS = re.compile(r"(<(script|style)\b[^>]*>)(.*?)</\2\s*>", re.S | re.I)
TEXT_NODE = re.compile(r">([^<>]+)<")
ATTR = re.compile(r"\b(?:title|alt|placeholder|aria-label|label|value)\s*=\s*(\"[^\"]*\"|'[^']*')")
STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`")
CODE_ARG = re.compile(
    r"(?:(?:add|remove)EventListener|querySelector(?:All)?|closest|matches|getElementById|"
    r"dispatchEvent|CustomEvent|Event|require|import)\s*\(\s*$|\b(?:from|import)\s*$"
)
KEYISH = re.compile(r"[^\s]*[#.:/\[_\-=][^\s]*|[a-z0-9]+|[A-Z0-9_]+")
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)|\bsrc\s*=\s*[\"']([^\"']+)")


def _found(text: str):
    """Colour literals in text; a # with 5 or 7 hex digits is not one."""
    return (c for c in COLOUR.finditer(text) if not re.fullmatch(r"#(.{5}|.{7})", c.group(0)))


def _alpha(s: str) -> float:
    return float(s[:-1]) / 100 if s.endswith("%") else float(s)


def _short(f: float) -> str:
    return f"{round(f, 2):g}"


def normalise(colour: str) -> str:
    """One spelling per colour: #rrggbb when opaque, else `rgb(r g b / a)`; hsl kept as hsl."""
    c = colour.strip().lower()
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        rgb, a = [int(h[i : i + 2], 16) for i in (0, 2, 4)], int(h[6:] or "ff", 16) / 255
    else:
        name, _, args = c.partition("(")
        parts = [p for p in re.split(r"[\s,/]+", args.rstrip(")").strip()) if p]
        try:
            a = _alpha(parts[3]) if len(parts) == 4 else 1.0
            if name.startswith("hsl"):
                h, s, li = parts[0].removesuffix("deg"), parts[1], parts[2]
                alpha = "" if round(a, 2) == 1 else f" / {_short(a)}"
                return f"hsl({_short(float(h))} {s} {li}{alpha})"
            rgb = [
                round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p))
                for p in parts[:3]
            ]
        except (ValueError, IndexError):
            return c
    if round(a, 2) == 1:
        return "#" + "".join(f"{v:02x}" for v in rgb)
    return f"rgb({' '.join(map(str, rgb))} / {_short(a)})"


def strip_comments(text: str, line_comments: bool = True, jinja: bool = False) -> str:
    """Comments blanked to spaces (newlines kept), so positions and lines stay right.

    Quoted strings are copied as they are, so "http://x" is not a comment."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            m = STRING.match(text, i)
            end = m.end() if m else i + 1
        else:
            end = None
            for start, stop in (("/*", "*/"), ("<!--", "-->"), ("{#", "#}")):
                if text.startswith(start, i) and (jinja or start != "{#"):
                    j = text.find(stop, i + len(start))
                    end = n if j < 0 else j + len(stop)
                    break
            if end is None and line_comments and text.startswith("//", i):
                if i == 0 or text[i - 1].isspace():
                    j = text.find("\n", i)
                    end = n if j < 0 else j
            if end is not None:
                out.append(_spaces(text[i:end]))
                i = end
                continue
            end = i + 1
        out.append(text[i:end])
        i = end
    return "".join(out)


def _spaces(s: str) -> str:
    return re.sub(r"[^\n]", " ", s)


def _blank(m: re.Match) -> str:
    return _spaces(m.group(0))


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _glob(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            ch = pattern[i]
            out += "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch)
            i += 1
    return re.compile(out + r"\Z")


def find(root: Path, patterns: list[str]) -> list[Path]:
    regexes = [_glob(p) for p in patterns]
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            rel = (Path(folder) / name).relative_to(root).as_posix()
            if not SKIP_FILES.search(name) and any(r.match(rel) for r in regexes):
                found.append(root / rel)
    return found


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def colours(root: Path, path: Path, known: set[str], ui: bool) -> list[str]:
    lines = ui or path.suffix in (".scss", ".less")
    text = strip_comments(read(path), line_comments=lines, jinja=path.suffix == ".html")
    text = URL.sub(_blank, text)  # url(#id) is not a colour
    out = []
    for decl in (UI_DECL if ui else DECL).finditer(text):
        for c in _found(decl.group(1)):
            if normalise(c.group(0)) not in known:
                pos = decl.start(1) + c.start()
                out.append(
                    f"{rel(root, path)}:{_line(text, pos)}: colour {c.group(0)} is not in tokens.md"
                )
    return out


def avoided(tone: str) -> list[str]:
    """The phrases in every Markdown table column headed "Avoid"."""
    phrases, column = [], None
    for line in tone.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            column = None
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if column is None:
            names = [c.strip("*_ ").lower() for c in cells]
            column = names.index("avoid") if "avoid" in names else -1
        elif column >= 0 and column < len(cells) and not set(cells[column]) <= set("-: "):
            for p in re.split(r"[,·;]", cells[column]):
                p = p.strip().strip("`\"'“”‘’*_ ").strip()
                if p and p not in ("—", "-") and p.lower() not in phrases:
                    phrases.append(p.lower())
    return phrases


def _script_texts(code: str, offset: int):
    """(position, text) of the JS strings that may be shown to a person."""
    for m in STRING.finditer(code):
        body = m.group(0)[1:-1]
        if CODE_ARG.search(code[max(0, m.start() - 40) : m.start()]):
            continue
        if not re.search(r"\s", body.strip()) and KEYISH.fullmatch(body.strip() or "x"):
            continue
        yield offset + m.start(), body


def ui_texts(path: Path, text: str):
    """(position, text) of everything in a UI file a person may read."""
    text = EXPR.sub(_blank, text)
    if path.suffix in SCRIPT:
        yield from _script_texts(text, 0)
    if path.suffix not in MARKUP:
        return
    markup = text
    if path.suffix not in SCRIPT:  # HTML, Vue, Svelte: <script> and <style> blocks
        for m in BLOCKS.finditer(text):
            if m.group(2).lower() == "script":
                yield from _script_texts(m.group(3), m.start(3))
            markup = markup[: m.start(3)] + _spaces(m.group(3)) + markup[m.end(3) :]
    for m in TEXT_NODE.finditer(markup):
        yield m.start(1), m.group(1)
    for m in ATTR.finditer(markup):
        yield m.start(1), m.group(1)[1:-1]


def words(root: Path, path: Path, phrases: list[re.Pattern]) -> list[str]:
    text = strip_comments(read(path), jinja=path.suffix == ".html")
    out, seen = [], set()
    for pos, t in ui_texts(path, text):
        for p in phrases:
            m = p.search(t)
            if m:
                line = _line(text, pos + m.start())
                if (line, p.pattern) not in seen:
                    seen.add((line, p.pattern))
                    out.append(
                        f'{rel(root, path)}:{line}: "{m.group(0)}" is avoided in tone-of-voice.md'
                    )
    return out


def icons(root: Path, design: Path) -> list[str]:
    md = design / "icons.md"
    svgs = sorted((design / "icons").glob("*.svg")) if (design / "icons").is_dir() else []
    if not md.is_file():
        return [f"{rel(root, md)}:1: missing, but icons/ has {len(svgs)} .svg"] if svgs else []
    text = read(md)
    out, refs = [], set()
    for m in IMAGE.finditer(text):
        ref = urllib.parse.unquote(m.group(1) or m.group(2))
        refs.add(ref.removeprefix("./"))
        if "://" not in ref and not ref.startswith("data:") and not (design / ref).is_file():
            out.append(f"{rel(root, md)}:{_line(text, m.start())}: {ref} does not exist")
    plain = urllib.parse.unquote(text)
    for svg in svgs:
        if f"icons/{svg.name}" not in refs and f"icons/{svg.name}" not in plain:
            out.append(f"{rel(root, svg)}:1: not referenced in icons.md")
    return out


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    ap = argparse.ArgumentParser(
        description="Check colours, avoided words and icons against the design folder."
    )
    ap.add_argument("root", nargs="?", default=".", help="repository root (default .)")
    ap.add_argument("--design", default="docs/design", help="design folder, from the root")
    ap.add_argument(
        "--css", nargs="+", metavar="GLOB", help="CSS files (default **/*.css, .scss, .less)"
    )
    ap.add_argument(
        "--ui",
        nargs="+",
        metavar="GLOB",
        help="UI files (default **/*.html, .js, .mjs, .ts, .jsx, .tsx, .vue, .svelte)",
    )
    a = ap.parse_args()
    root = Path(a.root).resolve()
    design = root / a.design
    if not design.is_dir():
        print(
            f"design_check: no {rel(root, design)} folder in {root}. Write the design "
            "system there first (tokens.md, tone-of-voice.md, icons.md, icons/), as the "
            "agent-artifacts skill says, then run this again.",
            file=sys.stderr,
        )
        sys.exit(2)

    problems: list[str] = []
    tokens, tone = design / "tokens.md", design / "tone-of-voice.md"
    known = None
    if tokens.is_file():
        known = {normalise(c.group(0)) for c in _found(read(tokens))}
    else:
        problems.append(f"{rel(root, tokens)}:1: missing, colours not checked")
    phrases = avoided(read(tone)) if tone.is_file() else []
    if not phrases:
        problems.append(f'{rel(root, tone)}:1: no table with an "Avoid" column')
    patterns = [
        re.compile(r"(?<![\w-])" + r"\s+".join(map(re.escape, p.split())) + r"(?![\w-])", re.I)
        for p in phrases
    ]

    css_files, ui_files = find(root, a.css or CSS), find(root, a.ui or UI)
    for f in css_files:
        problems += colours(root, f, known, ui=False) if known is not None else []
    for f in ui_files:
        problems += colours(root, f, known, ui=True) if known is not None else []
        problems += words(root, f, patterns)
    problems += icons(root, design)

    for p in problems:
        print(p)
    files = len(css_files) + len(ui_files)
    if problems:
        print(
            f"design_check: {len(problems)} problem(s) in {files} files; fix the code or, "
            f"if the change was agreed, update {rel(root, design)}/"
        )
        sys.exit(1)
    print(
        f"design_check: ok, {files} files against {len(known or ())} colours, "
        f"{len(phrases)} avoided phrases"
    )


if __name__ == "__main__":
    main()
