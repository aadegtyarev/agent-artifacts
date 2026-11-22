"""The viewer scripts repeat the server's input limits so that a comment is
cut or refused before it is sent. These checks read the scripts as text and
fail when a limit there no longer matches the server's: each number is taken
from the code around it, so a moved or rewritten check fails loudly too."""

import re
from pathlib import Path

from agent_artifacts import comments, store

PACKAGE = Path(comments.__file__).parent
COMMENTS_JS = (PACKAGE / "static" / "comments.js").read_text()
VIEWER_PINS_JS = (PACKAGE / "static" / "comments-pins.js").read_text()
PINS_JS = (PACKAGE / "static" / "pins.js").read_text()
VIEWER_HTML = (PACKAGE / "templates" / "viewer.html").read_text()


def one(pattern: str, text: str) -> tuple[str, ...]:
    found = re.findall(pattern, text, re.DOTALL)
    assert len(found) == 1, f"expected one match of {pattern!r}, found {len(found)}"
    return found[0] if isinstance(found[0], tuple) else (found[0],)


def number(pattern: str, text: str) -> int:
    return int(one(pattern, text)[0])


def test_author_name_and_key():
    assert number(r"\.trim\(\)\]\.slice\(0, (\d+)\)\.join", COMMENTS_JS) == comments.MAX_AUTHOR
    assert number(r'id="name-input"[^>]*maxlength="(\d+)"', VIEWER_HTML) == comments.MAX_AUTHOR
    key_re = one(r"if \(!/\^(.+?)\$/\.test\(authorKey", COMMENTS_JS)[0]
    assert key_re == comments._AUTHOR_KEY_RE.pattern
    assert number(r'replace\(/\\//g, "_"\)\.slice\(0, (\d+)\)', COMMENTS_JS) == 22


def test_comment_text():
    assert number(r"area\.maxLength = (\d+);", COMMENTS_JS) == comments.MAX_TEXT


def test_anchor_checks_in_the_viewer():
    js = one(r"const isAnchor = (.+?);\n", VIEWER_PINS_JS)[0]
    assert number(r"\[\.\.\.a\.selector\]\.length <= (\d+)", js) == comments.MAX_SELECTOR
    assert number(r"\[\.\.\.a\.text\]\.length <= (\d+)", js) == comments.MAX_ANCHOR_TEXT
    assert number(r"a\.width <= (\d+)", js) == comments.MAX_WIDTH
    assert number(r"\[\.\.\.a\.screen\]\.length <= (\d+)", js) == comments.MAX_SCREEN
    assert number(r"\[\.\.\.a\.hash\]\.length <= (\d+)", js) == comments.MAX_HASH
    assert one(r"/\^(#.+?)\$/\.test\(a\.hash\)", js)[0] == comments._HASH_RE.pattern


def test_anchors_made_and_checked_in_the_frame():
    assert number(r"return length\(best\) <= (\d+) \? best", PINS_JS) == comments.MAX_SELECTOR
    assert number(r'el\.title \|\| "", (\d+)\);', PINS_JS) == comments.MAX_ANCHOR_TEXT
    assert number(r'document\.title \|\| "", (\d+)\);', PINS_JS) == comments.MAX_SCREEN
    length, hash_re = one(
        r"const isHash = .+?h\.length <= (\d+) && /\^(#.+?)\$/\.test\(h\)", PINS_JS
    )
    assert int(length) == comments.MAX_HASH
    assert hash_re == comments._HASH_RE.pattern
    js = one(r"const isAnchor = (.+?);\n", PINS_JS)[0]
    assert number(r"length\(a\.selector\) <= (\d+)", js) == comments.MAX_SELECTOR
    assert number(r"length\(a\.text\) <= (\d+)", js) == comments.MAX_ANCHOR_TEXT


def test_both_scripts_refuse_what_the_server_refuses():
    # The scripts turn every refused character into a space, line breaks and
    # tabs too: the server refuses those in one-line fields.
    server = comments._REFUSED.pattern
    js = server.replace(r"\x00-\x08\x0b\x0c\x0e-\x1f", r"\u0000-\u001f").replace(r"\x7f-\x9f", r"\u007f-\u009f")
    assert one(r"const cleanName = .+?\.replace\(/(\[.+?\])\+/gu", COMMENTS_JS)[0] == js
    assert one(r"const clean = .+?\.replace\(/(\[.+?\])\+/gu", PINS_JS)[0] == js


def test_frame_keys_agree():
    key = r"const isKey = \(k\) => ([^\n]+);\n"
    assert one(key, VIEWER_PINS_JS) == one(key, PINS_JS)


def test_links_between_files_take_ids_as_the_server_makes_them():
    js = (PACKAGE / "static" / "links.js").read_text()
    assert one(r"const PAGE = /\^\\/\(a\|p\)\\/(.+?)\$/;", js)[0] == store.ID_RE
