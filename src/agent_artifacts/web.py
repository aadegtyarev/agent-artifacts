"""HTTP routes: the agent API under /api/v1, the viewer under /a, project
pages under /p, invites under /join. The HTML itself is in pages.py, the
admin API in admin.py, comments in comments.py."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import html
import json
import logging
import math
import re
import tempfile
import time
import unicodedata
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import quote, urlsplit

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
    StreamingResponse,
)
from starlette.routing import Mount, Route, request_response
from starlette.staticfiles import StaticFiles

from . import kinds, pages
from .account_pages import AccountPages
from .admin import admin_routes
from .api import ApiError, error, iso, json_body, on_api_error, require_agent, user_json
from .comments import _workspace_author, comment_routes, one_line
from .config import Config, normal_host
from .credentials import hash_key
from .drive_destination import DriveDestination, DriveDestinationError, DriveDestinationStore
from .drive_grants import WorkspaceDriveGrants
from .drive_project_rename import DriveProjectRenames, ProjectRename
from .drive_rename import DriveRenames, Rename
from .drive_storage import (
    DriveAccessError,
    DriveCapacityError,
    DriveError,
    DriveReadControl,
    DriveRevision,
    DriveStorage,
    DriveUploadLimitError,
)
from .google_oidc import GoogleOIDC
from .review_report_routes import report_routes
from .review_submissions import pending_closures
from .storage import DiskStorage
from .store import (
    ID_RE,
    TOKEN_RE,
    Artifact,
    DeletionPending,
    DriveArtifactError,
    DriveConflict,
    DriveRetentionBlocked,
    NameTaken,
    NoSuchProject,
    Project,
    QuotaExceeded,
    SectionWithoutProject,
    StorageFull,
    Store,
    TooMany,
)
from .workspace_auth import ACCOUNT_ENTRY_PATHS, WorkspaceAuth, WorkspaceAuthStore, same_origin
from .workspace_drive import DriveGrantRequired, WorkspaceDrive

PURGE_INTERVAL_SECONDS = 600
_DRIVE_READ_BUDGET_SECONDS = 30.0


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


def create_app(
    config: Config, store: Store | None = None, *, drive: DriveStorage | None = None,
) -> Starlette:
    if drive is not None and (config.workspace is None or config.drive is not None):
        raise ValueError("injected Drive is only for Workspace without configured Drive routing")
    store = store or Store(
        config.data_dir,
        max_total_bytes=config.max_total_bytes,
        user_quota_bytes=config.user_quota_bytes,
        ttl_seconds=config.ttl_seconds,
        storage=DiskStorage(config.data_dir),
    )
    if config.workspace is None and store.has_drive_artifacts():
        raise ValueError("Drive-backed artifacts require Workspace authentication")
    destination = (DriveDestination.from_config(config.drive, config.workspace, config.public_url)
                   if config.drive is not None and config.workspace is not None else None)
    destination_store = DriveDestinationStore(store._conn)
    destination_store.check(destination)
    grants = None
    delegated = None
    if config.workspace is not None and destination is not None:
        grants = WorkspaceDriveGrants(config.data_dir, config.workspace)
        delegated = WorkspaceDrive(config.workspace, grants, folder_id=destination.folder_id,
                                   folder_kind=destination.folder_kind, max_bytes=config.max_file_bytes,
                                   check_destination=functools.partial(destination_store.check, destination))

    def as_json(a: Artifact) -> dict:
        body = {
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
        if a.version_id is not None:
            body["version_url"] = f"{config.public_url}/a/{a.id}/versions/{a.version_id}"
        return body

    def project_json(p: Project) -> dict:
        return {
            "id": p.id,
            "url": f"{config.public_url}/p/{p.id}",
            "title": p.title,
            "created_at": iso(p.created_at),
            "files": p.files,
            "used_bytes": p.used_bytes,
        }

    def linked_subject(owner: str, credential_hash: str) -> str:
        subject = store.accounts.google_subject_for_agent(owner, credential_hash)
        if subject is None:
            raise ApiError(403, "drive_account_required", "Link this agent to your Workspace account.",
                           account_url=config.public_url + "/account")
        return subject

    def put_delegated(source, *, owner: str, credential_hash: str, **fields):
        assert delegated is not None and destination is not None
        with delegated.open(linked_subject(owner, credential_hash)) as storage:
            if not destination_store.check(destination):
                storage.validate_folder()
                destination_store.pin(destination)
            return store.put_drive(storage, source, owner=owner, **fields)

    renames = DriveRenames(store)
    project_renames = DriveProjectRenames(store)

    def rename_delegated(artifact_id: str, owner: str, credential_hash: str, title: str) -> Artifact | None:
        assert delegated is not None
        subject = linked_subject(owner, credential_hash)
        operation = renames.prepare(artifact_id, owner, title)
        if not isinstance(operation, Rename):
            return operation
        try:
            with delegated.open(subject) as drive_storage:
                return renames.execute(drive_storage, operation, owner)
        except (DriveConflict, DriveError):
            renames.discard_unsent(operation)
            raise

    def rename_project_delegated(project_id: str, owner: str, credential_hash: str, title: str) -> Project | None:
        assert delegated is not None
        operation = project_renames.prepare(project_id, owner, title)
        if not isinstance(operation, ProjectRename):
            return operation
        try:
            if config.drive_read_only:
                raise ApiError(503, "drive_read_only", "Google Drive writes are paused.")
            with delegated.open(linked_subject(owner, credential_hash)) as storage:
                return project_renames.execute(storage, operation, owner)
        except (ApiError, DriveConflict, DriveError):
            project_renames.discard_unsent(operation)
            raise

    async def save(
        request: Request, agent: str, artifact_id: str | None, *, use_drive: bool,
    ) -> Response:
        if use_drive and config.drive_read_only:
            return error(503, "drive_read_only", "Google Drive writes are paused.")
        if use_drive and drive is None and delegated is None:
            return error(503, "storage_unavailable", "storage is temporarily unavailable")
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
                put = store.put
                if use_drive:
                    if delegated is None:
                        put = functools.partial(store.put_drive, drive)
                    else:
                        supplied_key = request.headers["authorization"].partition(" ")[2].strip()
                        put = functools.partial(put_delegated, credential_hash=hash_key(supplied_key))
                artifact = await _stored(
                    put,
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
            except DriveUploadLimitError:
                return error(413, "too_large", "file exceeds storage transfer limit")
            except DriveCapacityError:
                return error(507, "storage_unavailable", "storage capacity is unavailable")
            except DriveGrantRequired:
                return error(403, "drive_authorization_required", "Authorize Google Drive for the linked account.",
                             authorization_url=config.public_url + "/auth/drive-access?return=/account")
            except DriveAccessError:
                if delegated is not None:
                    return error(403, "drive_access_denied",
                                 "Google Drive denied this operation. Ask your administrator to check folder access.")
                return error(503, "storage_unavailable", "storage is temporarily unavailable")
            except DriveError:
                return error(503, "storage_unavailable", "storage is temporarily unavailable")
            except (DriveConflict, DriveArtifactError):
                return error(409, "storage_conflict", "storage write cannot be published")
        finally:
            await form.close()
        if artifact is None:
            return error(404, "not_found", "no such artifact")
        return JSONResponse(as_json(artifact), status_code=200 if artifact_id else 201)

    async def collection(request: Request) -> Response:
        agent = require_agent(store, request)
        if request.method == "POST":
            if config.workspace is not None and drive is None and delegated is None:
                try:
                    await run_in_threadpool(destination_store.check, None)
                except DriveDestinationError:
                    return error(503, "storage_unavailable", "storage configuration is out of date")
            return await save(request, agent, None, use_drive=drive is not None or delegated is not None)
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
            try:
                if await run_in_threadpool(store.delete, artifact_id, owner=agent):
                    return Response(status_code=204)
            except DriveRetentionBlocked:
                return error(409, "retention_unresolved", "Drive retention policy is unresolved")
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
            try:
                snapshot = (await run_in_threadpool(store.get_drive, artifact_id, owner=agent)
                            if delegated is not None and "title" in changes else None)
                if snapshot is not None:
                    if set(changes) != {"title"}:
                        return error(409, "storage_conflict", "Send Drive title changes separately from placement changes.")
                    if config.drive_read_only:
                        return error(503, "drive_read_only", "Google Drive writes are paused.")
                    supplied = request.headers["authorization"].partition(" ")[2].strip()
                    edited = await run_in_threadpool(rename_delegated, artifact_id, agent,
                                                     hash_key(supplied), changes["title"])
                else:
                    edited = await _stored(store.edit, artifact_id, owner=agent, changes=changes)
            except DriveGrantRequired:
                return error(403, "drive_authorization_required", "Authorize Google Drive for the linked account.",
                             authorization_url=config.public_url + "/auth/drive-access?return=/account")
            except DriveAccessError:
                return error(403, "drive_access_denied", "Ask your administrator to check Google Drive access.")
            except DriveError:
                return error(503, "storage_unavailable", "Drive rename unavailable. Retry only the same title.")
            except DriveConflict:
                return error(409, "storage_conflict", "Drive rename or placement is unresolved. Do not repeat it with different input.")
            if edited is not None:
                return JSONResponse(as_json(edited))
        else:
            # Replacing never moves an existing file to a different provider.
            snapshot = await run_in_threadpool(store.get_with_drive, artifact_id, owner=agent)
            if snapshot is not None:
                return await save(request, agent, artifact_id, use_drive=snapshot[1] is not None)
        return error(404, "not_found", "no such artifact")

    async def projects(request: Request) -> Response:
        agent = require_agent(store, request)
        if request.method == "POST":
            data = await json_body(request, {"title"}, {"title"})
            made = await _stored(store.add_project, agent, _title(data["title"]))
            return JSONResponse(project_json(made), status_code=201)
        found = await run_in_threadpool(store.projects, agent)
        return JSONResponse([project_json(p) for p in found])

    def project_closures(project_id: str, owner: str) -> dict[str, str]:
        if not config.review_docs or delegated is None:
            return {}
        with store._conn() as db:
            return pending_closures(db, owner, project_id=project_id)

    async def project(request: Request) -> Response:
        agent = require_agent(store, request)
        project_id = request.path_params["id"]
        if request.method == "DELETE":
            try:
                if await run_in_threadpool(store.delete_project, project_id, owner=agent):
                    return Response(status_code=204)
            except DeletionPending:
                return error(409, "deletion_pending", "a file is still within its Undo window")
            except DriveRetentionBlocked:
                return error(409, "retention_unresolved", "Drive retention policy is unresolved")
        elif request.method == "PATCH":
            data = await json_body(request, {"title"}, {"title"})
            title = _title(data["title"])
            try:
                if delegated is None:
                    renamed = await run_in_threadpool(store.rename_project, project_id, owner=agent, title=title)
                else:
                    supplied = request.headers["authorization"].partition(" ")[2].strip()
                    renamed = await run_in_threadpool(rename_project_delegated, project_id, agent, hash_key(supplied), title)
            except DriveGrantRequired:
                return error(403, "drive_authorization_required", "Authorize Google Drive for the linked account.",
                             authorization_url=config.public_url + "/auth/drive-access?return=/account")
            except DriveAccessError:
                return error(403, "drive_access_denied", "Ask your administrator to check Google Drive access.")
            except DriveError:
                return error(503, "storage_unavailable", "Drive project rename unavailable. Retry only the same title.")
            except DriveConflict:
                return error(409, "storage_conflict", "Drive project rename is unavailable or unresolved.")
            if renamed is not None:
                return JSONResponse(project_json(renamed))
        else:
            found = await run_in_threadpool(store.project, project_id, owner=agent)
            if found is not None:
                p, files = found
                closures = await run_in_threadpool(project_closures, project_id, agent)
                artifacts = [as_json(a) | ({"review_closure": closures[a.id]} if a.id in closures else {})
                             for a in files]
                return JSONResponse(project_json(p) | {"artifacts": artifacts}, headers={"Cache-Control": "no-store"})
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
        closures = await run_in_threadpool(project_closures, request.path_params["id"], agent)
        return JSONResponse(
            {
                "now": now,
                "files": [
                    {"id": i, "title": t, "waiting": w, "accepted": a}
                    | ({"review_closure": closures[i]} if i in closures else {}) for i, t, w, a in files
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    def on_disk(a: Artifact) -> bool:
        if store.storage.available_for_serving(a.id):
            return True
        # Purged since the lookup, or lost (a database restored without its
        # files); the next purge removes the row. Not the id: that is the link.
        logging.getLogger(__name__).warning("file of %s's %r is missing", a.owner, a.name)
        return False

    def live(artifact_id: str) -> tuple[Artifact, DriveRevision | None] | None:
        snapshot = store.get_with_drive(artifact_id)
        if snapshot is None:
            return None
        artifact, revision = snapshot
        return snapshot if revision is not None or on_disk(artifact) else None

    async def view(request: Request) -> Response:
        snapshot = live(request.path_params["id"])
        if snapshot is None:
            return pages.not_found()
        a, revision = snapshot
        version_id = None
        if revision is not None:
            if delegated is not None:
                version_id = await run_in_threadpool(store.drive_version_id, a, revision)
                if version_id is None:
                    return _storage_unavailable()
            try:
                temp = await materialize(request, a, revision)
            except _ClientDisconnected:
                return _DisconnectedResponse()
            except DriveError as exc:
                return drive_failure(request, exc)
            temp.cleanup()
        kind = kinds.get(a.kind)
        separate = (f"{config.raw_url}/a/{a.id}/raw" if config.raw_url and kind.viewable
                    and (revision is None or delegated is None) else None)
        project = None
        if a.project:
            project = await run_in_threadpool(store.public_project, a.project)
        review_state = None
        identity = getattr(request.state, "workspace_identity", None)
        if version_id is not None and config.review_docs and identity is not None:
            review_state = await run_in_threadpool(store.browser_review_state, a.id, identity.subject,
                                                   read_only=config.drive_read_only)
        return pages.viewer_page(
            a, f"{config.public_url}/a/{a.id}", separate, project, store.clock(),
            workspace_author=_workspace_author(request),
            workspace_marker=getattr(request.state, "workspace_marker", None),
            review_state=review_state,
            version_id=version_id,
        )

    async def project_view(request: Request) -> Response:
        # Public by its link, like a file: no owner asked.
        found = await run_in_threadpool(store.public_project, request.path_params["id"])
        if found is None:
            return pages.not_found()
        p, files = found
        return pages.project_page(p, files, f"{config.public_url}/p/{p.id}", store.clock(),
                                  workspace_author=_workspace_author(request))

    def drive_failure(request: Request, exc: DriveError) -> Response:
        if delegated is not None and isinstance(exc, DriveGrantRequired):
            return RedirectResponse("/auth/drive-access?return=" + quote(request.url.path, safe="/"),
                                    status_code=303, headers={"Cache-Control": "no-store"})
        if delegated is not None and isinstance(exc, DriveAccessError):
            return pages.message(403, "Google Drive could not open this file with your account. "
                                 "Ask your administrator to check access.")
        return _storage_unavailable()

    async def materialize(request: Request, a: Artifact, revision: DriveRevision):
        try:
            if delegated is not None:
                identity = getattr(request.state, "workspace_identity", None)
                subject = identity.subject if identity is not None else None
                return await _materialize_async(request, None, revision, a.size, config.max_file_bytes,
                                                opener=lambda control: delegated.open(subject, control=control))
            if drive is None:
                raise DriveError("Drive transport unavailable")
            return await _materialize_async(request, drive, revision, a.size, config.max_file_bytes)
        except OSError:
            raise DriveError("Drive materialization failed") from None

    async def raw(request: Request) -> Response:
        snapshot = live(request.path_params["id"])
        if snapshot is None:
            return pages.not_found()
        a, revision = snapshot
        kind = kinds.get(a.kind)
        if not kind.viewable:
            return await download_snapshot(request, a, revision)
        if kind.sandboxed and request.headers.get("sec-fetch-dest") == "document":
            return RedirectResponse(f"/a/{a.id}", status_code=303)
        return await content(request, a, revision, kind)

    async def raw_origin(request: Request) -> Response:
        match = _RAW_PATH.fullmatch(request.scope["path"])
        if request.method not in ("GET", "HEAD") or match is None:
            return _raw_origin_not_found()
        snapshot = live(match[1])
        if snapshot is None:
            return _raw_origin_not_found()
        a, revision = snapshot
        if revision is not None and delegated is not None:
            return _raw_origin_not_found()
        kind = kinds.get(a.kind)
        if not kind.viewable:
            return _raw_origin_not_found()
        response = await content(request, a, revision, kind)
        return _raw_origin_not_found() if response.status_code == 404 else response

    async def content(
        request: Request, a: Artifact, revision: DriveRevision | None, kind: kinds.Kind,
    ) -> Response:
        csp = pages.SANDBOX_CSP if kind.sandboxed else pages.RAW_CSP
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": csp}
        temp = None
        storage = store.storage
        if revision is not None:
            try:
                temp = await materialize(request, a, revision)
            except _ClientDisconnected:
                return _DisconnectedResponse()
            except DriveError as exc:
                return drive_failure(request, exc)
            storage = temp
        try:
            if kind.name in ("html", "md", "txt"):
                body, length = await run_in_threadpool(_prepare_page, storage, a)
                if length is not None:
                    headers["Content-Length"] = str(length)
                response = StreamingResponse(body, headers=headers, media_type="text/html")
            else:
                response = _disk_response(storage, a.id, revision=revision, media_type=kind.mime,
                                          headers=headers)
            return _TemporaryResponse(response, temp) if temp is not None else response
        except FileNotFoundError:
            if temp is not None:
                temp.cleanup()
            return pages.not_found()
        except BaseException:
            if temp is not None:
                temp.cleanup()
            raise

    async def download_snapshot(
        request: Request, a: Artifact, revision: DriveRevision | None,
    ) -> Response:
        if revision is None:
            return _download(store.storage, a.id, a.name)
        try:
            temp = await materialize(request, a, revision)
        except _ClientDisconnected:
            return _DisconnectedResponse()
        except DriveError as exc:
            return drive_failure(request, exc)
        try:
            return _TemporaryResponse(_download(temp, a.id, a.name, revision=revision), temp)
        except BaseException:
            temp.cleanup()
            raise

    async def download(request: Request) -> Response:
        snapshot = live(request.path_params["id"])
        if snapshot is None:
            return pages.not_found()
        return await download_snapshot(request, *snapshot)

    def version_snapshot(request: Request) -> tuple[Artifact, DriveRevision] | None:
        if config.workspace is None:
            return None
        return store.get_drive_version(request.path_params["id"], request.path_params["version"])

    async def version_view(request: Request) -> Response:
        snapshot = version_snapshot(request)
        if snapshot is None:
            return pages.not_found()
        a, revision = snapshot
        try:
            temp = await materialize(request, a, revision)
        except _ClientDisconnected:
            return _DisconnectedResponse()
        except DriveError as exc:
            return drive_failure(request, exc)
        temp.cleanup()
        path = f"/a/{a.id}/versions/{request.path_params['version']}"
        name = html.escape(a.name)
        kind = kinds.get(a.kind)
        sandbox = f' sandbox="{pages.SANDBOX}"' if kind.sandboxed else ""
        frame = (f'<iframe title="{name}"{sandbox} src="{path}/raw"></iframe>'
                 if kind.viewable else "")
        return HTMLResponse(
            f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<link rel="stylesheet" href="/static/site.css"><title>{name}</title></head>'
            f'<body><header><div class="name"><h1>{name}</h1></div>'
            f'<div class="actions"><a href="{path}/download">Download</a></div></header>'
            f'<div class="stage"><main>{frame}</main></div></body></html>',
            headers={"Content-Security-Policy": pages.VIEWER_CSP, "Cache-Control": "no-store"},
        )

    async def version_raw(request: Request) -> Response:
        snapshot = version_snapshot(request)
        if snapshot is None:
            return pages.not_found()
        a, revision = snapshot
        kind = kinds.get(a.kind)
        if not kind.viewable:
            return await download_snapshot(request, a, revision)
        if kind.sandboxed and request.headers.get("sec-fetch-dest") == "document":
            return RedirectResponse(
                f"/a/{a.id}/versions/{request.path_params['version']}", status_code=303,
            )
        return await content(request, a, revision, kind)

    async def version_download(request: Request) -> Response:
        snapshot = version_snapshot(request)
        if snapshot is None:
            return pages.not_found()
        return await download_snapshot(request, *snapshot)

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

    def authorize_drive_review(artifact_id: str, subject: str) -> None:
        assert delegated is not None
        snapshot = store.get_with_drive(artifact_id)
        if snapshot is None or snapshot[1] is None:
            return  # missing and disk-backed items retain the existing review rules
        control = DriveReadControl(time.monotonic() + _DRIVE_READ_BUDGET_SECONDS)
        with delegated.open(subject, control=control) as storage:
            storage.check_read_access(snapshot[1].file_id, control=control)

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
    main_auth = None
    if config.workspace is not None:
        auth_store = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", config.workspace)
        oidc = GoogleOIDC(config.workspace, config.public_url, config.raw_url)
        main_auth = WorkspaceAuth(auth_store, oidc, origin=config.public_url, accounts=config.accounts,
                                  drive_grants=grants)
        raw_auth = (WorkspaceAuth(auth_store, oidc, origin=config.raw_url, raw=True)
                    if config.raw_url else None)
        middleware.append(Middleware(_WorkspaceAccess, main=main_auth, raw=raw_auth,
                                     review_permission=authorize_drive_review if delegated is not None else None))
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
            Route("/a/{id:artifact_id}/versions/{version:artifact_id}", version_view),
            Route("/a/{id:artifact_id}/versions/{version:artifact_id}/raw", version_raw),
            Route("/a/{id:artifact_id}/versions/{version:artifact_id}/download", version_download),
            Route("/p/{id:artifact_id}", project_view),
            Route("/join/{token:invite_token}", join, methods=["GET", "POST"]),
            Mount("/static", StaticFiles(directory=pages.STATIC_DIR), name="static"),
            *admin_routes(store, config.public_url),
            *comment_routes(store, drive_viewer=delegated is not None,
                            versioned_reviews=config.review_docs and delegated is not None,
                            read_only=config.drive_read_only),
            *(report_routes(store, config.public_url, delegated, read_only=config.drive_read_only)
              if config.review_docs and delegated is not None else []),
            *(AccountPages(store, config, main_auth).routes() if config.accounts else []),
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


_BROWSER_NAV = re.compile(
    rf"(?:/a/{ID_RE}(?:/versions/{ID_RE})?|/p/{ID_RE}|/join/{TOKEN_RE})"
)
_REVIEW_PATH = re.compile(rf"/a/({ID_RE})/(?:comments|drafts|review)(?:/.*)?")


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

    def __init__(self, app, main: WorkspaceAuth, raw: WorkspaceAuth | None,  # noqa: ANN001
                 review_permission: Callable[[str, str], None] | None = None) -> None:
        self.app = app
        self.main = main
        self.raw = raw
        self.review_permission = review_permission
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
            if auth.drive_grants is not None:
                auth_routes["/auth/drive-access"] = ("GET", auth.drive_access)
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

        account_path = auth.accounts and (path == "/account" or path.startswith("/account/"))
        protected = auth.raw or account_path or any(path == prefix or path.startswith(prefix + "/")
                                                   for prefix in ("/a", "/p", "/join"))
        if not protected:
            await self.app(scope, receive, send)
            return

        identity = await auth.identity(request)
        if identity is None:
            navigation = (method == "GET" and
                          ((not auth.raw and (_BROWSER_NAV.fullmatch(path) or
                                             (account_path and path in ACCOUNT_ENTRY_PATHS))) or
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
        review = _REVIEW_PATH.fullmatch(path) if not auth.raw else None
        if review:
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
            if self.review_permission is not None:
                blocked = None
                try:
                    await run_in_threadpool(self.review_permission, review[1], identity.subject)
                except DriveGrantRequired:
                    blocked = error(403, "drive_authorization_required", "Authorize Google Drive to open this review.")
                except DriveAccessError:
                    blocked = error(403, "drive_access_denied", "Ask your administrator to check access to the Drive file.")
                except DriveError:
                    blocked = error(503, "storage_unavailable", "Drive is temporarily unavailable.")
                if blocked is not None:
                    blocked.headers["Cache-Control"] = "no-store"
                    await blocked(scope, receive, send)
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


class _ClientDisconnected(Exception):
    pass


class _DisconnectedResponse(Response):
    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        return


class _TemporaryStorage:
    """One private, bounded Drive download, owned by one response."""

    def __init__(self, directory: tempfile.TemporaryDirectory) -> None:
        self.directory = directory
        self.path = Path(directory.name) / "content"
        self.opened = []

    def open(self, artifact_id: str):
        f = self.path.open("rb")
        self.opened.append(f)
        return f

    def size(self, f):
        return self.path.stat().st_size

    def file_path(self, artifact_id: str) -> Path:
        return self.path

    def cleanup(self) -> None:
        for f in self.opened:
            f.close()
        self.directory.cleanup()


class _TemporaryResponse(Response):
    def __init__(self, response: Response, storage: _TemporaryStorage) -> None:
        super().__init__(status_code=response.status_code)
        self.response = response
        self.storage = storage

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        try:
            await self.response(scope, receive, send)
        finally:
            self.storage.cleanup()


async def _materialize_async(
    request: Request, drive: DriveStorage | None, revision: DriveRevision, expected_size: int,
    max_bytes: int, *, opener: Callable[[DriveReadControl], AbstractContextManager[DriveStorage]] | None = None,
) -> _TemporaryStorage:
    control = DriveReadControl(time.monotonic() + _DRIVE_READ_BUDGET_SECONDS)

    def read() -> _TemporaryStorage:
        if opener is None:
            return _materialize_revision(drive, revision, expected_size, max_bytes, control)
        with opener(control) as authorized:
            return _materialize_revision(authorized, revision, expected_size, max_bytes, control)

    worker = asyncio.create_task(run_in_threadpool(read))

    async def watch_disconnect() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    watcher = asyncio.create_task(watch_disconnect())

    def finish_detached(task: asyncio.Task) -> None:
        try:
            storage = task.result()
        except asyncio.CancelledError:
            logging.getLogger(__name__).warning("detached Drive worker cancelled")
        except Exception as exc:
            logging.getLogger(__name__).warning("detached Drive worker: %s", type(exc).__name__)
        else:
            storage.cleanup()

    detached = True
    try:
        done, _ = await asyncio.wait(
            (worker, watcher), timeout=_DRIVE_READ_BUDGET_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if watcher in done:
            watcher.result()
            raise _ClientDisconnected
        if worker not in done:
            raise DriveError("Drive read deadline exceeded")
        result = worker.result()
        detached = False
        return result
    finally:
        watcher.cancel()
        if detached:
            control.stop()
            worker.add_done_callback(finish_detached)


def _materialize_revision(
    drive: DriveStorage, revision: DriveRevision, expected_size: int, max_bytes: int,
    control: DriveReadControl,
) -> _TemporaryStorage:
    control.check()
    storage = _TemporaryStorage(tempfile.TemporaryDirectory(prefix="aa-drive-"))
    try:
        size = 0
        with (
            storage.path.open("wb") as out,
            drive.open_revision(revision, control=control) as chunks,
        ):
            for chunk in chunks:
                control.check()
                size += len(chunk)
                if size > max_bytes or size > expected_size:
                    raise DriveError("Drive revision exceeds expected size")
                out.write(chunk)
        control.check()
        if size != expected_size:
            raise DriveError("Drive revision size mismatch")
        return storage
    except BaseException as exc:
        storage.cleanup()
        if isinstance(exc, OSError):
            raise DriveError("Drive materialization failed") from None
        raise


def _storage_unavailable() -> Response:
    return pages.message(503, "This file is temporarily unavailable.")


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


class _DriveFileResponse(FileResponse):
    """A temporary copy uses the committed revision, never spool timestamps, as validator."""

    def set_stat_headers(self, stat_result) -> None:  # noqa: ANN001
        self.headers.setdefault("content-length", str(stat_result.st_size))

    def _should_use_range(self, http_if_range: str) -> bool:
        return http_if_range == self.headers["etag"]


def _disk_response(
    storage: DiskStorage, artifact_id: str, *, revision: DriveRevision | None = None, **kwargs,
) -> Response:  # noqa: ANN003
    if revision is None:
        return FileResponse(storage.file_path(artifact_id), **kwargs)
    value = json.dumps(["drive-blob-v1", revision.file_id, revision.revision_id],
                       separators=(",", ":")).encode()
    etag = '"' + hashlib.sha256(value).hexdigest() + '"'
    kwargs["headers"] = {**kwargs.get("headers", {}), "ETag": etag}
    return _DriveFileResponse(storage.file_path(artifact_id), **kwargs)


def _download(
    storage: DiskStorage, artifact_id: str, name: str, *, revision: DriveRevision | None = None,
) -> Response:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return _disk_response(
        storage, artifact_id, revision=revision,
        media_type="application/octet-stream",
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )
