"""The HTML the service serves, from Jinja templates in templates/, and the
CSPs that go with each kind of page. The pages' own CSS and script are files
in static/, served from /static."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from starlette.responses import HTMLResponse

from . import kinds
from .api import iso
from .store import Artifact, Invite

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
    n: _static_url(n) for n in ("site.css", "theme.js", "copy.js", "comments.js", "viewer.js")
}

SANDBOX = (
    "allow-scripts allow-forms allow-modals allow-popups "
    "allow-popups-to-escape-sandbox allow-downloads"
)
# For untrusted HTML, SVG, Markdown and text: an opaque origin with no
# network access, so the content can neither read this site nor phone home.
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
# The invite page posts its form back to itself.
JOIN_CSP = f"{_OWN}; frame-ancestors 'none'; form-action 'self'"

REPO_URL = "https://github.com/aadegtyarev/agent-artifacts"
# Raw, so an agent fetching it gets Markdown rather than a GitHub page.
SETUP_URL = "https://raw.githubusercontent.com/aadegtyarev/agent-artifacts/main/docs/agent-setup.md"
USER_GUIDE_URL = f"{REPO_URL}/blob/main/docs/user-guide.md"

INVITE_GONE = "This invite was used, has expired or never existed. Ask for a new one."

# Our vendored file, put into a <script> as is.
_MARKED_JS = Markup((STATIC_DIR / "marked.umd.js").read_text(encoding="utf-8"))  # noqa: S704
# The script that draws pins inside a sandboxed page, see static/pins.js.
_PINS = Markup(f"<script>{(STATIC_DIR / 'pins.js').read_text(encoding='utf-8')}</script>")  # noqa: S704
PINS_SCRIPT = str(_PINS).encode()


def _page(template: str, csp: str, status: int = 200, **context: object) -> HTMLResponse:
    return HTMLResponse(
        _env.get_template(template).render(**context),
        status_code=status,
        headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
    )


def viewer_page(a: Artifact, url: str, separate: str | None = None) -> HTMLResponse:
    """The page at /a/{id}: a one-line header and the content below.
    ``separate`` is the file at the raw origin, for "Open separately"."""
    page = _page(
        "viewer.html",
        VIEWER_CSP,
        title=a.name,
        comments=True,
        viewer=True,
        separate=separate,
        a=a,
        url=url,
        raw=f"/a/{a.id}/raw",
        kind=kinds.get(a.kind),
        sandbox=SANDBOX,
        size=size(a.size),
        expires=iso(a.expires_at, human=True),
    )
    # Cuts window.opener for pages a sandboxed artifact opens, so they cannot
    # navigate this tab away.
    page.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return page


def invite_page(invite: Invite) -> HTMLResponse:
    """GET /join/{token}: who it is for, and the button that uses it."""
    return _page(
        "invite.html",
        JOIN_CSP,
        title=f"Invite for {invite.name}",
        name=invite.name,
        expires=iso(invite.expires_at, human=True),
    )


def key_page(name: str, key: str, public_url: str) -> HTMLResponse:
    """POST /join/{token}: the new key, once, as one text to paste into any
    agent: what to do, then the two settings."""
    setup = (
        f"Set up agent-artifacts for me: follow {SETUP_URL}\n"
        f"AGENT_ARTIFACTS_URL={public_url}\nAGENT_ARTIFACTS_KEY={key}"
    )
    return _page(
        "key.html",
        JOIN_CSP,
        title="Your key",
        copy=True,
        name=name,
        setup=setup,
        user_guide=USER_GUIDE_URL,
    )


def message(status: int, text: str, *, csp: str = MESSAGE_CSP) -> HTMLResponse:
    return _page("message.html", csp, status, title=text)


def not_found() -> HTMLResponse:
    return message(404, "This link has expired or never existed.")


def html_page(path: Path, title: str) -> bytes:
    """An HTML artifact for /raw inside the sandbox: the file with the pin
    script appended; the download stays the file as it is. (The title is the
    page's own; the argument is there to match the other builders.)"""
    return path.read_bytes() + PINS_SCRIPT


def text_page(path: Path, title: str) -> str:
    """A text artifact, for /raw inside the sandbox."""
    text = path.read_bytes().decode("utf-8", "replace")
    return _env.get_template("text.html").render(title=title, text=text, pins=_PINS)


def markdown_page(path: Path, title: str) -> str:
    """A Markdown artifact and its renderer, for /raw inside the sandbox."""
    # Non-ASCII kept as is, so the page is about the size of the file (escaped,
    # Cyrillic would grow sixfold on every view). "<" escaped so the text
    # cannot close the <script> it sits in; the two line separators for
    # older JavaScript engines.
    source = (
        json.dumps(path.read_bytes().decode("utf-8", "replace"), ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return _env.get_template("markdown.html").render(
        title=title,
        marked=_MARKED_JS,
        source=Markup(source),  # noqa: S704 (JSON with "<" escaped, see above)
        pins=_PINS,
    )


def size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
