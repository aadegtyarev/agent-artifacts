"""The HTML the service serves, from Jinja templates in templates/, and the
CSPs that go with each kind of page. The pages' own CSS and script are files
in static/, served from /static."""

from __future__ import annotations

import codecs
import hashlib
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from starlette.responses import HTMLResponse

from . import kinds
from .api import iso
from .store import Artifact, Invite, Project

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"

_env = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
    auto_reload=False,  # the templates ship with the code
)


def _static_url(name: str) -> str:
    """/static/<name>?v=<hash of its content>: a new deploy gets a new URL, so
    a browser never pairs a new page with a stale cached file."""
    digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12]
    return f"/static/{name}?v={digest}"


_env.globals["static"] = {
    n: _static_url(n)
    for n in (
        "pin.css", "site.css", "theme.js", "copy.js",
        "pin-marks.js", "comments-model.js", "sheet-drag.js", "links.js", "zoom.js", "review-keys.js",
        "comments-pins.js", "comments.js", "float-tip.js", "viewer.js", "menu-dialogs.js",
        "project.js", "local-dates.js", "account-notices.js", "account-dashboard.js", "account-setup.js",
    )
}

SANDBOX = (
    "allow-scripts allow-forms allow-modals allow-popups "
    "allow-popups-to-escape-sandbox allow-downloads"
)
# For untrusted HTML, SVG, Markdown and text: an opaque origin that loads
# nothing from the network, so the content cannot read this site or fetch
# from another. It can still navigate away (a link, its own frame, a popup
# allowed to leave the sandbox), and a URL can carry data out.
SANDBOX_CSP = (
    f"sandbox {SANDBOX}; default-src 'none'; form-action 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
    "media-src data: blob:; frame-ancestors 'self'"
)

# Images and PDFs: not sandboxed (browsers won't show a PDF in a sandbox),
# but only this site may frame them.
RAW_CSP = "frame-ancestors 'self'"

# The service's own pages: their CSS and script come from /static only.
_OWN = "default-src 'none'; script-src 'self'; style-src 'self'; base-uri 'none'"
# Error and other message pages show only text.
MESSAGE_CSP = f"{_OWN}; frame-ancestors 'none'; form-action 'none'"
# The viewer frames the content and calls the comment API from its script.
VIEWER_CSP = (
    f"{_OWN}; img-src 'self'; frame-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; form-action 'none'"
)
# The project page lists links and asks for its counters from its script.
PROJECT_CSP = f"{_OWN}; connect-src 'self'; frame-ancestors 'none'; form-action 'none'"
# The invite page posts its form back to itself.
JOIN_CSP = f"{_OWN}; frame-ancestors 'none'; form-action 'self'"
SIGN_IN_CSP = f"{JOIN_CSP} https://accounts.google.com"

REPO_URL = "https://github.com/aadegtyarev/agent-artifacts"
# Raw, so an agent fetching it gets Markdown rather than a GitHub page.
SETUP_URL = "https://raw.githubusercontent.com/aadegtyarev/agent-artifacts/main/docs/agent-setup.md"
USER_GUIDE_URL = f"{REPO_URL}/blob/main/docs/user-guide.md"

INVITE_GONE = "This invite was used, has expired or never existed. Ask for a new one."

# Our vendored file, put into a <script> as is.
_MARKED_JS = Markup((STATIC_DIR / "marked.umd.js").read_text(encoding="utf-8"))  # noqa: S704
# Which links open another page of the service, see static/links.js.
_LINKS_JS = (STATIC_DIR / "links.js").read_text(encoding="utf-8")
# The script that draws pins inside a sandboxed page, see static/pins.js,
# after the badge marks, the links, the zoom and the pin styles it shares with the
# viewer. One block around all, so their names are local to it and cannot
# clash with the page's own. The styles go in as a JS string; "<" escaped, so
# no "</script>" can end the block early.
_PIN_CSS = json.dumps((STATIC_DIR / "pin.css").read_text(encoding="utf-8")).replace("<", "\\u003c")
_PINS = Markup(  # noqa: S704
    "<script>{\n"
    + (STATIC_DIR / "pin-marks.js").read_text(encoding="utf-8")
    + _LINKS_JS
    + (STATIC_DIR / "zoom.js").read_text(encoding="utf-8")
    + (STATIC_DIR / "review-keys.js").read_text(encoding="utf-8")
    + f"const pinCss = {_PIN_CSS};\n"
    + (STATIC_DIR / "pins.js").read_text(encoding="utf-8")
    + "}</script>"
)
PINS_SCRIPT = str(_PINS).encode()


