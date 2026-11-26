"""One guarded Drive title change; a possibly sent rename is reconciled read-only."""
from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import astuple, dataclass, replace
from typing import TYPE_CHECKING

from .drive_layout import _available_name, _key, display_name, project_scope
from .drive_storage import DriveAccessError, DriveStorage, _API, _segment
from .store import Artifact, DriveConflict

if TYPE_CHECKING:
    from .store import Store

SCHEMA = """
CREATE TABLE drive_renames (
    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON DELETE RESTRICT,
    operation_id TEXT NOT NULL UNIQUE,
    file_id TEXT NOT NULL UNIQUE,
    revision_id TEXT NOT NULL,
    folder_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    old_title TEXT NOT NULL,
    new_title TEXT NOT NULL,
    old_name TEXT NOT NULL,
    new_name TEXT NOT NULL,
    new_name_key TEXT NOT NULL,
    preferred TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0 CHECK (sent IN (0, 1)),
    UNIQUE (folder_id, new_name_key)
);
"""
_MARKER = "agentArtifactsRename"
_COLUMN_LIST = ("artifact_id,operation_id,file_id,revision_id,folder_id,scope,old_title,new_title,"
                "old_name,new_name,new_name_key,preferred,sent")


@dataclass(frozen=True)
class Rename:
    artifact_id: str
    operation_id: str
    file_id: str
    revision_id: str
    folder_id: str
    scope: str
    old_title: str
    new_title: str
    old_name: str
    new_name: str
    new_name_key: str
    preferred: str
    sent: int = 0


def verify_name_change(drive: DriveStorage, *, file_id: str, operation_id: str, old_name: str,
                       new_name: str, sent: bool, observe: Callable[[], dict], claim: Callable[[], None]) -> None:
    """Metadata only. The caller's durable claim runs immediately before PATCH."""
    found = observe()
    if not sent:
        if found.get("name") != old_name:
            raise DriveConflict("Drive name changed outside the app")
        props, caps = found.get("appProperties", {}), found.get("capabilities", {})
        if not isinstance(props, dict) or not isinstance(caps, dict) or caps.get("canRename") is not True:
            raise DriveAccessError("this user cannot rename the object in Google Drive")
        claim()
        response = drive._request("PATCH", f"{_API}/{_segment(file_id)}",
                                  params={"supportsAllDrives": "true", "fields": "id"},
                                  json={"name": new_name, "appProperties": props | {_MARKER: operation_id}})
        response.close()
        found = observe()
    props = found.get("appProperties")
    if (found.get("name") != new_name or not isinstance(props, dict) or props.get(_MARKER) != operation_id):
        raise DriveConflict("Drive rename is not verified; retry only the same title")


