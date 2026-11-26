"""Google Drive blob revisions. Authentication and application metadata belong to the caller."""

from __future__ import annotations

import json
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote

import requests
import urllib3
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession

_API = "https://www.googleapis.com/drive/v3/files"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_FOLDER = "application/vnd.google-apps.folder"
_FIELDS = ("id,name,driveId,mimeType,trashed,parents,headRevisionId,appProperties,"
           "capabilities(canAddChildren,canListChildren,canEdit,canDownload,canRename)")
_MARKER = "agentArtifactsOperation"
_FOLDER_MARKER = "agentArtifactsFolder"
_FOLDER_SCOPE = re.compile(r"(?:unfiled|p-[A-Za-z0-9_-]{22})")
_JSON_LIMIT = 64 * 1024
_CAPACITY_REASONS = frozenset({
    "dailyLimitExceeded", "downloadQuotaExceeded", "rateLimitExceeded",
    "sharingRateLimitExceeded", "storageQuotaExceeded", "teamDriveFileLimitExceeded",
    "userRateLimitExceeded",
})


class DriveError(Exception):
    """A Drive operation failed; never interpret this as proof of deletion."""


class DriveAccessError(DriveError):
    """Access was denied or the object is inaccessible (including HTTP 404)."""


class DriveTransientError(DriveError):
    """A transport or temporary service failure; writes may have succeeded."""


class DriveCapacityError(DriveError):
    """A capacity or rate limit was encountered."""


class DriveReadControl:
    """Cooperative stop and monotonic deadline for one bounded read."""

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self._stopped = threading.Event()

    def stop(self) -> None:
        self._stopped.set()

    def check(self) -> float:
        remaining = self.deadline - time.monotonic()
        if self._stopped.is_set() or remaining <= 0:
            raise DriveTransientError("Drive read stopped or timed out")
        return remaining


class DriveUploadLimitError(DriveError):
    """The local media transfer exceeds the configured byte limit."""


class DriveStagingError(DriveError):
    """Local preparation of a media upload failed before dispatch."""


class DriveProtocolError(DriveError):
    """Drive returned unexpected metadata or rejected the request."""


@dataclass(frozen=True)
class DriveRevision:
    file_id: str
    revision_id: str

    def __post_init__(self) -> None:
        _reference(self.file_id)
        _reference(self.revision_id)


def _reference(value: str) -> str:
    # References are opaque but must be safe as single URL path segments.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", value):
        raise ValueError("invalid Drive reference")
    return value


def _segment(value: str) -> str:
    return quote(_reference(value), safe="")


class DriveAuthorizedSession(AuthorizedSession):
    """Authorized transport that neither resolves redirects nor replays on a 401.

    Credentials may refresh before a request; the caller owns and closes the session.
    Do not configure HTTP adapters to retry requests.
    """

    def __init__(self, credentials) -> None:
        super().__init__(credentials, max_refresh_attempts=0)

    def resolve_redirects(self, *args, **kwargs):
        return iter(())


