"""HTTP routes: the agent API under /api/v1, the viewer under /a, project
pages under /p, invites under /join. The HTML itself is in pages.py, the
admin API in admin.py, comments in comments.py."""

from __future__ import annotations

import asyncio
import functools
import hmac
import logging
import math
import re
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from urllib.parse import quote, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.convertors import StringConvertor, register_url_convertor
from starlette.datastructures import MutableHeaders, UploadFile
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route, request_response
from starlette.staticfiles import StaticFiles

from . import kinds, pages
from .admin import admin_routes
from .api import ApiError, error, iso, json_body, on_api_error, require_agent, user_json
from .comments import _workspace_author, comment_routes, one_line
from .config import Config, normal_host
from .google_oidc import GoogleOIDC
from .storage import DiskStorage
from .store import (
    ID_RE,
    TOKEN_RE,
    Artifact,
    NameTaken,
    NoSuchProject,
    Project,
    QuotaExceeded,
    SectionWithoutProject,
    StorageFull,
    Store,
    TooMany,
)
from .workspace_auth import WorkspaceAuth, WorkspaceAuthStore, same_origin

PURGE_INTERVAL_SECONDS = 600


class _ArtifactId(StringConvertor):
    regex = ID_RE


register_url_convertor("artifact_id", _ArtifactId())


class _InviteToken(StringConvertor):
    regex = TOKEN_RE


register_url_convertor("invite_token", _InviteToken())
# Multipart framing around the file; the file itself is checked exactly later.
FORM_OVERHEAD_BYTES = 64 * 1024
# Upload fields besides the file: name and title, then project and section.
MAX_FORM_FIELDS = 4
MAX_TITLE = 200  # a file's or a project's
MAX_SECTION = 64


