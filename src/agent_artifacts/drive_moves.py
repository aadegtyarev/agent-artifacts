"""Durable coordination for reparenting a managed Drive blob and its review Doc."""
from __future__ import annotations

import json
import secrets
from dataclasses import astuple, dataclass
from typing import TYPE_CHECKING

from .drive_layout import DriveLayout, _available_name, _key, display_name, project_scope
from .drive_rename import DriveRenames
from .drive_reparent import MoveFolder, MovingObject, verify_reparent
from .drive_storage import DriveStorage
from .store import Artifact, DriveConflict

if TYPE_CHECKING:
    from .store import Store

SCHEMA = """
CREATE TABLE drive_moves (
    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON DELETE RESTRICT,
    operation_id TEXT NOT NULL UNIQUE,
    file_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    doc_id TEXT,
    root_id TEXT NOT NULL,
    old_project TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    new_project TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    old_folder TEXT NOT NULL REFERENCES drive_folders(folder_id),
    new_folder TEXT NOT NULL REFERENCES drive_folders(folder_id),
    old_name TEXT NOT NULL,
    new_name TEXT NOT NULL,
    new_name_key TEXT NOT NULL,
    preferred TEXT NOT NULL,
    section TEXT,
    request TEXT NOT NULL,
    blob_sent INTEGER NOT NULL DEFAULT 0 CHECK (blob_sent IN (0, 1)),
    doc_sent INTEGER NOT NULL DEFAULT 0 CHECK (doc_sent IN (0, 1)),
    UNIQUE(new_folder,new_name_key)
);
CREATE INDEX drive_moves_new_project ON drive_moves(new_project);
"""
_COLUMNS = ("artifact_id,operation_id,file_id,revision_id,doc_id,root_id,old_project,new_project,"
            "old_folder,new_folder,old_name,new_name,new_name_key,preferred,section,request,blob_sent,doc_sent")


@dataclass(frozen=True)
class Move:
    artifact_id: str
    operation_id: str
    file_id: str
    revision_id: str
    doc_id: str | None
    root_id: str
    old_project: str | None
    new_project: str | None
    old_folder: str
    new_folder: str
    old_name: str
    new_name: str
    new_name_key: str
    preferred: str
    section: str | None
    request: str
    blob_sent: int = 0
    doc_sent: int = 0


