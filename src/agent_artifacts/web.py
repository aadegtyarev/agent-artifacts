"""HTTP routes: the agent API under /api/v1, the viewer under /a, invites
under /join. The HTML itself is in pages.py, the admin API in admin.py, comments in
comments.py."""

from __future__ import annotations

import asyncio
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
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import kinds, pages
from .admin import admin_routes
from .api import ApiError, error, iso, on_api_error, require_agent, user_json
from .comments import comment_routes
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
            "comments": a.comments,
            "open_comments": a.open_comments,
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
                    f"your files take {pages.size(e.used)} of {pages.size(e.quota)} and this "
                    f"one needs {pages.size(size)}; delete files you no longer need "
                    "(GET /api/v1/artifacts lists them)",
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
        found = await run_in_threadpool(store.list, agent)  # counts comments too
        return JSONResponse([as_json(a) for a in found])

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
            return pages.not_found()
        return pages.viewer_page(a, f"{config.public_url}/a/{a.id}")

    async def raw(request: Request) -> Response:
        a = live(request)
        if a is None:
            return pages.not_found()
        kind = kinds.get(a.kind)
        if not kind.viewable:
            return _download(store.path(a.id), a.name)
        if kind.sandboxed and request.headers.get("sec-fetch-dest") == "document":
            # Opened on its own, a page would look like a page of this site;
            # show it with the viewer's header instead. Images and PDFs open
            # as they are, in the browser's own viewer.
            return RedirectResponse(f"/a/{a.id}", status_code=303)
        csp = pages.SANDBOX_CSP if kind.sandboxed else pages.RAW_CSP
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": csp}
        # Pages get the pin script: appended to HTML, inside the Markdown and
        # text pages built here.
        if kind.name in ("html", "md", "txt"):
            build = {"html": pages.html_page, "md": pages.markdown_page, "txt": pages.text_page}
            try:
                page = await run_in_threadpool(build[kind.name], store.path(a.id), a.name)
            except FileNotFoundError:  # purged since the lookup
                return pages.not_found()
            return HTMLResponse(page, headers=headers)
        return FileResponse(store.path(a.id), media_type=kind.mime, headers=headers)

    async def download(request: Request) -> Response:
        a = live(request)
        return pages.not_found() if a is None else _download(store.path(a.id), a.name)

    async def join(request: Request) -> Response:
        token = request.path_params["token"]
        if request.method != "POST":
            # Only a press of the button uses the invite: chat apps and link
            # checkers open links themselves (GET or HEAD) to build a preview.
            invite = store.invite_for_token(token)
            if invite is None:
                return pages.message(404, pages.INVITE_GONE, csp=pages.JOIN_CSP)
            return pages.invite_page(invite)
        try:
            claimed = await run_in_threadpool(store.claim_invite, token)
        except NameTaken:
            text = "This name is already taken. Ask for a new invite."
            return pages.message(409, text, csp=pages.JOIN_CSP)
        if claimed is None:
            return pages.message(404, pages.INVITE_GONE, csp=pages.JOIN_CSP)
        name, key = claimed
        return pages.key_page(name, key, config.public_url)

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
            Mount("/static", StaticFiles(directory=pages.STATIC_DIR), name="static"),
            *admin_routes(store, config.public_url),
            *comment_routes(store),
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
