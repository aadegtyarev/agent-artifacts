"""HTTP: the agent API under /api/v1 and the viewer pages under /a."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.convertors import StringConvertor, register_url_convertor
from starlette.datastructures import MutableHeaders, UploadFile
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route

from . import kinds
from .admin import admin_routes
from .api import ApiError, error, iso, on_api_error, require_agent, user_json
from .config import Config
from .store import ID_RE, TOKEN_RE, Artifact, NameTaken, QuotaExceeded, StorageFull, Store

PURGE_INTERVAL_SECONDS = 600


class _ArtifactId(StringConvertor):
    regex = ID_RE


register_url_convertor("artifact_id", _ArtifactId())


class _InviteToken(StringConvertor):
    regex = TOKEN_RE


register_url_convertor("invite_token", _InviteToken())
# Multipart framing around the file; the file itself is checked exactly later.
FORM_OVERHEAD_BYTES = 64 * 1024

SANDBOX = (
    "allow-scripts allow-forms allow-modals allow-popups "
    "allow-popups-to-escape-sandbox allow-downloads"
)
# For untrusted HTML, SVG, Markdown and text: an opaque origin with no
# network access, so the content can neither read this site nor phone home.
SANDBOX_CSP = (
    f"sandbox {SANDBOX}; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
    "media-src data: blob:; frame-ancestors 'self'"
)

# Images and PDFs: not sandboxed (browsers won't show a PDF in a sandbox),
# but only this site may frame them.
RAW_CSP = "frame-ancestors 'self'"

_COPY_JS = (
    "document.getElementById('copy').onclick=function(){"
    "var u=document.getElementById('url');"
    "(navigator.clipboard?navigator.clipboard.writeText(u.value):Promise.reject())"
    ".then(function(){var b=document.getElementById('copy');b.classList.add('done');"
    "setTimeout(function(){b.classList.remove('done')},1500)},"
    "function(){u.select()})};"
)
_COPY_JS_HASH = base64.b64encode(hashlib.sha256(_COPY_JS.encode()).digest()).decode()
VIEWER_CSP = (
    f"default-src 'none'; script-src 'sha256-{_COPY_JS_HASH}'; style-src 'unsafe-inline'; "
    "img-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)

# The invite pages: a form posting back to the same URL, and a copy button
# for the settings with the new key.
_SETUP_JS = (
    "document.getElementById('copy').onclick=function(){"
    "var t=document.getElementById('setup');"
    "(navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject())"
    ".then(function(){document.getElementById('copy').textContent='Copied'},"
    "function(){t.select()})};"
)
_SETUP_JS_HASH = base64.b64encode(hashlib.sha256(_SETUP_JS.encode()).digest()).decode()
JOIN_CSP = (
    f"default-src 'none'; script-src 'sha256-{_SETUP_JS_HASH}'; style-src 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
REPO_URL = "https://github.com/aadegtyarev/agent-artifacts"
# Raw, so an agent fetching it gets Markdown rather than a GitHub page.
SETUP_URL = "https://raw.githubusercontent.com/aadegtyarev/agent-artifacts/main/docs/agent-setup.md"
USER_GUIDE_URL = f"{REPO_URL}/blob/main/docs/user-guide.md"

# One compact header row: icon buttons, the name and details cut to one line each.
_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true">'
_DOWNLOAD_ICON = _SVG + '<path d="M12 4v11m-5-5 5 5 5-5M5 20h14"/></svg>'
_LINK_ICON = (
    _SVG + '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/>'
    '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>'
)
# Shown instead of the link icon for a moment after the link is copied.
_CHECK_ICON = (
    '<svg class="check" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>'
)

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --fg: #1d1d1f; --bg: #fff; --muted: #6e6e73;
  --line: #d2d2d7; --btn: #f2f2f4; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg: #f2f2f4; --bg: #1c1c1e;
  --muted: #a1a1a6; --line: #3a3a3c; --btn: #2c2c2e; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font: 15px/1.4 system-ui, sans-serif; color: var(--fg);
  background: var(--bg); display: flex; flex-direction: column; height: 100vh; }}
header {{ display: flex; gap: 8px; align-items: center; padding: 4px 8px 4px 12px;
  border-bottom: 1px solid var(--line); }}
.name {{ flex: 1; min-width: 0; }}
h1, .meta {{ margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
h1 {{ font-size: 14px; }}
.meta {{ color: var(--muted); font-size: 12px; }}
.actions {{ display: flex; gap: 4px; }}
a.btn, button {{ display: flex; align-items: center; justify-content: center;
  min-width: 32px; height: 32px; padding: 0 6px; font: 13px system-ui, sans-serif;
  color: inherit; background: var(--btn); border: 1px solid var(--line);
  border-radius: 6px; text-decoration: none; cursor: pointer; }}
svg {{ width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2;
  stroke-linecap: round; stroke-linejoin: round; }}
.check, .done svg {{ display: none; }}
.done .check {{ display: block; }}
#url {{ position: absolute; left: -9999px; }}
main {{ flex: 1; min-height: 0; }}
iframe {{ border: 0; width: 100%; height: 100%; background: #fff; }}
.center {{ display: flex; align-items: center; justify-content: center;
  height: 100%; padding: 16px; text-align: center; }}
img {{ max-width: 100%; max-height: 100%; cursor: zoom-in; }}
.center a {{ display: contents; }}
.join {{ max-width: 560px; margin: 0 auto; padding: 24px 16px; }}
.join h1 {{ font-size: 20px; margin-bottom: 12px; }}
.join button {{ height: auto; padding: 8px 16px; font-size: 15px; }}
textarea {{ width: 100%; height: 8.5em; word-break: break-all; color: inherit;
  font: 13px ui-monospace, monospace; background: var(--btn);
  border: 1px solid var(--line); border-radius: 6px; padding: 8px; resize: none; }}
</style></head><body>{body}</body></html>"""