def create_app(config: Config, store: Store | None = None) -> Starlette:
    store = store or Store(
        config.data_dir,
        max_total_bytes=config.max_total_bytes,
        user_quota_bytes=config.user_quota_bytes,
        ttl_seconds=config.ttl_seconds,
        storage=DiskStorage(config.data_dir),
    )

    def as_json(a: Artifact) -> dict:
        return {
            "id": a.id,
            "url": f"{config.public_url}/a/{a.id}",
            "name": a.name,
            "title": a.title,
            "project": a.project,
            "section": a.section,
            "type": a.kind,
            "size": a.size,
            "created_at": iso(a.created_at),
            "updated_at": iso(a.updated_at),
            "expires_at": iso(a.expires_at),
            "comments": a.comments,
            "open_comments": a.open_comments,
        }

    def project_json(p: Project) -> dict:
        return {
            "id": p.id,
            "url": f"{config.public_url}/p/{p.id}",
            "title": p.title,
            "created_at": iso(p.created_at),
            "files": p.files,
            "used_bytes": p.used_bytes,
        }

    async def save(request: Request, agent: str, artifact_id: str | None) -> Response:
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return error(411, "bad_request", "Content-Length is required")
        if int(length) > config.max_file_bytes + FORM_OVERHEAD_BYTES:
            return error(413, "too_large", f"limit is {config.max_file_bytes} bytes")
        try:
            form = await request.form(max_files=1, max_fields=MAX_FORM_FIELDS, max_part_size=4096)
        except Exception:  # starlette raises several types for a malformed body
            return error(400, "bad_request", "expected multipart/form-data")
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return error(400, "bad_request", "multipart field 'file' is required")
            size = upload.size or 0
            if size > config.max_file_bytes:
                return error(413, "too_large", f"limit is {config.max_file_bytes} bytes")
            title_field = form.get("title")
            title = None if title_field is None else _title(title_field)
            project_field, section_field = form.get("project"), form.get("section")
            project = None if project_field is None else _project_id(project_field)
            section = None if section_field is None else _title(section_field, "section")
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
                artifact = await _stored(
                    store.put,
                    upload.file,
                    owner=agent,
                    name=name,
                    kind=kind.name,
                    size=size,
                    artifact_id=artifact_id,
                    title=title,
                    project=project,
                    section=section,
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
        settings = store.settings()
        body = user_json(info, settings["user_quota_bytes"])
        # What uploads are held to now, from the values the checks use.
        body["limits"] = {
            "ttl_seconds": settings["ttl_seconds"],
            "max_file_bytes": config.max_file_bytes,
            "max_text_bytes": kinds.MAX_TEXT_BYTES,
            "types": kinds.extensions(),
        }
        return JSONResponse(body)

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
        elif request.method == "PATCH":
            data = await json_body(request, {"title", "project", "section"})
            changes = {}
            if "title" in data:
                changes["title"] = _title(data["title"])
            if "project" in data:
                v = data["project"]
                changes["project"] = None if v is None else _project_id(v)
            if "section" in data:
                v = data["section"]
                changes["section"] = None if v is None else _title(v, "section")
            edited = await _stored(store.edit, artifact_id, owner=agent, changes=changes)
            if edited is not None:
                return JSONResponse(as_json(edited))
        elif store.get(artifact_id, owner=agent) is not None:
            return await save(request, agent, artifact_id)
        return error(404, "not_found", "no such artifact")

    async def projects(request: Request) -> Response:
        agent = require_agent(store, request)
        if request.method == "POST":
            data = await json_body(request, {"title"}, {"title"})
            made = await _stored(store.add_project, agent, _title(data["title"]))
            return JSONResponse(project_json(made), status_code=201)
        found = await run_in_threadpool(store.projects, agent)
        return JSONResponse([project_json(p) for p in found])

    async def project(request: Request) -> Response:
        agent = require_agent(store, request)
        project_id = request.path_params["id"]
        if request.method == "DELETE":
            if await run_in_threadpool(store.delete_project, project_id, owner=agent):
                return Response(status_code=204)
        elif request.method == "PATCH":
            data = await json_body(request, {"title"}, {"title"})
            title = _title(data["title"])
            renamed = await run_in_threadpool(
                store.rename_project, project_id, owner=agent, title=title
            )
            if renamed is not None:
                return JSONResponse(project_json(renamed))
        else:
            found = await run_in_threadpool(store.project, project_id, owner=agent)
            if found is not None:
                p, files = found
                return JSONResponse(project_json(p) | {"artifacts": [as_json(a) for a in files]})
        return error(404, "not_found", "no such project")

    async def project_news(request: Request) -> Response:
        # One request per poll of `wait` on a whole project.
        agent = require_agent(store, request)
        since = request.query_params.get("since")
        if since is not None:
            try:
                since = float(since)
            except ValueError:
                since = math.nan
            if not math.isfinite(since):
                raise ApiError(400, "bad_request", "since must be a number of seconds")
        found = await run_in_threadpool(
            functools.partial(
                store.agent_summary, request.path_params["id"], owner=agent, since=since
            )
        )
        if found is None:
            return error(404, "not_found", "no such project")
        now, files = found
        return JSONResponse(
            {
                "now": now,
                "files": [
                    {"id": i, "title": t, "waiting": w, "accepted": a} for i, t, w, a in files
                ],
            }
        )

    def on_disk(a: Artifact) -> bool:
        if store.storage.available_for_serving(a.id):
            return True
        # Purged since the lookup, or lost (a database restored without its
        # files); the next purge removes the row. Not the id: that is the link.
        logging.getLogger(__name__).warning("file of %s's %r is missing", a.owner, a.name)
        return False

    def live(request: Request) -> Artifact | None:
        a = store.get(request.path_params["id"])
        return a if a and on_disk(a) else None

    async def view(request: Request) -> Response:
        a = live(request)
        if a is None:
            return pages.not_found()
        kind = kinds.get(a.kind)
        # "Open separately": the file at the raw origin, if there is one.
        separate = f"{config.raw_url}/a/{a.id}/raw" if config.raw_url and kind.viewable else None
        # A file in a project lists the project's files in its header: as
        # open as the project, whose page anyone with the link may see.
        project = None
        if a.project:
            project = await run_in_threadpool(store.public_project, a.project)
        return pages.viewer_page(
            a, f"{config.public_url}/a/{a.id}", separate, project, store.clock(),
            workspace_author=_workspace_author(request),
            workspace_marker=getattr(request.state, "workspace_marker", None),
        )

    async def project_view(request: Request) -> Response:
        # Public by its link, like a file: no owner asked.
        found = await run_in_threadpool(store.public_project, request.path_params["id"])
        if found is None:
            return pages.not_found()
        p, files = found
        return pages.project_page(p, files, f"{config.public_url}/p/{p.id}", store.clock(),
                                  workspace_author=_workspace_author(request))

    async def raw(request: Request) -> Response:
        a = live(request)
        if a is None:
            return pages.not_found()
        kind = kinds.get(a.kind)
        if not kind.viewable:
            return _download(store.storage, a.id, a.name)
        if kind.sandboxed and request.headers.get("sec-fetch-dest") == "document":
            # Opened on its own, a page would look like a page of this site;
            # show it with the viewer's header instead. Images and PDFs open
            # as they are, in the browser's own viewer.
            return RedirectResponse(f"/a/{a.id}", status_code=303)
        return await content(a, kind)

    async def raw_origin(request: Request) -> Response:
        """Every request to the raw origin: /a/{id}/raw of a file that can be
        shown, as it is, and 404 for anything else."""
        match = _RAW_PATH.fullmatch(request.scope["path"])
        if request.method not in ("GET", "HEAD") or match is None:
            return _raw_origin_not_found()
        a = store.get(match[1])
        if a is None or not on_disk(a):
            return _raw_origin_not_found()
        kind = kinds.get(a.kind)
        if not kind.viewable:
            return _raw_origin_not_found()
        response = await content(a, kind)
        return _raw_origin_not_found() if response.status_code == 404 else response

    async def content(a: Artifact, kind: kinds.Kind) -> Response:
        csp = pages.SANDBOX_CSP if kind.sandboxed else pages.RAW_CSP
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": csp}
        # Pages get the pin script: appended to HTML, inside the Markdown and
        # text pages built here. Sent as they are read, so a big file is never
        # whole in memory; the file is open already, a purge cannot cut it.
        if kind.name in ("html", "md", "txt"):
            try:
                body, length = await run_in_threadpool(_prepare_page, store.storage, a)
            except FileNotFoundError:  # purged since the lookup
                return pages.not_found()
            if length is not None:
                headers["Content-Length"] = str(length)
            return StreamingResponse(body, headers=headers, media_type="text/html")
        return _disk_response(store.storage, a.id, media_type=kind.mime, headers=headers)

    async def download(request: Request) -> Response:
        a = live(request)
        return pages.not_found() if a is None else _download(store.storage, a.id, a.name)

    async def join(request: Request) -> Response:
        token = request.path_params["token"]
        if request.method != "POST":
            # Only a press of the button uses the invite: chat apps and link
            # checkers open links themselves (GET or HEAD) to build a preview.
            invite = store.invite_for_token(token)
            if invite is None:
                return pages.message(404, pages.INVITE_GONE, csp=pages.JOIN_CSP)
            response = pages.invite_page(invite)
            if request.method == "GET" and getattr(request.state, "workspace_identity", None):
                response.headers["Referrer-Policy"] = "strict-origin"
            return response
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

    middleware = [Middleware(_CommonHeaders)]
    if config.workspace is not None:
        auth_store = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", config.workspace)
        oidc = GoogleOIDC(config.workspace, config.public_url, config.raw_url)
        main_auth = WorkspaceAuth(auth_store, oidc, origin=config.public_url)
        raw_auth = (WorkspaceAuth(auth_store, oidc, origin=config.raw_url, raw=True)
                    if config.raw_url else None)
        middleware.append(Middleware(_WorkspaceAccess, main=main_auth, raw=raw_auth))
    middleware.append(
        Middleware(_RawOrigin, host=urlsplit(config.raw_url).hostname, app_there=raw_origin)
    )

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/api/v1/me", me),
            Route("/api/v1/me/key", new_key, methods=["POST"]),
            Route("/api/v1/artifacts", collection, methods=["GET", "POST"]),
            Route("/api/v1/artifacts/{id:artifact_id}", item, methods=["PUT", "PATCH", "DELETE"]),
            Route("/api/v1/projects", projects, methods=["GET", "POST"]),
            # A project id is made like an artifact id.
            Route("/api/v1/projects/{id:artifact_id}", project, methods=["GET", "PATCH", "DELETE"]),
            Route("/api/v1/projects/{id:artifact_id}/summary", project_news),
            Route("/a/{id:artifact_id}", view),
            Route("/a/{id:artifact_id}/raw", raw),
            Route("/a/{id:artifact_id}/download", download),
            Route("/p/{id:artifact_id}", project_view),
            Route("/join/{token:invite_token}", join, methods=["GET", "POST"]),
            Mount("/static", StaticFiles(directory=pages.STATIC_DIR), name="static"),
            *admin_routes(store, config.public_url),
            *comment_routes(store),
        ],
        middleware=middleware,
        exception_handlers={ApiError: on_api_error},
        lifespan=lifespan,
    )

    return app


