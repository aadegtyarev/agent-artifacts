"""Narrow provider primitive for a journaled move. No bytes, copies or ACL writes."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .docs_report import _MARKER as _REPORT_MARKER, _MIME as _DOC
from .drive_storage import _API, _FIELDS, DriveAccessError, DriveStorage, _reference, _segment
from .store import DriveConflict

_MOVE_MARK = "agentArtifactsMove"
_FOLDER_FIELDS = _FIELDS + ",ownedByMe,inheritedPermissionsDisabled"
_ITEM_FIELDS = _FIELDS.replace("canRename)", "canRename,canMoveItemWithinDrive)")
_PERMISSION_FIELDS = (
    "nextPageToken,permissions(id,type,role,expirationTime,allowFileDiscovery,deleted,view)"
)


@dataclass(frozen=True)
class MovingObject:
    file_id: str
    # Blob metadata, or the managed Review Doc's artifact tag (not both).
    revision_id: str | None = None
    old_name: str | None = None
    new_name: str | None = None
    review_artifact: str | None = None


@dataclass(frozen=True)
class MoveFolder:
    file_id: str
    scope: str


def _metadata(drive: DriveStorage, file_id: str, fields: str) -> tuple[dict, str | None]:
    response = drive._request("GET", f"{_API}/{_segment(file_id)}", params={
        "supportsAllDrives": "true", "fields": fields,
    }, headers={"Accept-Encoding": "identity"})
    try:
        etag = getattr(response, "headers", {}).get("ETag")
        return drive._json(response), etag
    finally:
        response.close()


def _permissions(drive: DriveStorage, folder: str) -> frozenset[tuple]:
    response = drive._request("GET", f"{_API}/{_segment(folder)}/permissions", params={
        "supportsAllDrives": "true", "pageSize": 100, "fields": _PERMISSION_FIELDS,
    })
    try:
        data = drive._json(response)
    finally:
        response.close()
    rows = data.get("permissions")
    if not isinstance(rows, list) or not rows or data.get("nextPageToken"):
        raise DriveAccessError("could not verify complete managed folder permissions")
    result = set()
    ids = set()
    fields = ("id", "type", "role", "expirationTime", "allowFileDiscovery", "deleted", "view")
    for row in rows:
        if (not isinstance(row, dict) or not set(row) <= set(fields)
                or not all(isinstance(row.get(field), str) and row[field] for field in fields[:3])
                or row["id"] in ids
                or not all(isinstance(row[x], bool) for x in ("allowFileDiscovery", "deleted") if x in row)
                or not all(isinstance(row[x], str) for x in ("expirationTime", "view") if x in row)):
            raise DriveAccessError("unsupported managed folder permission")
        ids.add(row["id"])
        result.add(tuple(row.get(field) for field in fields))
    if len([item for item in result if item[1:3] == ("user", "owner")]) != 1:
        raise DriveAccessError("managed folder permissions have no verified owner")
    return frozenset(result)


def _same_access(drive: DriveStorage, old: MoveFolder, new: MoveFolder) -> None:
    # Start with ordinary My Drive and the owning grant. Do not assume a
    # Shared Drive member sees every inherited or limited-access permission.
    if (not drive.project_layout or old.file_id == new.file_id or old.scope == new.scope
            or drive.validate_folder(write=False) is not None):
        raise DriveAccessError("move requires distinct managed folders in the approved My Drive root")
    for folder, write in ((old, False), (new, True)):
        data, _ = _metadata(drive, folder.file_id, _FOLDER_FIELDS)
        drive._validate_child(data, folder.file_id, None, scope=folder.scope, write=write)
        if data.get("ownedByMe") is not True or data.get("inheritedPermissionsDisabled", False) is not False:
            raise DriveAccessError("cannot verify folder permissions with the linked Drive grant")
    if _permissions(drive, old.file_id) != _permissions(drive, new.file_id):
        raise DriveAccessError("managed folder permissions differ")


def verify_reparent(drive: DriveStorage, item: MovingObject, *, operation: str, old: MoveFolder,
                    new: MoveFolder, sent: bool, claim: Callable[[], None], check_only: bool = False) -> None:
    """With a durable caller claim, send at most one metadata PATCH per object.

    Reject other placements; an earlier response or a timeout is not proof.
    Folder ACL observations and remote mutations are not atomic. Recheck them
    before committing placement in the caller, and never modify permissions.
    """
    _reference(operation)
    report = item.review_artifact is not None
    if ((report and any(x is not None for x in (item.revision_id, item.old_name, item.new_name)))
            or (not report and not all(isinstance(x, str) and x for x in
                                       (item.revision_id, item.old_name, item.new_name)))):
        raise ValueError("invalid kind of managed move object")

    def observe(*, after: bool) -> str | None:
        data, etag = _metadata(drive, item.file_id, _ITEM_FIELDS)
        props, caps, kind = data.get("appProperties", {}), data.get("capabilities"), data.get("mimeType")
        if (data.get("id") != item.file_id or data.get("driveId") is not None or data.get("trashed") is not False
                or data.get("parents") != [(new if after else old).file_id]
                or not isinstance(kind, str) or not isinstance(props, dict)
                or (report and (kind != _DOC or props.get(_REPORT_MARKER) != item.review_artifact))
                or (not report and (kind.startswith("application/vnd.google-apps.")
                                    or data.get("headRevisionId") != item.revision_id
                                    or data.get("name") != (item.new_name if after else item.old_name)))
                or ((props.get(_MOVE_MARK) == operation) != after)):
            raise DriveConflict("known object does not match its expected move state")
        if not after and (not isinstance(caps, dict) or caps.get("canMoveItemWithinDrive") is not True
                          or (not report and caps.get("canRename") is not True)):
            raise DriveAccessError("the linked Drive grant cannot move this object")
        return etag

    _same_access(drive, old, new)
    if sent:
        observe(after=True)
        return
    etag = observe(after=False)
    if check_only:
        return
    payload = {"appProperties": {_MOVE_MARK: operation}}
    if not report:
        payload["name"] = item.new_name
    claim()
    response = drive._request("PATCH", f"{_API}/{_segment(item.file_id)}", params={
        "supportsAllDrives": "true", "addParents": _reference(new.file_id),
        "removeParents": _reference(old.file_id), "fields": "id",
    }, json=payload, headers={"If-Match": etag} if etag else {})
    response.close()
    _same_access(drive, old, new)
    observe(after=True)
