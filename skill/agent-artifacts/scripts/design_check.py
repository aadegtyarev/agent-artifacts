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
  tokens.md. Spelling does not matter: #FFF, #ffffff, rgb(255,255,255) and
  rgb(255 255 255 / 1) are one colour. Comments are skipped.
- words: tone-of-voice.md has a Markdown table with a column named "Avoid";
  its cells list phrases separated by "," or "·" (backticks and quotes
  around them are dropped). None of them may appear, as whole words in any
  case, in a string literal or a text node of a UI file. Lines that are
  only a comment are skipped, and so are {{ }}, {% %} and ${ } expressions.
- icons: every .svg in icons/ is referenced in icons.md, and every image
  icons.md references exists.

Exit status: 0 clean, 1 problems found, 2 usage error or no design folder.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

CSS = ["**/*.css"]
UI = ["**/*.html", "**/*.js", "**/*.jsx", "**/*.tsx", "**/*.vue", "**/*.svelte"]
SKIP_DIRS = set("node_modules .venv venv dist build docs vendor tests test __tests__".split())
SKIP_FILES = re.compile(r"\.(min|umd|bundle)\.(css|js)$")  # built or vendored

HEX = r"#[0-9a-fA-F]{3,8}(?![\w-])"
FUNC = r"(?:rgba?|hsla?)\([^()]*\)"
COLOUR = re.compile(rf"{HEX}|{FUNC}", re.I)
# A CSS declaration: `prop: value` that ends in ";" or "}" (so not a selector).
DECL = re.compile(r"[\w-]+\s*:([^;{}]*)(?=[;}]|\Z)")
# A colour property in a UI file: CSS, a JS object or style, an SVG attribute.
UI_DECL = re.compile(
    r"(?i)(?:--[\w-]+|[\w-]*(?:colou?r|background|border|fill|stroke|shadow|outline|"
    r"caret|decoration|filter|rule|stop|flood)[\w-]*)\s*[:=]\s*[\"'`]?([^;{}\"'`<>\n]*)"
)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->", re.S)
LINE_COMMENT = re.compile(r"^[ \t]*(//|/\*|\*|<!--|\{#).*$", re.M)
EXPR = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\$\{[^}]*\}")
TEXT_NODE = re.compile(r">([^<>]+)<")
STRINGS = {
    '"': re.compile(r'"((?:[^"\\\n]|\\.)*)"'),
    "`": re.compile(r"`((?:[^`\\\n]|\\.)*)`"),
    "'": re.compile(r"'((?:[^'\\\n]|\\.)*)'"),
}
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)|\bsrc\s*=\s*[\"']([^\"']+)")


def _found(text: str):
    """Colour literals in text; a # with 5 or 7 hex digits is not one."""
    return (c for c in COLOUR.finditer(text) if not re.fullmatch(r"#(.{5}|.{7})", c.group(0)))


def _num(s: str) -> str:
    """'0.120' -> '0.12', '.5' -> '0.5', '50%' stays."""
    if s.endswith("%"):
        return _num(s[:-1]) + "%"
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f == int(f) else repr(f)


def normalise(colour: str) -> str:
    """One spelling per colour: lowercase #rrggbb[aa], or `rgb(r g b / a)`."""
    c = colour.strip().lower()
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        if len(h) == 8 and h.endswith("ff"):
            h = h[:6]
        return "#" + h
    name, _, args = c.partition("(")
    parts = [_num(p) for p in re.split(r"[\s,/]+", args.rstrip(")").strip()) if p]
    name = name.rstrip("a")  # rgba -> rgb, hsla -> hsl
    if len(parts) == 4 and parts[3] in ("1", "100%"):
        parts = parts[:3]
    if name == "rgb" and len(parts) == 3 and all(p.isdigit() and int(p) < 256 for p in parts):
        return "#" + "".join(f"{int(p):02x}" for p in parts)
    alpha = f" / {parts[3]}" if len(parts) == 4 else ""
    return f"{name}({' '.join(parts[:3])}{alpha})"


def _blank(m: re.Match) -> str:
    """A comment replaced by its newlines, so line numbers stay right."""
    return "\n" * m.group(0).count("\n")


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
    return path.read_text(encoding="utf-8", errors="replace")


def colours(root: Path, path: Path, known: set[str], ui: bool) -> list[str]:
    text = BLOCK_COMMENT.sub(_blank, read(path))
    if ui:
        text = LINE_COMMENT.sub("", text)
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


def words(root: Path, path: Path, phrases: list[re.Pattern]) -> list[str]:
    quotes = '"`' if path.suffix == ".html" else "\"`'"
    out = []
    for n, line in enumerate(read(path).splitlines(), 1):
        if LINE_COMMENT.match(line):
            continue
        texts = [m.group(1) for m in TEXT_NODE.finditer(line)]
        texts += [m.group(1) for q in quotes for m in STRINGS[q].finditer(line)]
        seen = set()
        for t in texts:
            t = EXPR.sub(" ", t)
            for p in phrases:
                if p.pattern not in seen and p.search(t):
                    seen.add(p.pattern)
                    out.append(
                        f'{rel(root, path)}:{n}: "{p.search(t).group(0)}" is '
                        "avoided in tone-of-voice.md"
                    )
    return out


def icons(root: Path, design: Path) -> list[str]:
    md = design / "icons.md"
    svgs = sorted((design / "icons").glob("*.svg")) if (design / "icons").is_dir() else []
    if not md.is_file():
        return [f"{rel(root, md)}:1: missing, but icons/ has {len(svgs)} .svg"] if svgs else []
    text = read(md)
    out = []
    for m in IMAGE.finditer(text):
        ref = m.group(1) or m.group(2)
        if "://" not in ref and not ref.startswith("data:") and not (design / ref).is_file():
            out.append(f"{rel(root, md)}:{_line(text, m.start())}: {ref} does not exist")
    for svg in svgs:
        if f"icons/{svg.name}" not in text:
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
    ap.add_argument("--css", nargs="+", metavar="GLOB", help=f"CSS files (default {CSS[0]})")
    ap.add_argument(
        "--ui",
        nargs="+",
        metavar="GLOB",
        help="UI files (default **/*.html, .js, .jsx, .tsx, .vue, .svelte)",
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
    known = set()
    if tokens.is_file():
        known = {normalise(c.group(0)) for c in _found(read(tokens))}
    else:
        problems.append(f"{rel(root, tokens)}:1: missing")
    phrases = avoided(read(tone)) if tone.is_file() else []
    if not phrases:
        problems.append(f'{rel(root, tone)}:1: no table with an "Avoid" column')
    patterns = [
        re.compile(r"(?<![\w-])" + r"\s+".join(map(re.escape, p.split())) + r"(?![\w-])", re.I)
        for p in phrases
    ]

    css_files, ui_files = find(root, a.css or CSS), find(root, a.ui or UI)
    for f in css_files:
        problems += colours(root, f, known, ui=False)
    for f in ui_files:
        problems += colours(root, f, known, ui=True) + words(root, f, patterns)
    problems += icons(root, design)

    for p in problems:
        print(p)
    files = len(css_files) + len(ui_files)
    if problems:
        print(
            f"design_check: {len(problems)} problem(s) in {files} files; fix the code or, "
            f"if the change was agreed, {rel(root, design)}/"
        )
        sys.exit(1)
    print(
        f"design_check: ok, {files} files against {len(known)} colours, "
        f"{len(phrases)} avoided phrases"
    )


if __name__ == "__main__":
    main()