def _page(template: str, csp: str, status: int = 200, **context: object) -> HTMLResponse:
    return HTMLResponse(
        _env.get_template(template).render(**context),
        status_code=status,
        headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
    )


def viewer_page(
    a: Artifact,
    url: str,
    separate: str | None = None,
    project: tuple[Project, list[Artifact]] | None = None,
    now: float = 0,
    workspace_author: str | None = None,
    workspace_marker: str | None = None,
    review_state: str | None = None,
    version_id: str | None = None,
) -> HTMLResponse:
    """The page at /a/{id}: a one-line header and the content below.

    A Drive viewer loads its displayed revision even if the shared file is
    replaced after this response. The page URL and comment scope stay stable.
    """
    content_path = f"/a/{a.id}"
    if version_id is not None:
        content_path += f"/versions/{version_id}"
    page = _page(
        "viewer.html",
        VIEWER_CSP,
        title=a.title,
        viewer=True,
        workspace_author=workspace_author,
        workspace_marker=workspace_marker,
        separate=separate,
        review_state=review_state,
        a=a,
        url=url,
        raw=f"{content_path}/raw",
        download=f"{content_path}/download",
        version_id=version_id,
        kind=kinds.get(a.kind),
        sandbox=SANDBOX,
        size=size(a.size),
        expires=iso(a.expires_at, human=True),
        expires_iso=iso(a.expires_at),
        in_project=project and project[0],
        rows=project and file_rows(project[1], now),
    )
    # Cuts window.opener for pages a sandboxed artifact opens, so they cannot
    # navigate this tab away.
    page.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return page


def project_page(p: Project, files: list[Artifact], url: str, now: float,
                 workspace_author: str | None = None) -> HTMLResponse:
    """The page at /p/{id}: the project's files in its order, a heading
    before each section; the counters come from its script."""
    count = f"{len(files)} file" + ("" if len(files) == 1 else "s")
    page = _page(
        "project.html",
        PROJECT_CSP,
        title=p.title,
        project=p,
        workspace_author=workspace_author,
        rows=file_rows(files, now),
        count=count,
        url=url,
    )
    page.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return page


def file_rows(files: list[Artifact], now: float) -> list[dict]:
    """The lines of a project's file list (the project page and a file's
    header), in the project's order, a heading before each section."""
    rows, section = [], None
    for a in files:
        rows.append(
            {
                "a": a,
                "type": a.kind.upper(),
                "size": size(a.size),
                "date": ago(a.updated_at, now),
                "date_iso": iso(a.updated_at) if now - a.updated_at >= 86400 else None,
                # A heading before the first file of each section.
                "heading": a.section if a.section != section else None,
            }
        )
        section = a.section
    return rows


def account_page(template: str, status: int, **context: object) -> HTMLResponse:
    csp = f"{JOIN_CSP}; connect-src 'self'" if context.get("account_setup") else JOIN_CSP
    response = _page(template, csp, status, **context)
    # Same-origin forms send Origin on POST. No credential is in a page URL.
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def sign_in_page(domain: str, destination: str, *, email: str | None = None,
                 title: str = "Sign in", status: int = 200, account_mode: bool = False,
                 drive_consent: bool = False) -> HTMLResponse:
    response = _page(
        "sign-in.html", SIGN_IN_CSP, status, title=title, domain=domain,
        destination=destination, email=email, drive_consent=drive_consent, retry=status >= 400, account_mode=account_mode,
    )
    # Don't send the callback query to subresources. Only the signed-in
    # landing page needs a same-origin policy for the logout form's Origin.
    response.headers["Referrer-Policy"] = "same-origin" if email is not None else "no-referrer"
    return response


def invite_page(invite: Invite) -> HTMLResponse:
    """GET /join/{token}: who it is for, and the button that uses it."""
    return _page(
        "invite.html",
        JOIN_CSP,
        title=f"Invite for {invite.name}",
        name=invite.name,
        expires=iso(invite.expires_at, human=True),
        expires_iso=iso(invite.expires_at),
    )


