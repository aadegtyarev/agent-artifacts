"""Which files are accepted, how they are served, and checking a file's
content against its name."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    name: str
    mime: str
    # Served from /raw with the sandbox CSP and shown in a sandboxed iframe.
    sandboxed: bool = False
    # Shown inline on the viewer page; archives are download-only.
    viewable: bool = True
    magic: tuple[bytes, ...] = ()
    text: bool = False


_KINDS = {
    "html": Kind("html", "text/html; charset=utf-8", sandboxed=True, text=True),
    "md": Kind("md", "text/html; charset=utf-8", sandboxed=True, text=True),
    "txt": Kind("txt", "text/plain; charset=utf-8", sandboxed=True, text=True),
    "svg": Kind("svg", "image/svg+xml", sandboxed=True, text=True),
    "pdf": Kind("pdf", "application/pdf", magic=(b"%PDF-",)),
    "png": Kind("png", "image/png", magic=(b"\x89PNG\r\n\x1a\n",)),
    "jpeg": Kind("jpeg", "image/jpeg", magic=(b"\xff\xd8\xff",)),
    "gif": Kind("gif", "image/gif", magic=(b"GIF87a", b"GIF89a")),
    "webp": Kind("webp", "image/webp"),
    "zip": Kind("zip", "application/zip", viewable=False, magic=(b"PK\x03\x04", b"PK\x05\x06")),
    "tgz": Kind("tgz", "application/gzip", viewable=False, magic=(b"\x1f\x8b",)),
}

_EXTENSIONS = {
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".txt": "txt",
    ".svg": "svg",
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
    ".zip": "zip",
    ".tar.gz": "tgz",
    ".tgz": "tgz",
}

MAX_NAME = 200


class KindError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def get(name: str) -> Kind:
    return _KINDS[name]


def clean_name(raw: str) -> str:
    """A safe base name: no directories, no control characters, bounded length.
    The extension is kept when the name is shortened."""
    name = unicodedata.normalize("NFC", raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f\"]", "", name).strip().lstrip(".")
    if len(name) > MAX_NAME:
        ext = _extension(name) or ""
        name = name[: MAX_NAME - len(ext)] + ext
    if not name:
        raise KindError("unsupported_type", "file name is empty")
    return name


def detect(name: str, head: bytes) -> Kind:
    """The kind for ``name``, checked against the first bytes of the file."""
    ext = _extension(name)
    if ext is None:
        allowed = ", ".join(sorted(_EXTENSIONS))
        raise KindError("unsupported_type", f"allowed extensions: {allowed}")
    kind = _KINDS[_EXTENSIONS[ext]]
    if kind.text:
        ok = _is_utf8_text(head)
    elif kind.name == "webp":
        ok = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    else:
        ok = head.startswith(kind.magic)
    if not ok:
        raise KindError("type_mismatch", f"content is not a valid {ext} file")
    return kind


def _extension(name: str) -> str | None:
    lower = name.lower()
    for ext in sorted(_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(ext) and len(lower) > len(ext):
            return ext
    return None


def _is_utf8_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte character cut at the end of the sample is fine.
        return e.start >= len(head) - 3 and e.reason == "unexpected end of data"
    return True