class DriveStorage:
    """Use DriveAuthorizedSession or an equivalent injected transport.

    The caller-owned transport must stream responses without draining redirects, not
    follow redirects, and never replay writes (including credential-refresh replay).
    Folder checks are not a tenant authorization test or an atomic containment lock.
    """

    def __init__(self, session, folder_id: str, *, max_bytes: int, timeout: float = 30,
                 project_layout: bool = False, folder_kind: str = "shared-drive") -> None:
        self._session = session
        self._folder_id = _reference(folder_id)
        self.project_layout = project_layout
        if folder_kind not in ("my-drive", "shared-drive"):
            raise ValueError("Drive folder_kind must be my-drive or shared-drive")
        self._folder_kind = folder_kind
        if max_bytes <= 0 or timeout <= 0:
            raise ValueError("max_bytes and timeout must be positive")
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._drive_id: str | None = None

    @property
    def folder_id(self) -> str:
        return self._folder_id

    def _request(self, method: str, url: str, *, control: DriveReadControl | None = None, **kwargs):
        timeout = min(self._timeout, 2.0, control.check()) if control else self._timeout
        try:
            response = self._session.request(
                method, url, timeout=timeout, allow_redirects=False, stream=True, **kwargs
            )
        except (requests.RequestException, GoogleAuthError):
            raise DriveTransientError(
                "Drive transport failed; write outcome may be unknown"
            ) from None
        if control is not None:
            try:
                control.check()
            except DriveError:
                response.close()
                raise
        if not 200 <= response.status_code < 300:
            status = response.status_code
            capacity = False
            try:
                if status == 403:
                    try:
                        data = self._json(response, control=control)
                        errors = data.get("error", {}).get("errors", [])
                        capacity = isinstance(errors, list) and any(
                            isinstance(item, dict) and item.get("reason") in _CAPACITY_REASONS
                            for item in errors
                        )
                    except (DriveProtocolError, AttributeError, TypeError):
                        pass
            finally:
                response.close()
            if capacity:
                raise DriveCapacityError("Drive capacity or rate limit (HTTP 403)")
            if status in (401, 403, 404):
                raise DriveAccessError(f"Drive access unavailable (HTTP {status})")
            if status in (429, 507):
                raise DriveCapacityError(f"Drive capacity or rate limit (HTTP {status})")
            if status >= 500 or status == 408:
                raise DriveTransientError(f"Drive temporarily unavailable (HTTP {status})")
            raise DriveProtocolError(f"Drive request rejected (HTTP {status})")
        return response

    @staticmethod
    def _chunks(response, control: DriveReadControl | None) -> Iterator[bytes]:
        if control is None:
            yield from response.iter_content(chunk_size=8192)
            return
        headers = getattr(response, "headers", {})
        if headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
            raise DriveProtocolError("unexpected compressed Drive response")
        raw = getattr(response, "raw", None)
        if isinstance(raw, urllib3.response.HTTPResponse):
            while True:
                control.check()
                chunk = raw.read1(64 * 1024, decode_content=False)
                control.check()
                if not chunk:
                    break
                yield chunk
        else:
            control.check()
            chunks = iter(response.iter_content(chunk_size=1))
            while True:
                control.check()
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
                control.check()
                yield chunk

    @staticmethod
    def _json(response, *, control: DriveReadControl | None = None) -> dict:
        body = bytearray()
        try:
            for chunk in DriveStorage._chunks(response, control):
                body.extend(chunk)
                if len(body) > _JSON_LIMIT:
                    raise DriveProtocolError("Drive JSON response exceeds limit")
        except (requests.RequestException, urllib3.exceptions.HTTPError, OSError):
            raise DriveTransientError("Drive response stream failed") from None
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise DriveProtocolError("invalid Drive JSON response") from None

    def _metadata(self, file_id: str, *, control: DriveReadControl | None = None) -> dict:
        response = self._request("GET", f"{_API}/{_segment(file_id)}", params={
            "supportsAllDrives": "true", "fields": _FIELDS,
        }, headers={"Accept-Encoding": "identity"}, control=control)
        try:
            return self._json(response, control=control)
        finally:
            response.close()

    def validate_folder(self, *, write: bool = True, control: DriveReadControl | None = None) -> str | None:
        data = self._metadata(self._folder_id, control=control)
        drive_id = data.get("driveId")
        caps = data.get("capabilities")
        if (data.get("id") != self._folder_id or data.get("mimeType") != _FOLDER
                or data.get("trashed") is not False or not isinstance(caps, dict)
                or caps.get("canListChildren") is not True
                or (write and caps.get("canAddChildren") is not True)):
            raise DriveAccessError("configured Drive folder is not accessible for this operation")
        if self._folder_kind == "my-drive":
            if drive_id is not None:
                raise DriveAccessError("configured folder is not on My Drive")
            return None
        if not isinstance(drive_id, str) or not drive_id or (self.project_layout and drive_id == self._folder_id):
            raise DriveAccessError("configured folder is not inside the expected Shared Drive")
        try:
            _reference(drive_id)
        except ValueError:
            raise DriveProtocolError("invalid shared drive reference") from None
        if self._drive_id is not None and drive_id != self._drive_id:
            raise DriveAccessError("configured folder changed shared drive")
        self._drive_id = drive_id
        return drive_id

    def _validate_child(self, data: dict, folder_id: str, drive_id: str | None, *,
                        scope: str | None = None, write: bool = False) -> None:
        props, caps = data.get("appProperties"), data.get("capabilities")
        marker = props.get(_FOLDER_MARKER) if isinstance(props, dict) else None
        if (not self.project_layout or data.get("id") != folder_id or data.get("driveId") != drive_id
                or data.get("mimeType") != _FOLDER or data.get("trashed") is not False
                or data.get("parents") != [self._folder_id]
                or not isinstance(marker, str) or not _FOLDER_SCOPE.fullmatch(marker)
                or (scope is not None and marker != scope)
                or (write and (not isinstance(caps, dict) or caps.get("canAddChildren") is not True))):
            raise DriveAccessError("folder is not a managed child of the configured root")

    def check_managed_folder(self, folder_id: str, scope: str) -> None:
        drive_id = self.validate_folder()
        self._validate_child(self._metadata(folder_id), folder_id, drive_id, scope=scope, write=True)

    def create_managed_folder(self, folder_id: str, label: str, scope: str) -> None:
        if not self.project_layout or not _FOLDER_SCOPE.fullmatch(scope):
            raise ValueError("invalid managed Drive folder scope")
        drive_id = self.validate_folder()
        response = self._request("POST", _API, params={"supportsAllDrives": "true", "fields": _FIELDS},
                                 json={"id": _reference(folder_id), "name": label, "mimeType": _FOLDER,
                                       "parents": [self._folder_id],
                                       "appProperties": {_FOLDER_MARKER: scope}})
        try:
            self._validate_child(self._json(response), folder_id, drive_id, scope=scope, write=True)
        finally:
            response.close()

    def _validate_file(self, file_id: str, drive_id: str | None, *, edit: bool,
                       control: DriveReadControl | None = None,
                       expected_parent: tuple[str, str] | None = None) -> dict:
        data = self._metadata(file_id, control=control)
        caps, parents = data.get("capabilities"), data.get("parents")
        if (data.get("id") != file_id or data.get("driveId") != drive_id
                or data.get("trashed") is not False
                or not isinstance(parents, list) or len(parents) != 1
                or not isinstance(parents[0], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", parents[0])
                or not isinstance(data.get("mimeType"), str)
                or data["mimeType"].startswith("application/vnd.google-apps.")
                or (edit and (not isinstance(caps, dict) or caps.get("canEdit") is not True))):
            raise DriveAccessError("file is not an accessible blob in the configured folder")
        parent = parents[0]
        if expected_parent is not None and (parent == self._folder_id or parent != expected_parent[0]):
            raise DriveAccessError("file is outside the recorded managed folder")
        if parent != self._folder_id:
            if not self.project_layout:
                raise DriveAccessError("file is outside the configured folder")
            self._validate_child(self._metadata(parent, control=control), parent, drive_id,
                                 scope=expected_parent[1] if expected_parent is not None else None)
        return data

    def generate_file_id(self) -> str:
        """Reserve an ID for a binary blob, without creating a file."""
        response = self._request("GET", f"{_API}/generateIds", params={
            "count": 1, "space": "drive", "type": "files",
        })
        try:
            data = self._json(response)
            ids = data.get("ids")
            if not isinstance(ids, list) or len(ids) != 1:
                raise DriveProtocolError("invalid generated Drive ID response")
            try:
                return _reference(ids[0])
            except ValueError:
                raise DriveProtocolError("invalid generated Drive ID") from None
        finally:
            response.close()

    def inspect_operation(self, file_id: str, operation_id: str) -> tuple[bool, str | None]:
        """Observe the marker and current head; the head is not attributed to the marker."""
        _reference(operation_id)
        drive_id = self.validate_folder(write=False)
        data = self._validate_file(file_id, drive_id, edit=False)
        props = data.get("appProperties")
        matched = isinstance(props, dict) and props.get(_MARKER) == operation_id
        head = data.get("headRevisionId")
        if head is not None:
            try:
                _reference(head)
            except ValueError:
                raise DriveProtocolError("invalid observed Drive revision") from None
        return matched, head

    def create(self, name: str, source: BinaryIO, *, file_id: str | None = None,
               operation_id: str | None = None, parent: tuple[str, str] | None = None,
               before_write: Callable[[], None] | None = None) -> DriveRevision:
        if not isinstance(name, str) or not name or len(name) > 1024:
            raise ValueError("invalid file name")
        if self.project_layout != (parent is not None):
            raise ValueError("parent must match the configured Drive layout")
        drive_id = self.validate_folder()
        folder = self._folder_id
        if parent is not None:
            folder, scope = parent
            self._validate_child(self._metadata(folder), folder, drive_id, scope=scope, write=True)
        metadata = {"name": name, "parents": [folder],
                    "mimeType": "application/octet-stream"}
        if file_id is not None:
            metadata["id"] = _reference(file_id)
        if operation_id is not None:
            metadata["appProperties"] = {_MARKER: _reference(operation_id)}
        revision = self._upload("POST", _UPLOAD, metadata, source, before_write=before_write)
        if file_id is not None and revision.file_id != file_id:
            raise DriveProtocolError("Drive returned a different file ID")
        return revision

    def replace(self, file_id: str, source: BinaryIO, *, operation_id: str | None = None,
                before_write: Callable[[], None] | None = None) -> DriveRevision:
        _reference(file_id)
        metadata = {"appProperties": {_MARKER: _reference(operation_id)}} if operation_id else {}
        result = self._upload("PATCH", f"{_UPLOAD}/{_segment(file_id)}", metadata, source,
                              file_id=file_id, before_write=before_write)
        if result.file_id != file_id:
            raise DriveProtocolError("Drive returned a different file ID")
        return result

    def _upload(self, method: str, url: str, metadata: dict, source: BinaryIO,
                *, file_id: str | None = None,
                before_write: Callable[[], None] | None = None) -> DriveRevision:
        boundary = "drive_" + secrets.token_hex(16)
        body, length = self._spool_upload(metadata, source, boundary)
        with body:
            if file_id is not None:
                # The following check and PATCH are separate; a move can still race them.
                drive_id = self.validate_folder()
                self._validate_file(file_id, drive_id, edit=True)
            if before_write is not None:
                before_write()
            response = self._request(method, url, params={
                "uploadType": "multipart", "supportsAllDrives": "true",
                "keepRevisionForever": "true", "fields": "id,headRevisionId",
            }, headers={"Content-Type": f"multipart/related; boundary={boundary}",
                        "Content-Length": str(length)}, data=body)
            try:
                data = self._json(response)
                return DriveRevision(data["id"], data["headRevisionId"])
            except (ValueError, TypeError, KeyError):
                raise DriveProtocolError(
                    "missing Drive file or head revision ID; write may have succeeded"
                ) from None
            finally:
                response.close()

    def _spool_upload(self, metadata: dict, source: BinaryIO, boundary: str):
        body = None
        try:
            body = tempfile.TemporaryFile()
            body.write((f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                        + json.dumps(metadata) + f"\r\n--{boundary}\r\n"
                        "Content-Type: application/octet-stream\r\n\r\n").encode())
            total = 0
            while chunk := source.read(min(64 * 1024, self._max_bytes + 1 - total)):
                total += len(chunk)
                if total > self._max_bytes:
                    raise DriveUploadLimitError("file exceeds Drive transfer limit")
                body.write(chunk)
            body.write(f"\r\n--{boundary}--\r\n".encode())
            length = body.tell()
            body.seek(0)
            return body, length
        except BaseException as exc:
            if body is not None:
                with suppress(OSError):
                    body.close()
            if isinstance(exc, OSError):
                raise DriveStagingError("Drive upload staging failed") from None
            raise

    def check_read_access(self, file_id: str, *, control: DriveReadControl | None = None) -> None:
        """Check before serving review metadata derived from Drive-backed bytes."""
        drive_id = self.validate_folder(write=False, control=control)
        data = self._validate_file(file_id, drive_id, edit=False, control=control)
        capabilities = data.get("capabilities")
        if not isinstance(capabilities, dict) or capabilities.get("canDownload") is not True:
            raise DriveAccessError("this user cannot download the Drive file")

    @contextmanager
    def open_revision(self, revision: DriveRevision, *,
                      control: DriveReadControl | None = None) -> Iterator[Iterator[bytes]]:
        drive_id = self.validate_folder(write=False, control=control)
        self._validate_file(revision.file_id, drive_id, edit=False, control=control)
        url = f"{_API}/{_segment(revision.file_id)}/revisions/{_segment(revision.revision_id)}"
        response = self._request("GET", url, params={"alt": "media"},
                                 headers={"Accept-Encoding": "identity"}, control=control)

        def chunks() -> Iterator[bytes]:
            total = 0
            try:
                for chunk in self._chunks(response, control):
                    total += len(chunk)
                    if total > self._max_bytes:
                        raise DriveCapacityError("revision exceeds Drive transfer limit")
                    if chunk:
                        yield chunk
            except (requests.RequestException, urllib3.exceptions.HTTPError, OSError):
                raise DriveTransientError("Drive revision stream failed") from None

        try:
            yield chunks()
        finally:
            response.close()