def key_setup(key: str, public_url: str) -> str:
    return (f"Set up agent-artifacts for me: follow {SETUP_URL}\n"
            f"AGENT_ARTIFACTS_URL={public_url}\nAGENT_ARTIFACTS_KEY={key}")


def key_page(name: str, key: str, public_url: str, *, account_back: bool = False) -> HTMLResponse:
    """Reveal a new key once, as one text to paste into an agent."""
    return _page(
        "key.html",
        JOIN_CSP,
        title="Your key",
        copy=True,
        name=name,
        setup=key_setup(key, public_url),
        user_guide=USER_GUIDE_URL,
        account_back=account_back,
    )


def message(status: int, text: str, *, csp: str = MESSAGE_CSP) -> HTMLResponse:
    return _page("message.html", csp, status, title=text)


def not_found() -> HTMLResponse:
    return message(404, "This link has expired or never existed.")


# How much of a file a page reads at a time: pages go out in pieces, so a
# big file viewed many times at once never sits whole in memory.
CHUNK_BYTES = 64 * 1024
# Where the file's text goes in a rendered template, split there. No title can
# hold it (no control characters), and the split is at the last one, after
# which only our own markup follows.
_HOLE = "\x00file\x00"

Page = tuple[Iterator[bytes], int | None]


def html_page(f: BinaryIO, title: str, size: int) -> Page:
    """An HTML artifact for /raw inside the sandbox: the file with the pin
    script appended; the download stays the file as it is. (The title is the
    page's own; the argument is there to match the other builders.) The pieces
    and the length, known here; FileNotFoundError at once if it is gone."""
    length = size + len(PINS_SCRIPT)
    return _around(_chunks(f), "", PINS_SCRIPT.decode()), length


def text_page(f: BinaryIO, title: str) -> Page:
    """A text artifact, for /raw inside the sandbox; no length, the escaping
    changes it."""
    page = _env.get_template("text.html").render(
        title=title,
        text=Markup(_HOLE),  # noqa: S704 (our own marker)
        pins=_PINS,
    )
    head, _, tail = page.rpartition(_HOLE)
    return _around((str(escape(t)) for t in _text(f)), head, tail), None


def markdown_page(f: BinaryIO, title: str) -> Page:
    """A Markdown artifact and its renderer, for /raw inside the sandbox."""
    page = _env.get_template("markdown.html").render(
        title=title,
        marked=_MARKED_JS,
        source=Markup(f'"{_HOLE}"'),  # noqa: S704 (our own marker)
        links=Markup(_LINKS_JS),  # noqa: S704 (our own file)
        pins=_PINS,
    )
    head, _, tail = page.rpartition(_HOLE)
    return _around((_js_string(t) for t in _text(f)), head, tail), None


def _js_string(text: str) -> str:
    """A piece of the Markdown as the inside of a JS string literal; escaped
    character by character, so the pieces join into the whole. Non-ASCII kept
    as is, so the page is about the size of the file (escaped, Cyrillic would
    grow sixfold on every view). "<" escaped so the text cannot close the
    <script> it sits in; the two line separators for older JavaScript engines."""
    return (
        json.dumps(text, ensure_ascii=False)[1:-1]
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _chunks(f: BinaryIO) -> Iterator[bytes]:
    with f:
        while chunk := f.read(CHUNK_BYTES):
            yield chunk


def _text(f: BinaryIO) -> Iterator[str]:
    """The file as UTF-8 text in pieces, a bad byte as U+FFFD, the same as
    decoding it whole; a character split between chunks is joined."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    for chunk in _chunks(f):
        yield decoder.decode(chunk)
    yield decoder.decode(b"", final=True)


def _around(body: Iterable[bytes | str], head: str, tail: str) -> Iterator[bytes]:
    yield head.encode()
    for piece in body:
        yield piece if isinstance(piece, bytes) else piece.encode()
    yield tail.encode()


def size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def ago(ts: float, now: float) -> str:
    """How long ago, as the viewer says it (see when() in comments-model.js):
    minutes and hours, then the date (UTC)."""
    seconds = now - ts
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    t, today = datetime.fromtimestamp(ts, UTC), datetime.fromtimestamp(now, UTC)
    day = f"{t:%b} {t.day}"
    return day if t.year == today.year else f"{day}, {t.year}"