def create_app(config: Config, store: Store | None = None) -> Starlette:
    store = store or Store(
        config.data_dir,
        max_total_bytes=config.max_total_bytes,
        user_quota_bytes=config.user_quota_bytes,
        ttl_seconds=config.ttl_seconds,
    )

    def as_json(a: Artifact) -> dict:
        return {
            "id": a.id,
            "url": f"{config.public_url}/a/{a.id}",
            "name": a.name,
            "type": a.kind,
            "size": a.size,
            "created_at": iso(a.created_at),
            "expires_at": iso(a.expires_at),
        }

    async def save(request: Request, agent: str, artifact_id: str | None) -> Response:
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return error(411, "bad_request", "Content-Length is required")
        if int(length) > config.max_file_bytes + FORM_OVERHEAD_BYTES:
            return error(413, "too_large", f"limit is {config.max_file_bytes} bytes")
        try:
            form = await request.form(max_files=1, max_fields=2, max_part_size=4096)
        except Exception:  # starlette raises several types for a malformed body
            return error(400, "bad_request", "expected multipart/form-data")
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return error(400, "bad_request", "multipart field 'file' is required")
            size = upload.size or 0
            if size > config.max_file_bytes:
                return error(413, "too_large", f"limit is {config.max_file_bytes} bytes")
            name_field = form.get("name")
            raw_name = name_field if isinstance(name_field, str) and name_field else upload.filename
            try:
                name = kinds.clean_name(raw_name or "")
                kind = kinds.detect(name, await upload.read(64 * 1024))
            except kinds.KindError as e:
                return error(415, e.code, str(e))
            if kind.name in ("md", "txt") and size > kinds.MAX_TEXT_BYTES:
                return error(413, "too_large", "Markdown and text are limited to 2 MB; use .zip")
            await upload.seek(0)
            try:
                artifact = await run_in_threadpool(
                    store.put,
                    upload.file,
                    owner=agent,
                    name=name,
                    kind=kind.name,
                    size=size,
                    artifact_id=artifact_id,
                )
            except QuotaExceeded as e:
                return error(
                    507,
                    "quota_exceeded",
                    f"your files take {_size(e.used)} of {_size(e.quota)} and this one needs "
                    f"{_size(size)}; delete files you no longer need (GET /api/v1/artifacts "
                    "lists them)",
                    used_bytes=e.used,
                    quota_bytes=e.quota,
                )
            except StorageFull:
                return error(507, "storage_full", "the service is out of space, try later")
        finally:
            await form.close()
        if artifact is None:
            return error(404, "not_found", "no such artifact")
        return JSONResponse(as_json(artifact), status_code=200 if artifact_id else 201)

    async def collection(request: Request) -> Response:
        agent = require_agent(store, request)
        if request.method == "POST":
            return await save(request, agent, None)
        return JSONResponse([as_json(a) for a in store.list(agent)])

    async def me(request: Request) -> Response:
        info = await run_in_threadpool(store.agent_info, require_agent(store, request))
        if info is None:  # removed since the key was checked
            raise ApiError(401, "unauthorized", "missing or unknown agent key")
        return JSONResponse(user_json(info, store.settings()["user_quota_bytes"]))

    async def new_key(request: Request) -> Response:
        agent = require_agent(store, request)
        key = await run_in_threadpool(store.new_key, agent)
        return JSONResponse({"name": agent, "key": key}, headers={"Cache-Control": "no-store"})

    async def item(request: Request) -> Response:
        agent = require_agent(store, request)
        artifact_id = request.path_params["id"]
        if request.method == "DELETE":
            if await run_in_threadpool(store.delete, artifact_id, owner=agent):
                return Response(status_code=204)
        elif store.get(artifact_id, owner=agent) is not None:
            return await save(request, agent, artifact_id)
        return error(404, "not_found", "no such artifact")

    def live(request: Request) -> Artifact | None:
        a = store.get(request.path_params["id"])
        # The file may be gone already if the purge ran since the lookup.
        return a if a and store.path(a.id).exists() else None

    async def view(request: Request) -> Response:
        a = live(request)
        if a is None:
            return _not_found()
        kind = kinds.get(a.kind)
        url = f"{config.public_url}/a/{a.id}"
        raw = f"/a/{a.id}/raw"
        if kind.sandboxed:
            content = f'<iframe sandbox="{SANDBOX}" src="{raw}" title="content"></iframe>'
        elif kind.name == "pdf":
            # Browsers refuse to run their PDF viewer in a sandboxed frame.
            content = f'<iframe src="{raw}" title="content"></iframe>'
        elif kind.viewable:
            # A click opens it full size in the browser's image viewer.
            content = (
                f'<div class="center"><a href="{raw}" target="_blank" rel="noopener">'
                f'<img src="{raw}" alt="{html.escape(a.name)}"></a></div>'
            )
        else:
            content = '<div class="center">Archive: download it to see the contents.</div>'
        name = html.escape(a.name)
        body = (
            f'<header><div class="name"><h1 title="{name}">{name}</h1>'
            f'<div class="meta">{_size(a.size)} · expires {iso(a.expires_at, human=True)}'
            "</div></div>"
            f'<div class="actions"><a class="btn" href="/a/{a.id}/download" '
            f'title="Download" aria-label="Download">{_DOWNLOAD_ICON}</a>'
            f'<input id="url" readonly value="{html.escape(url)}" aria-hidden="true">'
            f'<button id="copy" type="button" title="Copy link" aria-label="Copy link">'
            f"{_LINK_ICON}{_CHECK_ICON}</button></div></header>"
            f"<main>{content}</main><script>{_COPY_JS}</script>"
        )
        return HTMLResponse(
            _PAGE.format(title=html.escape(a.name), body=body),
            headers={
                "Content-Security-Policy": VIEWER_CSP,
                "Cache-Control": "no-store",
                # Cuts window.opener for pages a sandboxed artifact opens, so
                # they cannot navigate this tab away.
                "Cross-Origin-Opener-Policy": "same-origin",
            },
        )

    async def raw(request: Request) -> Response:
        a = live(request)
        if a is None:
            return _not_found()
        kind = kinds.get(a.kind)
        if not kind.viewable:
            return _download(store.path(a.id), a.name)
        if kind.sandboxed and request.headers.get("sec-fetch-dest") == "document":
            # Opened on its own, a page would look like a page of this site;
            # show it with the viewer's header instead. Images and PDFs open
            # as they are, in the browser's own viewer.
            return RedirectResponse(f"/a/{a.id}", status_code=303)
        csp = SANDBOX_CSP if kind.sandboxed else RAW_CSP
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": csp}
        if kind.name in ("md", "txt"):
            build = _markdown_page if kind.name == "md" else _text_page
            try:
                page = await run_in_threadpool(build, store.path(a.id), a.name)
            except FileNotFoundError:  # purged since the lookup
                return _not_found()
            return HTMLResponse(page, headers=headers)
        return FileResponse(store.path(a.id), media_type=kind.mime, headers=headers)

    async def download(request: Request) -> Response:
        a = live(request)
        return _not_found() if a is None else _download(store.path(a.id), a.name)

    async def join(request: Request) -> Response:
        token = request.path_params["token"]
        if request.method != "POST":
            # Only a press of the button uses the invite: chat apps and link
            # checkers open links themselves (GET or HEAD) to build a preview.
            invite = store.invite_for_token(token)
            if invite is None:
                return _message(404, _INVITE_GONE, csp=JOIN_CSP)
            body = (
                f'<main class="join"><h1>An invite for {html.escape(invite.name)}</h1>'
                "<p>It gives you a key for agent-artifacts: your agents upload files "
                "there and get links to share. The key is shown once, on the next "
                "page.</p>"
                '<form method="post"><button type="submit">Get my key</button></form>'
                f'<p class="meta">This invite works once, until '
                f"{iso(invite.expires_at, human=True)}.</p></main>"
            )
            return _join_page(200, f"Invite for {invite.name}", body)
        try:
            claimed = await run_in_threadpool(store.claim_invite, token)
        except NameTaken:
            return _message(409, "This name is already taken. Ask for a new invite.", csp=JOIN_CSP)
        if claimed is None:
            return _message(404, _INVITE_GONE, csp=JOIN_CSP)
        name, key = claimed
        # One text to paste into any agent: what to do, then the two settings.
        setup = (
            f"Set up agent-artifacts for me: follow {SETUP_URL}\n"
            f"AGENT_ARTIFACTS_URL={config.public_url}\nAGENT_ARTIFACTS_KEY={key}"
        )
        body = (
            f'<main class="join"><h1>Your key, {html.escape(name)}</h1>'
            "<p>Copy it now: this page is not shown again.</p>"
            f'<textarea id="setup" readonly aria-label="setup">{html.escape(setup)}</textarea>'
            '<p><button id="copy" type="button">Copy</button></p>'
            "<p>Paste the text into your agent: Claude Code, Codex or any other that "
            "runs shell commands. It installs the skill and saves the key. To do it by "
            f'hand, see the <a href="{USER_GUIDE_URL}" target="_blank" rel="noopener">'
            "user guide</a>.</p>"
            "<p>Keep the key secret. If it leaks, ask your agent for a new one.</p>"
            f"<script>{_SETUP_JS}</script></main>"
        )
        return _join_page(200, "Your key", body)

    async def health(request: Request) -> Response:
        return Response("ok", media_type="text/plain")

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        task = asyncio.create_task(_purge_forever(store))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/api/v1/me", me),
            Route("/api/v1/me/key", new_key, methods=["POST"]),
            Route("/api/v1/artifacts", collection, methods=["GET", "POST"]),
            Route("/api/v1/artifacts/{id:artifact_id}", item, methods=["PUT", "DELETE"]),
            Route("/a/{id:artifact_id}", view),
            Route("/a/{id:artifact_id}/raw", raw),
            Route("/a/{id:artifact_id}/download", download),
            Route("/join/{token:invite_token}", join, methods=["GET", "POST"]),
            *admin_routes(store, config.public_url),
        ],
        middleware=[Middleware(_CommonHeaders)],
        exception_handlers={ApiError: on_api_error},
        lifespan=lifespan,
    )

    return app


class _CommonHeaders:
    """Headers every response carries, set unless the route chose its own."""

    HEADERS = (
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        ("X-Robots-Tag", "noindex"),
    )

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        async def send_with_headers(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.HEADERS:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers if scope["type"] == "http" else send)


async def _purge_forever(store: Store) -> None:
    while True:
        # One failed pass must not stop the cleanup for good.
        try:
            await run_in_threadpool(store.purge_expired)
        except Exception as e:
            # Only the type: a path in the message would carry a link's id.
            logging.getLogger(__name__).warning("purge failed: %s", type(e).__name__)
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


def _download(path: Path, name: str, media_type: str = "application/octet-stream") -> Response:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )


def _not_found() -> HTMLResponse:
    return _message(404, "This link has expired or never existed.")


def _message(status: int, text: str, *, csp: str = VIEWER_CSP) -> HTMLResponse:
    body = f'<main><div class="center">{text}</div></main>'
    return HTMLResponse(
        _PAGE.format(title=text, body=body),
        status_code=status,
        headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
    )


_INVITE_GONE = "This invite was used, has expired or never existed. Ask for a new one."


def _join_page(status: int, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), body=body),
        status_code=status,
        headers={"Content-Security-Policy": JOIN_CSP, "Cache-Control": "no-store"},
    )


_MARKED_JS = (Path(__file__).parent / "static" / "marked.umd.js").read_text(encoding="utf-8")

# Runs in the viewer's browser, inside the sandbox: heading ids for #anchor
# links, and links to other sites in a new tab (the only way out of it).
_MARKDOWN_JS = """
document.body.innerHTML = marked.parse(SOURCE);
const seen = {};
for (const h of document.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
  const slug = h.textContent.toLowerCase().trim()
    .replace(/[^\\p{L}\\p{N}\\- ]/gu, "").replace(/ /g, "-") || "section";
  const n = seen[slug] = (seen[slug] || 0) + 1;
  h.id = n > 1 ? `${slug}-${n - 1}` : slug;
}
for (const a of document.querySelectorAll("a[href]")) {
  if (!a.getAttribute("href").startsWith("#")) { a.target = "_blank"; a.rel = "noopener"; }
}
"""


def _text_page(path: Path, title: str) -> str:
    """Plain text in a page with fixed colours: a bare text/plain document
    follows the browser's dark mode and turns white-on-white in the frame."""
    text = html.escape(path.read_bytes().decode("utf-8", "replace"))
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>"
        "body{margin:0;padding:16px;color:#1d1d1f;background:#fff}"
        "pre{margin:0;font:14px/1.5 ui-monospace,monospace;white-space:pre-wrap;"
        "overflow-wrap:anywhere}"
        f"</style></head><body><pre>{text}</pre></body></html>"
    )


def _markdown_page(path: Path, title: str) -> str:
    """The Markdown source and a renderer; the viewer's browser does the work."""
    # "<" escaped so the text cannot close the <script> it sits in.
    source = json.dumps(path.read_bytes().decode("utf-8", "replace")).replace("<", "\\u003c")
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>"
        "body{max-width:820px;margin:0 auto;padding:24px 16px;"
        "font:16px/1.6 system-ui,sans-serif;color:#1d1d1f;background:#fff}"
        "pre,code{background:#f2f2f4;border-radius:4px}pre{padding:12px;overflow:auto}"
        "code{padding:1px 4px}pre code{padding:0}table{border-collapse:collapse}"
        "th,td{border:1px solid #d2d2d7;padding:4px 8px}img{max-width:100%}"
        "blockquote{margin:0;padding-left:12px;border-left:3px solid #d2d2d7;color:#6e6e73}"
        f"</style></head><body><script>{_MARKED_JS}</script>"
        f"<script>const SOURCE = {source};{_MARKDOWN_JS}</script></body></html>"
    )


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