class DriveRenames:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _active(db, artifact_id: str) -> Rename | None:
        row = db.execute(f"SELECT {_COLUMN_LIST} FROM drive_renames WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return Rename(*row) if row else None

    @staticmethod
    def _blob(db, artifact_id: str) -> tuple | None:
        return db.execute(
            "SELECT r.file_id,r.revision_id,n.folder_id,n.name,n.preferred,f.scope,f.ready "
            "FROM drive_refs r JOIN drive_names n ON n.artifact_id=r.artifact_id "
            "JOIN drive_folders f ON f.folder_id=n.folder_id WHERE r.artifact_id=?",
            (artifact_id,),
        ).fetchone()

    @staticmethod
    def _with_version(db, artifact: Artifact | None) -> Artifact:
        if artifact is None:
            raise DriveConflict("artifact no longer available")
        row = db.execute(
            "SELECT v.version_id FROM drive_versions v JOIN drive_refs r ON r.artifact_id=v.artifact_id "
            "AND r.file_id=v.file_id AND r.revision_id=v.revision_id WHERE v.artifact_id=? "
            "ORDER BY v.rowid DESC LIMIT 1", (artifact.id,),
        ).fetchone()
        if row is None:
            raise DriveConflict("artifact has no committed exact version")
        return replace(artifact, version_id=row[0])

    @staticmethod
    def _other_write(db, artifact_id: str, file_id: str) -> bool:
        return bool(db.execute("SELECT 1 FROM drive_attempts WHERE artifact_id=? OR file_id=?",
                               (artifact_id, file_id)).fetchone()
                    or db.execute("SELECT 1 FROM review_operations WHERE artifact_id=? AND confirmed_at IS NULL",
                                  (artifact_id,)).fetchone())

    def prepare(self, artifact_id: str, owner: str, title: str) -> Rename | Artifact | None:
        with self.store._conn(write=True) as db:
            artifact = self.store._artifact(db, artifact_id, owner)
            if artifact is None:
                return None
            if db.execute("SELECT 1 FROM drive_moves WHERE artifact_id=?", (artifact_id,)).fetchone():
                raise DriveConflict("Drive move is unresolved")
            active = self._active(db, artifact_id)
            if active is not None:
                if active.new_title != title:
                    raise DriveConflict("another Drive rename is unresolved")
                self._check(db, active, owner)
                return active
            artifact = self._with_version(db, artifact)
            if artifact.title == title:
                return artifact
            blob = self._blob(db, artifact_id)
            if blob is None or not blob[-1] or blob[-2] != project_scope(artifact.project):
                raise DriveConflict("Drive file is not in a registered managed folder")
            file_id, revision, folder, name, old_preferred, scope, _ = blob
            if self._other_write(db, artifact_id, file_id):
                raise DriveConflict("another Drive or report write is unresolved")
            preferred = display_name(title, artifact.name)
            if preferred == old_preferred:
                db.execute("UPDATE artifacts SET title = ? WHERE id = ?", (title, artifact_id))
                return self._with_version(db, self.store._artifact(db, artifact_id, owner))

            def occupied(key: str) -> bool:
                return bool(db.execute("SELECT 1 FROM drive_names WHERE folder_id=? AND name_key=? AND artifact_id!=?",
                                       (folder, key, artifact_id)).fetchone()
                            or db.execute("SELECT 1 FROM drive_renames WHERE folder_id=? AND new_name_key=?",
                                          (folder, key)).fetchone()
                            or db.execute("SELECT 1 FROM drive_moves WHERE new_folder=? AND new_name_key=?",
                                          (folder, key)).fetchone())

            chosen = _available_name(preferred, artifact_id, occupied, blob=True)
            if chosen == name:
                # The title now resolves to the same already allocated name.
                db.execute("UPDATE artifacts SET title = ? WHERE id = ?", (title, artifact_id))
                db.execute("UPDATE drive_names SET preferred = ? WHERE artifact_id = ?", (preferred, artifact_id))
                return self._with_version(db, self.store._artifact(db, artifact_id, owner))
            operation = Rename(artifact_id, secrets.token_urlsafe(16), file_id, revision, folder, scope,
                               artifact.title, title, name, chosen, _key(chosen), preferred)
            db.execute(f"INSERT INTO drive_renames ({_COLUMN_LIST}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       astuple(operation))
            return operation

    def _check(self, db, operation: Rename, owner: str) -> Rename:
        artifact = self.store._artifact(db, operation.artifact_id, owner)
        active = self._active(db, operation.artifact_id)
        blob = self._blob(db, operation.artifact_id)
        if (artifact is None or artifact.title != operation.old_title
                or active is None or astuple(active)[:-1] != astuple(operation)[:-1]
                or blob is None or blob[:4] != (operation.file_id, operation.revision_id,
                                                operation.folder_id, operation.old_name)
                or blob[-2:] != (operation.scope, 1) or project_scope(artifact.project) != operation.scope
                or self._other_write(db, operation.artifact_id, operation.file_id)):
            raise DriveConflict("Drive rename no longer matches current metadata")
        self._with_version(db, artifact)
        return active

    def discard_unsent(self, operation: Rename) -> None:
        # A concurrent dispatcher either already marked the row sent (do not
        # delete), or will fail its claim before contacting Google.
        with self.store._conn(write=True) as db:
            db.execute("DELETE FROM drive_renames WHERE artifact_id=? AND operation_id=? AND sent=0",
                       (operation.artifact_id, operation.operation_id))

    def execute(self, drive: DriveStorage, operation: Rename, owner: str) -> Artifact:
        def observe() -> dict:
            location = drive.validate_folder(write=False)
            data = drive._validate_file(operation.file_id, location, edit=False,
                                        expected_parent=(operation.folder_id, operation.scope))
            if data.get("headRevisionId") != operation.revision_id:
                raise DriveConflict("Drive revision changed during rename")
            return data

        with self.store._conn() as db:
            active = self._check(db, operation, owner)
        def claim() -> None:
            with self.store._conn(write=True) as db:
                if self._check(db, operation, owner).sent:
                    raise DriveConflict("Drive rename already dispatched")
                db.execute("UPDATE drive_renames SET sent=1 WHERE operation_id=?", (operation.operation_id,))

        verify_name_change(drive, file_id=operation.file_id, operation_id=operation.operation_id,
                           old_name=operation.old_name, new_name=operation.new_name, sent=bool(active.sent),
                           observe=observe, claim=claim)
        with self.store._conn(write=True) as db:
            if not self._check(db, operation, owner).sent:
                raise DriveConflict("Drive rename has no durable dispatch claim")
            db.execute("UPDATE drive_names SET name=?,name_key=?,preferred=? WHERE artifact_id=?",
                       (operation.new_name, operation.new_name_key, operation.preferred, operation.artifact_id))
            db.execute("UPDATE artifacts SET title=? WHERE id=?", (operation.new_title, operation.artifact_id))
            db.execute("DELETE FROM drive_renames WHERE operation_id=?", (operation.operation_id,))
            return self._with_version(db, self.store._artifact(db, operation.artifact_id, owner))
