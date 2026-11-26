"""Export one selected Drive artifact; verify or extract its private archive offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

import requests
import urllib3

from .api import _reject_constant, _unique_json_object
from .config import Config, DriveFolderConfig
from .docs_report import DocsReport, _DOCS, _DOC_JSON_LIMIT, _origin
from .drive_destination import DriveDestination
from .drive_grants import WorkspaceDriveGrants
from .drive_recovery import InventoryError, inventory, save_inventory
from .drive_storage import DriveError, DriveReadControl, DriveRevision, DriveStorage, _API, _reference, _segment
from .report_source import _single_tab
from .store import ID_RE
from .workspace_drive import WorkspaceDrive

_FORMAT = "agent-artifacts-independent-drive-v1"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_BYTES = 10 * 1024 * 1024  # Drive files.export limit
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_REVISIONS = 200
_MANIFEST_BYTES = 256 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ArchiveError(ValueError):
    """Fixed local failure messages; no paths, credentials, or Google bodies."""


def _artifact_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(ID_RE, value):
        raise ArchiveError("invalid application artifact or version ID")
    return value


@dataclass(frozen=True)
class Version:
    version_id: str
    revision_id: str
    size: int

    @property
    def filename(self) -> str:
        return f"version-{self.version_id}.bytes"


@dataclass(frozen=True)
class ArchivePlan:
    destination: DriveDestination
    artifact: str
    file_id: str
    versions: tuple[Version, ...]
    document_id: str | None
    metadata_sha256: str


def plan_archive(backup: Path, expected: DriveDestination, artifact: str, *, include_report: bool = False) -> ArchivePlan:
    """Validate the sqlite snapshot and requested scope before contacting Google."""
    _artifact_id(artifact)
    try:
        if not backup.is_file():
            raise ArchiveError("use a standalone metadata backup file")
        with backup.open("rb") as content:
            before = hashlib.file_digest(content, "sha256").hexdigest()
        data = inventory(backup)
        with backup.open("rb") as content:
            if hashlib.file_digest(content, "sha256").hexdigest() != before:
                raise ArchiveError("metadata backup changed while planning the export")
    except OSError:
        raise ArchiveError("could not read the standalone metadata backup") from None
    if data["destination"] != asdict(expected):
        raise ArchiveError("backup destination does not match the configured Workspace Drive")
    blobs = [row for row in data["blobs"] if row["artifact_id"] == artifact]
    if len(blobs) != 1:
        raise ArchiveError("no committed Drive artifact in the backup")
    file_id = _reference(blobs[0]["file_id"])
    if (any(row["artifact_id"] == artifact or row["file_id"] == file_id
            for row in (*data["media_attempts"], *data["renames"]))
            or any(row["artifact_id"] == artifact
                   for row in (*data["report_attempts"], *data["pending_review_closures"]))):
        raise ArchiveError("resolve pending Drive or Docs operations before exporting this artifact")
    versions = []
    for row in data["versions"]:
        if row["artifact_id"] != artifact:
            continue
        size = row["size"]
        if row["file_id"] != file_id or type(size) is not int or size < 0:
            raise ArchiveError("inconsistent committed version metadata")
        versions.append(Version(_artifact_id(row["version_id"]), _reference(row["revision_id"]), size))
    if (not 1 <= len(versions) <= _MAX_REVISIONS or sum(v.size for v in versions) > _MAX_ARCHIVE_BYTES
            or blobs[0]["revision_id"] not in {v.revision_id for v in versions}):
        raise ArchiveError("incomplete or oversized exact-version set")
    document = None
    if include_report:
        reports = [row for row in data["reports"] if row["artifact_id"] == artifact]
        if (len(reports) != 1 or reports[0]["file_id"] != file_id or not reports[0]["doc_id"]
                or not reports[0]["has_baseline"] or not reports[0]["closed"]):
            raise ArchiveError("a confirmed closed native report is required for report export")
        document = _reference(reports[0]["doc_id"])
    return ArchivePlan(expected, artifact, file_id, tuple(versions), document, before)


@contextmanager
def _private_read(path: Path) -> Iterator[BinaryIO]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ArchiveError("archive entries must be private regular files")
        yield stream


def _copy(path: Path, chunks: Iterable[bytes], *, limit: int,
          exact: int | None = None, expected_digest: str | None = None) -> dict:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), 0o600)
            digest = hashlib.sha256()
            size = 0
            for part in chunks:
                size += len(part)
                if size > limit:
                    raise ArchiveError("content exceeds its archive limit")
                digest.update(part)
                out.write(part)
            sha = digest.hexdigest()
            if (exact is not None and size != exact) or (expected_digest is not None and sha != expected_digest):
                raise ArchiveError("archive content size or digest does not match")
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        with suppress(OSError):
            path.unlink()
        raise
    return {"file": path.name, "size": size, "sha256": sha}


def _doc_snapshot(report: DocsReport, doc: str, artifact: str) -> tuple[str, dict]:
    location = report._drive.validate_folder(write=False)
    report._validate(doc, artifact, location)
    result = report._json_request("GET", f"{_DOCS}/{_segment(doc)}", params={
        "includeTabsContent": "true", "suggestionsViewMode": "SUGGESTIONS_INLINE",
        "fields": "documentId,title,revisionId,tabs", "prettyPrint": "false",
    }, headers={"Accept-Encoding": "identity"}, document=True)
    try:
        _, revision, _ = _single_tab(result)
        if result["documentId"] != doc:
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ArchiveError("native report has an unsupported structure") from None
    return revision, result


def _export_report(plan: ArchivePlan, drive: DriveStorage, output: Path) -> dict:
    doc = plan.document_id
    assert doc is not None
    report = DocsReport(drive, public_url=plan.destination.origin, artifact_file_id=plan.file_id)
    revision, snapshot = _doc_snapshot(report, doc, plan.artifact)
    body = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    native = _copy(output / "report.json", (body,), limit=_DOC_JSON_LIMIT)
    control = DriveReadControl(time.monotonic() + 60)
    response = drive._request("GET", f"{_API}/{_segment(doc)}/export", params={"mimeType": _DOCX},
                              headers={"Accept-Encoding": "identity"}, control=control)
    try:
        editable = _copy(output / "report.docx", drive._chunks(response, control), limit=_DOCX_BYTES)
    except (requests.RequestException, urllib3.exceptions.HTTPError):
        raise ArchiveError("the native report export stream failed") from None
    finally:
        response.close()
    try:
        with ZipFile(output / "report.docx") as zipped:
            if not {"[Content_Types].xml", "word/document.xml"} <= set(zipped.namelist()):
                raise BadZipFile
    except BadZipFile:
        raise ArchiveError("Google did not return an editable DOCX") from None
    end, _ = _doc_snapshot(report, doc, plan.artifact)
    if end != revision:
        raise ArchiveError("report changed during export; use a new archive of a stable report")
    return {"document_id": doc, "native": native, "editable": editable}


def export_archive(plan: ArchivePlan, drive: DriveStorage, output: Path) -> dict:
    """GET exact committed revisions only. No remote mutation or disk serving fallback."""
    if (drive.folder_id != plan.destination.folder_id or drive._folder_kind != plan.destination.folder_kind
            or not drive.project_layout):
        raise ArchiveError("Drive transport does not match the pinned project layout")
    output.mkdir(mode=0o700)  # do not overwrite an existing archive
    try:
        versions = []
        for version in plan.versions:
            with drive.open_revision(DriveRevision(plan.file_id, version.revision_id),
                                     control=DriveReadControl(time.monotonic() + 60)) as chunks:
                entry = _copy(output / version.filename, chunks, limit=version.size, exact=version.size)
            versions.append({"id": version.version_id, "revision": version.revision_id, **entry})
        report = _export_report(plan, drive, output) if plan.document_id is not None else None
        manifest = {"format": _FORMAT, "artifact": plan.artifact, "file_id": plan.file_id,
                    "destination": asdict(plan.destination), "metadata_sha256": plan.metadata_sha256,
                    "versions": versions, "report": report}
        # A killed process may leave a private directory with incomplete
        # contents. Only a complete manifest is accepted as an archive.
        save_inventory(output / "manifest.json", manifest)
        return manifest
    except BaseException:
        with suppress(OSError):
            shutil.rmtree(output)
        raise


def _entry(value: object, name: str, limit: int, extra: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or set(value) != {"file", "size", "sha256"} | extra:
        raise ArchiveError("invalid archive entry")
    if (value["file"] != name or type(value["size"]) is not int or not 0 <= value["size"] <= limit
            or not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"])):
        raise ArchiveError("invalid archive file name, size, or digest")
    return value


def verify_archive(directory: Path, *, backup: Path | None = None) -> dict:
    """Verify an independent copy. Does not read app config or contact Google."""
    try:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ArchiveError("archive must be a private regular directory")
        with _private_read(directory / "manifest.json") as f:
            raw = f.read(_MANIFEST_BYTES + 1)
        if len(raw) > _MANIFEST_BYTES:
            raise ArchiveError("archive manifest is too large")
        manifest = json.loads(raw, object_pairs_hook=_unique_json_object, parse_constant=_reject_constant)
        if (not isinstance(manifest, dict) or set(manifest) != {
            "format", "artifact", "file_id", "destination", "metadata_sha256", "versions", "report",
        } or manifest["format"] != _FORMAT):
            raise ArchiveError("unsupported or incomplete archive manifest")
        _artifact_id(manifest["artifact"])
        _reference(manifest["file_id"])
        value = manifest["destination"]
        if not isinstance(value, dict) or set(value) != {"folder_id", "folder_kind", "origin", "policy_hash"}:
            raise ArchiveError("invalid archive destination")
        target = DriveDestination(**value)
        DriveFolderConfig(target.folder_id, target.folder_kind)
        if _origin(target.origin) != target.origin or not isinstance(target.policy_hash, str) or not _SHA256.fullmatch(target.policy_hash):
            raise ArchiveError("invalid archive destination")
        if not isinstance(manifest["metadata_sha256"], str) or not _SHA256.fullmatch(manifest["metadata_sha256"]):
            raise ArchiveError("invalid metadata backup digest")
        versions = manifest["versions"]
        if not isinstance(versions, list) or not 1 <= len(versions) <= _MAX_REVISIONS:
            raise ArchiveError("invalid archive version list")
        entries = []
        for value in versions:
            if not isinstance(value, dict):
                raise ArchiveError("invalid archive version")
            version = _artifact_id(value.get("id"))
            _reference(value.get("revision"))
            entries.append(_entry(value, f"version-{version}.bytes", _MAX_ARCHIVE_BYTES, {"id", "revision"}))
        if sum(entry["size"] for entry in entries) > _MAX_ARCHIVE_BYTES:
            raise ArchiveError("archive versions exceed the size limit")
        report = manifest["report"]
        if report is not None:
            if not isinstance(report, dict) or set(report) != {"document_id", "native", "editable"}:
                raise ArchiveError("invalid report entries")
            _reference(report["document_id"])
            entries.extend((_entry(report["native"], "report.json", _DOC_JSON_LIMIT),
                            _entry(report["editable"], "report.docx", _DOCX_BYTES)))
        if backup is not None:
            plan = plan_archive(backup, target, manifest["artifact"], include_report=report is not None)
            if (plan.metadata_sha256 != manifest["metadata_sha256"] or manifest["file_id"] != plan.file_id
                    or [(v["id"], v["revision"], v["size"]) for v in versions]
                    != [(v.version_id, v.revision_id, v.size) for v in plan.versions]
                    or (report is not None and report["document_id"] != plan.document_id)):
                raise ArchiveError("SQLite copy does not match this archive")
        files = {entry["file"] for entry in entries}
        if len(files) != len(entries) or {p.name for p in directory.iterdir()} != files | {"manifest.json"}:
            raise ArchiveError("archive contains unexpected, missing, or duplicate entries")
        for entry in entries:
            with _private_read(directory / entry["file"]) as content:
                if (os.fstat(content.fileno()).st_size != entry["size"]
                        or hashlib.file_digest(content, "sha256").hexdigest() != entry["sha256"]):
                    raise ArchiveError("archive content size or digest does not match")
    except ArchiveError:
        raise
    except (OSError, RecursionError, TypeError, ValueError):
        raise ArchiveError("could not verify the private archive") from None
    return manifest


def extract_version(directory: Path, version_id: str, output: Path) -> None:
    """Restore only the specified exact bytes to a new protected local file."""
    _artifact_id(version_id)
    if output.parent.resolve() == directory.resolve():
        raise ArchiveError("restore to a new file outside the archive")
    manifest = verify_archive(directory)
    entry = next((item for item in manifest["versions"] if item["id"] == version_id), None)
    if entry is None:
        raise ArchiveError("exact version is not present in the archive")
    with _private_read(directory / entry["file"]) as source:
        _copy(output, iter(lambda: source.read(64 * 1024), b""), limit=entry["size"],
              exact=entry["size"], expected_digest=entry["sha256"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    export = commands.add_parser("export", help="read selected data from Workspace Drive")
    export.add_argument("--backup", required=True, type=Path)
    export.add_argument("--artifact", required=True)
    export.add_argument("--subject", required=True, help="the consenting Workspace user's verified Google subject")
    export.add_argument("--include-report", action="store_true")
    export.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify", help="check hashes offline")
    verify.add_argument("--archive", required=True, type=Path)
    verify.add_argument("--backup", type=Path)
    recover = commands.add_parser("extract-version", help="recover exact bytes offline to a new file")
    recover.add_argument("--archive", required=True, type=Path)
    recover.add_argument("--version", required=True)
    recover.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            verify_archive(args.archive, backup=args.backup)
            print("Independent archive digests match. No Google requests made.")
        elif args.action == "extract-version":
            extract_version(args.archive, args.version, args.output)
            print("Exact version restored to a new private file. No Google requests made.")
        else:
            config = Config.from_env()
            if config.drive is None or config.workspace is None:
                raise ArchiveError("Workspace Drive configuration is required")
            expected = DriveDestination.from_config(config.drive, config.workspace, config.public_url)
            plan = plan_archive(args.backup, expected, args.artifact, include_report=args.include_report)
            if os.path.lexists(args.output):
                raise ArchiveError("archive output must be a new directory")
            grants = WorkspaceDriveGrants(config.data_dir, config.workspace)
            provider = WorkspaceDrive(config.workspace, grants, folder_id=expected.folder_id,
                                      folder_kind=expected.folder_kind, max_bytes=config.max_file_bytes)
            with provider.open(args.subject) as drive:
                result = export_archive(plan, drive, args.output)
            print(f"Private archive created: {len(result['versions'])} exact versions; "
                  f"native report {'included' if result['report'] else 'not included'}. No remote writes.")
    except (ArchiveError, InventoryError) as exc:
        print(f"Archive stopped: {exc}.")
        return 1
    except (OSError, DriveError, ValueError):
        print("Archive stopped: configuration, permissions, or content could not be verified.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
