"""Standalone, bounded Google Docs report adapter; no application mapping or retention.

Callers must serialize creation per artifact with a durable claim and reconcile an
uncertain create before trying again. An empty lookup after a timeout is not proof
of failure. Folder checks and writes are not atomic. This adapter supports only a
single tab containing ordinary text paragraphs, up to the Docs-only JSON response
limit; manual changes to markers, tables and other rich structures are unsupported.
A project-layout adapter binds every operation to the artifact blob's managed parent.
The conservative read budget assumes projected compact JSON, a single section break,
printable ASCII revision/tab IDs of at most 1024 characters, and no outside edits after
our read. Outside edits or provider changes can invalidate that budget. Real
shared-drive permissions, indexing latency and Docs behavior need validation.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
import urllib3

from .drive_storage import (
    _API,
    DriveAccessError,
    DriveProtocolError,
    DriveStorage,
    DriveTransientError,
    _reference,
    _segment,
)
from .store import ID_RE

_DOCS = "https://docs.googleapis.com/v1/documents"
_DOC_JSON_LIMIT = 4 * 1024 * 1024
_MIME = "application/vnd.google-apps.document"
_MARKER = "agentArtifactsReport"
_PREFIX = "[agent-artifacts-round:"
_DOC_FIELDS = ("revisionId,tabs(tabProperties(tabId),childTabs(tabProperties(tabId)),"
               "documentTab(body(content(endIndex,sectionBreak,paragraph(elements(textRun(content)))))))")


def _artifact_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(ID_RE, value):
        raise ValueError("invalid artifact ID")


def _origin(value: str) -> str:
    if (not isinstance(value, str) or not value.startswith("https://")
            or any(c in value for c in "?#@\\%") or not value.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError("public_url must be an HTTPS app origin")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (not host or parsed.path not in ("", "/") or parsed.username is not None
                or parsed.password is not None or (port is not None and port < 1)):
            raise ValueError
        authority = parsed.netloc
        if authority.startswith("["):
            ipaddress.IPv6Address(host)
            if not re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::[0-9]+)?", authority):
                raise ValueError
        elif (not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", authority)
              or len(host) > 253
              or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p)
                     for p in host.split("."))):
            raise ValueError
        elif re.fullmatch(r"[0-9.]+", host):
            ipaddress.IPv4Address(host)
    except ValueError:
        raise ValueError("public_url must be an HTTPS app origin") from None
    return value.rstrip("/")


class ReportConflict(DriveProtocolError):
    """Ambiguous lookup, changed round, or unsupported report structure."""


class ReportLimitError(DriveProtocolError):
    """The candidate report cannot safely fit a bounded reconciliation read."""


@dataclass(frozen=True)
class ReportRound:
    key: str
    version_url: str
    goal: str
    decision: str
    reasons: str
    open_items: str


@dataclass(frozen=True)
class RenderedReportRound:
    """Exact writer framing and metadata for independent future source verification."""

    key: str
    digest: str
    version_url: str
    framed_utf16_length: int
    link_start: int
    link_end: int
    text: str


class DocsReport:
    """Uses the same caller-owned, no-redirect/no-replay transport as DriveStorage."""

    def __init__(self, drive: DriveStorage, *, public_url: str, artifact_file_id: str | None = None):
        if drive.project_layout != (artifact_file_id is not None):
            raise ValueError("project reports must be bound to the Drive artifact")
        self._drive = drive
        self._artifact_file_id = _reference(artifact_file_id) if artifact_file_id is not None else None
        self._origin = _origin(public_url)

    @classmethod
    def for_folder(cls, session, folder_id: str, *, public_url: str, timeout: float = 30,
                   folder_kind: str = "shared-drive") -> DocsReport:
        """Standalone mode for probes, without application project routing."""
        return cls(DriveStorage(session, folder_id, max_bytes=1, timeout=timeout, folder_kind=folder_kind),
                   public_url=public_url)

    def _parent(self, drive_id: str | None, *, create: bool = False) -> str:
        if self._artifact_file_id is None:
            return self._drive.folder_id
        artifact = self._drive._validate_file(self._artifact_file_id, drive_id, edit=False)
        parent = artifact["parents"][0]
        if parent == self._drive.folder_id:
            raise DriveAccessError("a project report requires a managed project or unfiled folder")
        if create:
            self._drive._validate_child(self._drive._metadata(parent), parent, drive_id, write=True)
        return parent

    def _json_request(self, method, url, *, document=False, **kwargs):
        response = self._drive._request(method, url, **kwargs)
        try:
            if not document:
                return self._drive._json(response)
            body = bytearray()
            try:
                for chunk in self._drive._chunks(response, None):
                    body.extend(chunk)
                    if len(body) > _DOC_JSON_LIMIT:
                        raise DriveProtocolError("Docs JSON response exceeds limit")
            except (requests.RequestException, urllib3.exceptions.HTTPError, OSError):
                raise DriveTransientError("Docs response stream failed") from None
            try:
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError
                return result
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise DriveProtocolError("invalid Docs JSON response") from None
        finally:
            response.close()

    def matches_target(self, folder_id: str, public_url: str) -> bool:
        """Require the report and artifact adapters to use the same folder and app origin."""
        return self._drive.folder_id == folder_id and self._origin == _origin(public_url)

    def report_url(self, file_id: str) -> str:
        """Link to the mapped native Doc; Workspace controls access to it."""
        return f"https://docs.google.com/document/d/{_segment(file_id)}/edit"

    def version_url(self, artifact_id: str, version_id: str) -> str:
        """Construct a private app route; the caller must verify publication."""
        _artifact_id(artifact_id)
        _artifact_id(version_id)
        return f"{self._origin}/a/{artifact_id}/versions/{version_id}"

    def validate_round(self, artifact_id: str, round_: ReportRound) -> None:
        """Validate a candidate without contacting Google."""
        self._block(artifact_id, round_)

    def create(self, artifact_id: str, *, claim: object,
               before_create: Callable[[], None] | None = None) -> str:
        """Create once while holding a caller-managed exclusive per-artifact claim.

        The claim is an explicit acknowledgement, not a lock verified by this adapter.
        The optional hook runs after folder validation, immediately before the POST;
        raising from it prevents dispatch. Never blindly retry an uncertain create.
        """
        _artifact_id(artifact_id)
        if claim is None:
            raise ValueError("caller-managed per-artifact claim required")
        drive_id = self._drive.validate_folder()
        parent = self._parent(drive_id, create=True)
        if before_create is not None:
            before_create()
        data = self._json_request("POST", _API,
                                  params={"supportsAllDrives": "true", "fields": "id"},
                                  json={"name": "Review report", "mimeType": _MIME,
                                        "parents": [parent],
                                        "appProperties": {_MARKER: artifact_id}})
        try:
            return _reference(data["id"])
        except (KeyError, ValueError, TypeError):
            raise DriveProtocolError("invalid create response; outcome may be unknown") from None

    def lookup(self, artifact_id: str) -> str | None:
        """Bounded read-only lookup; absence is not proof an uncertain create failed."""
        _artifact_id(artifact_id)
        drive_id = self._drive.validate_folder(write=False)
        parent = self._parent(drive_id)
        query = (f"'{parent}' in parents and trashed = false and "
                 f"appProperties has {{ key='{_MARKER}' and value='{artifact_id}' }}")
        corpus = ({"corpora": "user"} if drive_id is None else
                  {"corpora": "drive", "driveId": drive_id, "includeItemsFromAllDrives": "true"})
        data = self._json_request("GET", _API, params={
            "q": query, "supportsAllDrives": "true", **corpus,
            "pageSize": 2, "fields": "nextPageToken,incompleteSearch,files(id)",
        })
        files = data.get("files")
        if (not isinstance(files, list) or data.get("incompleteSearch") is True
                or data.get("nextPageToken") or len(files) > 1):
            raise ReportConflict("report lookup incomplete or ambiguous")
        if not files:
            return None
        try:
            file_id = _reference(files[0]["id"])
        except (KeyError, ValueError, TypeError):
            raise DriveProtocolError("invalid lookup response") from None
        self._validate(file_id, artifact_id, drive_id, parent=parent)
        return file_id

    def _validate(self, file_id: str, artifact_id: str, drive_id: str | None, *, parent: str | None = None) -> None:
        if parent is None:
            parent = self._parent(drive_id)
        data = self._drive._metadata(file_id)
        caps = data.get("capabilities")
        if (data.get("id") != file_id or data.get("driveId") != drive_id
                or data.get("mimeType") != _MIME or data.get("trashed") is not False
                or data.get("parents") != [parent]
                or not isinstance(data.get("appProperties"), dict)
                or data["appProperties"].get(_MARKER) != artifact_id
                or not isinstance(caps, dict) or caps.get("canEdit") is not True):
            raise DriveAccessError("report is not an editable tagged document in the folder")

    def render_round(self, artifact_id: str, round_: ReportRound) -> RenderedReportRound:
        return self.render_for(self._origin, artifact_id, round_)

    @staticmethod
    def render_for(public_url: str, artifact_id: str, round_: ReportRound) -> RenderedReportRound:
        """Render the exact append payload and metadata before opening a transport."""
        _reference(round_.key)
        _artifact_id(artifact_id)
        if len(round_.key) > 128:
            raise ValueError("round key is too long")
        if not re.fullmatch(re.escape(_origin(public_url)) + rf"/a/{artifact_id}/versions/{ID_RE}",
                            round_.version_url):
            raise ValueError("version URL must be an exact same-app artifact version URL")
        fields = (round_.goal, round_.decision, round_.reasons, round_.open_items)
        if any(not isinstance(v, str) or len(v) > 8000 or _PREFIX in v
               or any(ord(c) <= 8 or 12 <= ord(c) <= 31
                      or 0xD800 <= ord(c) <= 0xF8FF for c in v) for v in fields):
            raise ValueError("invalid round text")
        content = (f"Goal: {fields[0]}\nVersion: {round_.version_url}\n"
                   f"Decision: {fields[1]}\nReasons: {fields[2]}\nOpen items: {fields[3]}\n")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        head = f"{_PREFIX}{round_.key}:{digest}]\n"
        block = head + content + f"[agent-artifacts-end:{round_.key}]\n"
        start = len((head + f"Goal: {fields[0]}\nVersion: ").encode("utf-16-le")) // 2
        return RenderedReportRound(round_.key, digest, round_.version_url,
                                   len(block.encode("utf-16-le")) // 2, start,
                                   start + len(round_.version_url.encode("utf-16-le")) // 2,
                                   block)

    def _block(self, artifact_id: str, round_: ReportRound) -> tuple[str, int, int]:
        rendered = self.render_round(artifact_id, round_)
        return rendered.text, rendered.link_start, rendered.link_end

    def append(self, file_id: str, artifact_id: str, round_: ReportRound, *,
               before_write: Callable[[], None] | None = None, expected_revision: str | None = None) -> bool:
        """Append once or return False if exactly present; caller reconciles failures.

        Revision conflicts and uncertain writes are never replayed automatically. A
        fresh call re-reads the marker and revision before considering another write.
        The optional hook runs after preflight, immediately before batchUpdate; raising
        from it prevents dispatch. It is not called when an existing round is found.
        """
        _reference(file_id)
        block, link_start, link_end = self._block(artifact_id, round_)
        drive_id = self._drive.validate_folder()
        self._validate(file_id, artifact_id, drive_id)
        url = f"{_DOCS}/{_segment(file_id)}"
        data = self._json_request("GET", url, params={"includeTabsContent": "true",
                                                       "fields": _DOC_FIELDS,
                                                       "prettyPrint": "false"},
                                  headers={"Accept-Encoding": "identity"}, document=True)
        try:
            tabs = data["tabs"]
            if len(tabs) != 1 or tabs[0].get("childTabs"):
                raise ValueError
            elements = tabs[0]["documentTab"]["body"]["content"]
            revision = data["revisionId"]
            tab_id = tabs[0]["tabProperties"]["tabId"]
            if (any(not isinstance(value, str) or not 0 < len(value) <= 1024
                    or any(not 32 <= ord(c) <= 126 for c in value)
                    for value in (tab_id, revision)) or not isinstance(elements, list)):
                raise ValueError
            text_parts = []
            sections = 0
            for element in elements:
                if "sectionBreak" in element:
                    sections += 1
                    if sections > 1:
                        raise ValueError
                    continue
                for part in element["paragraph"]["elements"]:
                    content = part["textRun"]["content"]
                    if not isinstance(content, str) or not content:
                        raise ValueError
                    text_parts.append(content)
            text = "".join(text_parts)
            end = elements[-1]["endIndex"] - 1
            if not isinstance(end, int) or end < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError, IndexError):
            raise ReportConflict("unsupported document structure") from None
        if expected_revision is not None and revision != expected_revision:
            raise ReportConflict("report changed after source verification")
        marker = f"{_PREFIX}{round_.key}:"
        if marker in text:
            if text.count(marker) == 1 and block in text:
                return False
            raise ReportConflict("round key already present with different content")
        # Charge every UTF-16 unit as a separate JSON text run and each newline as
        # a paragraph. This covers JSON escaping, run/paragraph wrappers and indices;
        # reserve 16 KiB for bounded IDs and the rest of the projected response.
        separator = "\n" if text and not text[:-1].endswith("\n") and text != "\n" else ""
        inserted = separator + block
        try:
            candidate = text + inserted
            units = len(candidate.encode("utf-16-le")) // 2
        except UnicodeEncodeError:
            raise ReportConflict("invalid document text") from None
        if 16384 + 64 * units + 128 * candidate.count("\n") > _DOC_JSON_LIMIT:
            raise ReportLimitError("report would exceed reconciliation read budget")
        link_start += len(separator)
        link_end += len(separator)
        if before_write is not None:
            before_write()
        self._json_request("POST", f"{url}:batchUpdate", json={
            "writeControl": {"requiredRevisionId": revision},
            "requests": [
                {"insertText": {"location": {"index": end, "tabId": tab_id}, "text": inserted}},
                {"updateTextStyle": {"range": {"startIndex": end + link_start,
                                              "endIndex": end + link_end, "tabId": tab_id},
                                     "textStyle": {"link": {"url": round_.version_url}},
                                     "fields": "link"}},
            ],
        })
        return True