class DriveMoves:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _active(db, artifact_id: str) -> Move | None:
        row = db.execute(f"SELECT {_COLUMNS} FROM drive_moves WHERE artifact_id=?",
                         (artifact_id,)).fetchone()
        return Move(*row) if row else None

    @staticmethod
    def _check_no_rename(db, artifact_id: str, old: str | None, new: str | None) -> None:
        if (db.execute("SELECT 1 FROM drive_renames WHERE artifact_id=?", (artifact_id,)).fetchone()
                or db.execute("SELECT 1 FROM drive_project_renames WHERE project_id IN (?,?)",
                              (old, new)).fetchone()):
            raise DriveConflict("a file or project rename is unresolved")

    def _source(self, db, artifact_id: str, owner: str, target: str | None, *, pending=False) -> tuple:
        # Only an already-reserved move can reconcile past the unfiled TTL.
        # Browser URLs and unrelated owner operations still honor expiry.
        a = self.store._artifact(db, artifact_id, owner, now=float("-inf") if pending else None)
        if a is None:
            raise DriveConflict("artifact no longer available for the prepared move")
        self._check_no_rename(db, artifact_id, a.project, target)
        blob = DriveRenames._blob(db, artifact_id)
        if blob is None or blob[-2:] != (project_scope(a.project), 1):
            raise DriveConflict("file is not in a registered managed folder")
        if DriveRenames._other_write(db, artifact_id, blob[0]):
            raise DriveConflict("a content or report write is unresolved")
        if target is None and db.execute("SELECT 1 FROM review_submissions WHERE artifact_id=?",
                                         (artifact_id,)).fetchone():
            raise DriveConflict("review evidence must stay in a project")
        doc = db.execute("SELECT file_id,doc_id FROM review_documents WHERE artifact_id=?",
                         (artifact_id,)).fetchone()
        if doc is not None and (doc[0] != blob[0] or not doc[1] or target is None):
            raise DriveConflict("a verified review document must stay in a project")
        return a, blob, doc[1] if doc is not None else None

    def _placement(self, db, a: Artifact, changes: dict, now: float) -> tuple:
        # A true project change stamps a new placement; the old stamp is unused.
        return self.store._placed(db, a.owner, a.id, (a.project, a.section, None), changes, now)

    @staticmethod
    def _folder_in(db, project: str | None, folder: str, root: str) -> bool:
        return db.execute("SELECT 1 FROM drive_folders WHERE scope=? AND folder_id=? AND root_id=? AND ready=1",
                          (project_scope(project), folder, root)).fetchone() is not None

    def _validate(self, db, move: Move, owner: str) -> tuple[Move, Artifact, tuple]:
        active = self._active(db, move.artifact_id)
        if active is None or astuple(active)[:-2] != astuple(move)[:-2]:
            raise DriveConflict("move ticket changed")
        a, blob, doc = self._source(db, move.artifact_id, owner, move.new_project, pending=True)
        place = self._placement(db, a, json.loads(move.request), self.store.clock())
        if (a.project != move.old_project or doc != move.doc_id or place[:2] != (move.new_project, move.section)
                or blob[:4] != (move.file_id, move.revision_id, move.old_folder, move.old_name)
                or not self._folder_in(db, a.project, move.old_folder, move.root_id)
                or not self._folder_in(db, move.new_project, move.new_folder, move.root_id)):
            raise DriveConflict("managed move no longer matches the current metadata")
        return active, a, place

    def _prepare(self, drive: DriveStorage, artifact: str, owner: str, changes: dict) -> Move | Artifact | None:
        request = json.dumps(changes, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        target = changes["project"]
        with self.store._conn() as db:
            existing = self._active(db, artifact)
            if existing is not None:
                if self.store._artifact(db, artifact, owner, now=float("-inf")) is None:
                    return None
                if existing.request != request:
                    raise DriveConflict("retry the identical move while its placement is unresolved")
                return self._validate(db, existing, owner)[0]
            a = self.store._artifact(db, artifact, owner)
            if a is None:
                return None
            if a.project == target:
                return DriveRenames._with_version(db, a) if (
                    ("section" not in changes or changes["section"] == a.section)
                    and ("title" not in changes or changes["title"] == a.title)
                ) else None
            a, blob, doc = self._source(db, artifact, owner, target)
            self._placement(db, a, changes, self.store.clock())
            expected = (a.project, a.section, a.title), blob, doc
        if drive.validate_folder(write=False) is not None:
            raise DriveConflict("moving on Shared Drive is not enabled")

        def preflight(db):
            found, current, bound_doc = self._source(db, artifact, owner, target)
            if ((found.project, found.section, found.title), current, bound_doc) != expected:
                raise DriveConflict("file placement changed before preparing the move")
            self._placement(db, found, changes, self.store.clock())

        layout = DriveLayout(self.store._conn)
        new_folder = layout.folder(drive, project_scope(target), preflight)
        with self.store._conn(write=True) as db:
            existing = self._active(db, artifact)
            if existing is not None:
                if existing.request != request:
                    raise DriveConflict("another move is unresolved")
                return self._validate(db, existing, owner)[0]
            preflight(db)
            placed = self._placement(db, a, changes, self.store.clock())
            file_id, revision, old_folder, old_name = blob[:4]
            if (not self._folder_in(db, a.project, old_folder, drive.folder_id)
                    or not self._folder_in(db, target, new_folder, drive.folder_id)):
                raise DriveConflict("source and destination do not match the configured root")
            preferred = display_name(changes.get("title", a.title), a.name)

            def occupied(key: str) -> bool:
                bindings = (new_folder, key)
                return bool(db.execute("SELECT 1 FROM drive_names WHERE folder_id=? AND name_key=?", bindings).fetchone()
                            or db.execute("SELECT 1 FROM drive_renames WHERE folder_id=? AND new_name_key=?", bindings).fetchone()
                            or db.execute("SELECT 1 FROM drive_moves WHERE new_folder=? AND new_name_key=?", bindings).fetchone())

            chosen = _available_name(preferred, artifact, occupied, blob=True)
            move = Move(artifact, secrets.token_urlsafe(16), file_id, revision, doc, drive.folder_id,
                        a.project, target, old_folder, new_folder, old_name, chosen, _key(chosen),
                        preferred, placed[1], request)
            db.execute(f"INSERT INTO drive_moves ({_COLUMNS}) VALUES ({','.join('?' * 18)})", astuple(move))
        return move

    def move(self, drive: DriveStorage, artifact: str, owner: str, changes: dict) -> Artifact | None:
        move = self._prepare(drive, artifact, owner, changes)
        if not isinstance(move, Move):
            return move
        try:
            return self._execute(drive, move, owner)
        finally:
            # Even a rejected request may have modified one object. Delete
            # only a ticket no caller ever claimed for a remote call.
            with self.store._conn(write=True) as db:
                db.execute("DELETE FROM drive_moves WHERE operation_id=? AND blob_sent=0 AND doc_sent=0",
                           (move.operation_id,))

    def _execute(self, drive: DriveStorage, move: Move, owner: str) -> Artifact:
        if drive.folder_id != move.root_id:
            raise DriveConflict("move ticket uses a different Drive root")
        old = MoveFolder(move.old_folder, project_scope(move.old_project))
        new = MoveFolder(move.new_folder, project_scope(move.new_project))
        blob = MovingObject(move.file_id, move.revision_id, move.old_name, move.new_name)
        doc = MovingObject(move.doc_id, review_artifact=move.artifact_id) if move.doc_id else None

        def run(obj: MovingObject, *, report: bool, check_only: bool = False):
            with self.store._conn() as db:
                active, _, _ = self._validate(db, move, owner)
                sent = bool(active.doc_sent if report else active.blob_sent)

            def claim():
                with self.store._conn(write=True) as db:
                    active, _, _ = self._validate(db, move, owner)
                    if (active.doc_sent if report else active.blob_sent):
                        raise DriveConflict("object move already dispatched")
                    sql = ("UPDATE drive_moves SET doc_sent=1 WHERE operation_id=?" if report
                           else "UPDATE drive_moves SET blob_sent=1 WHERE operation_id=?")
                    db.execute(sql, (move.operation_id,))

            verify_reparent(drive, obj, operation=move.operation_id, old=old, new=new,
                            sent=sent, claim=claim, check_only=check_only)

        if doc is not None:
            run(doc, report=True, check_only=True)
        run(blob, report=False)
        if doc is not None:
            run(doc, report=True)
            # The blob could have left the target while the Doc was moving.
            run(blob, report=False)
        with self.store._conn(write=True) as db:
            active, a, placed = self._validate(db, move, owner)
            if not active.blob_sent or (doc is not None and not active.doc_sent):
                raise DriveConflict("move has no durable dispatch claim")
            expires = None if move.new_project is not None else self.store.clock() + self.store.settings(db)["ttl_seconds"]
            title = json.loads(move.request).get("title", a.title)
            db.execute("UPDATE drive_names SET folder_id=?,name=?,name_key=?,preferred=? WHERE artifact_id=?",
                       (move.new_folder, move.new_name, move.new_name_key, move.preferred, move.artifact_id))
            db.execute("UPDATE artifacts SET title=?,expires_at=?,project=?,section=?,added_at=? WHERE id=?",
                       (title, expires, *placed, move.artifact_id))
            db.execute("DELETE FROM drive_moves WHERE operation_id=?", (move.operation_id,))
            return DriveRenames._with_version(db, self.store._artifact(db, move.artifact_id, owner))