# Marks that turn the direction of text, refused in a title on top of the
# controls one_line refuses; and characters that show nothing.
_TITLE_BIDI = re.compile("[\u200e\u200f\u061c]")
_INVISIBLE = re.compile("[\u200b-\u200d\u2060\ufeff]")


def _title(v: object, field: str = "title") -> str:
    """What pages show for a file or a project, or a file's section: one
    line of 1 to MAX_TITLE (a section MAX_SECTION) characters, composed (NFC)
    like file names, so one title is stored one way."""
    if isinstance(v, str):
        v = unicodedata.normalize("NFC", v)
        if _TITLE_BIDI.search(v):
            raise ApiError(400, "bad_request", f"{field} must be text without control characters")
    title = one_line(v, field, MAX_SECTION if field == "section" else MAX_TITLE)
    if not _INVISIBLE.sub("", title).strip():
        raise ApiError(400, "bad_request", f"{field} is empty")
    return title


def _project_id(v: object) -> str:
    """A project field: an id, or 404 like a project of another agent's."""
    if not isinstance(v, str):
        raise ApiError(400, "bad_request", "project must be a project id")
    if not re.fullmatch(ID_RE, v):
        raise ApiError(404, "not_found", "no such project")
    return v


async def _stored(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
    """``func`` in the thread pool, its refusals about projects as API errors."""
    try:
        return await run_in_threadpool(func, *args, **kwargs)
    except NoSuchProject:
        raise ApiError(404, "not_found", "no such project") from None
    except SectionWithoutProject:
        raise ApiError(400, "bad_request", "a section needs a project") from None
    except TooMany as e:
        raise ApiError(409, "too_many", str(e)) from None


_RAW_PATH = re.compile(rf"/a/({ID_RE})/raw")


def _raw_origin_not_found() -> Response:
    return Response(
        "Not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "no-store"}
    )


_BROWSER_NAV = re.compile(rf"(?:/a/{ID_RE}|/p/{ID_RE}|/join/{TOKEN_RE})")
_REVIEW_PATH = re.compile(rf"/a/{ID_RE}/(?:comments|drafts|review)(?:/.*)?")


def _authority(value: str) -> tuple[str, int] | None:
    """HTTPS authority, treating an omitted port as 443 and rejecting malformed hosts."""
    if not value or not value.isascii() or any(c in value for c in "\\/?#@ \t\r\n"):
        return None
    try:
        parts = urlsplit("//" + value)
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname or parts.netloc.endswith(":") or (port is not None and port == 0):
        return None
    return normal_host(parts.hostname), port if port is not None else 443


class _WorkspaceAccess:
    """Authenticate browser paths and isolate both configured authorities."""

    def __init__(self, app, main: WorkspaceAuth, raw: WorkspaceAuth | None) -> None:  # noqa: ANN001
        self.app = app
        self.main = main
        self.raw = raw
        self.main_authority = _authority(urlsplit(main.origin).netloc)
        self.raw_authority = _authority(urlsplit(raw.origin).netloc) if raw else None
        if (self.main_authority is None or (raw and self.raw_authority is None)
                or (raw and self.main_authority == self.raw_authority)):
            raise ValueError("invalid Workspace authorities")

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hosts = [v.decode("latin-1") for k, v in scope["headers"] if k == b"host"]
        host = _authority(hosts[0]) if len(hosts) == 1 else None
        if host == self.main_authority:
            auth = self.main
        elif self.raw and host == self.raw_authority:
            auth = self.raw
        else:
            await Response(status_code=400, headers={"Cache-Control": "no-store"})(
                scope, receive, send
            )
            return

        path, method = scope["path"], scope["method"]
        request = Request(scope, receive)
        auth_routes = {
            "/auth/login": ("GET", auth.login if auth.raw else auth.sign_in),
            "/auth/callback": ("GET", auth.callback),
            "/auth/logout": ("POST", auth.logout),
        }
        if not auth.raw:
            auth_routes.update({
                "/": ("GET", auth.sign_in),
                "/auth/start": ("GET", auth.login),
                "/auth/signed-out": ("GET", auth.sign_in),
            })
        if path in auth_routes:
            expected, handler = auth_routes[path]
            response = (await handler(request) if method == expected else
                        Response(status_code=405, headers={"Allow": expected,
                                                          "Cache-Control": "no-store"}))
            await response(scope, receive, send)
            return

        if auth.raw and (not _RAW_PATH.fullmatch(path) or method not in ("GET", "HEAD")):
            await _raw_origin_not_found()(scope, receive, send)
            return

        protected = auth.raw or any(path == prefix or path.startswith(prefix + "/")
                                    for prefix in ("/a", "/p", "/join"))
        if not protected:
            await self.app(scope, receive, send)
            return

        identity = await auth.identity(request)
        if identity is None:
            navigation = (method == "GET" and
                          ((_BROWSER_NAV.fullmatch(path) and not auth.raw) or
                           (auth.raw and _RAW_PATH.fullmatch(path) and
                            request.headers.get("sec-fetch-dest") == "document")))
            if navigation:
                response = RedirectResponse(
                    "/auth/login?return=" + quote(path, safe="/"), status_code=303,
                    headers={"Cache-Control": "no-store"},
                )
            else:
                response = Response(status_code=401, headers={"Cache-Control": "no-store"})
            await response(scope, receive, send)
            return

        if method not in ("GET", "HEAD", "OPTIONS"):
            origins = request.headers.getlist("origin")
            if len(origins) != 1 or not same_origin(origins[0], auth.origin):
                await Response(status_code=403, headers={"Cache-Control": "no-store"})(
                    scope, receive, send
                )
                return
        marker = auth.page_marker(request)
        if not auth.raw and _REVIEW_PATH.fullmatch(path):
            markers = request.headers.getlist("x-workspace-session")
            if len(markers) != 1 or not hmac.compare_digest(
                markers[0].encode("utf-8"), marker.encode("ascii")
            ):
                response = JSONResponse(
                    {"error": "workspace_session_changed",
                     "message": "Sign-in changed. Copy unsent text and reload this page."},
                    status_code=409, headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
        state = scope.setdefault("state", {})
        state["workspace_identity"] = identity
        state["workspace_marker"] = marker

        async def no_store(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, no_store)


class _RawOrigin:
    """Sends requests whose Host is the raw origin's to its own handler, so
    none of the service's other paths answer there."""

    def __init__(self, app, host: str | None, app_there) -> None:  # noqa: ANN001
        self.app = app
        self.host = normal_host(host) if host else None
        self.there = request_response(app_there)

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if self.host and scope["type"] == "http" and _host(scope) == self.host:
            await self.there(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def _host(scope) -> str:  # noqa: ANN001
    """The Host header's name, without the port, as normal_host() puts it."""
    for name, value in scope["headers"]:
        if name == b"host":
            host = value.decode("latin-1")
            host = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0]
            return normal_host(host)
    return ""


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


def _prepare_page(storage: DiskStorage, artifact: Artifact) -> pages.Page:
    f = storage.open(artifact.id)
    try:
        if artifact.kind == "html":
            return pages.html_page(f, artifact.name, storage.size(f))
        build = {"md": pages.markdown_page, "txt": pages.text_page}
        return build[artifact.kind](f, artifact.name)
    except BaseException:
        f.close()
        raise


def _disk_response(storage: DiskStorage, artifact_id: str, **kwargs) -> Response:  # noqa: ANN003
    return FileResponse(storage.file_path(artifact_id), **kwargs)


def _download(storage: DiskStorage, artifact_id: str, name: str) -> Response:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return _disk_response(
        storage, artifact_id,
        media_type="application/octet-stream",
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )
