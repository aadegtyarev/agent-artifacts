"""Rename a registered project folder without moving any children."""
from __future__ import annotations

import secrets
from dataclasses import astuple, dataclass
from typing import TYPE_CHECKING

from .drive_layout import UNFILED_NAME, _available_name, _key, _label, project_scope
from .drive_rename import verify_name_change
from .drive_storage import DriveStorage
from .store import DriveConflict, Project

if TYPE_CHECKING:
    from .store import Store

SCHEMA = """
CREATE TABLE drive_project_renames (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE RESTRICT,
    operation_id TEXT NOT NULL UNIQUE,
    root_id TEXT NOT NULL,
    folder_id TEXT NOT NULL UNIQUE,
    old_title TEXT NOT NULL,
    new_title TEXT NOT NULL,
    old_name TEXT NOT NULL,
    new_name TEXT NOT NULL,
    new_name_key TEXT NOT NULL UNIQUE,
    sent INTEGER NOT NULL DEFAULT 0 CHECK (sent IN (0, 1))
);
"""
_COLUMNS = "project_id,operation_id,root_id,folder_id,old_title,new_title,old_name,new_name,new_name_key,sent"


@dataclass(frozen=True)
class ProjectRename:
    project_id: str
    operation_id: str
    root_id: str
    folder_id: str
    old_title: str
    new_title: str
    old_name: str
    new_name: str
    new_name_key: str
    sent: int = 0


class DriveProjectRenames:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _active(db, project_id: str) -> ProjectRename | None:
        row = db.execute(f"SELECT {_COLUMNS} FROM drive_project_renames WHERE project_id=?", (project_id,)).fetchone()
        return ProjectRename(*row) if row else None

    @staticmethod
    def _folder(db, project_id: str) -> tuple | None:
        return db.execute("SELECT root_id,folder_id,name,ready FROM drive_folders WHERE scope=?",
                          (project_scope(project_id),)).fetchone()

    @staticmethod
    def _has_drive_file(db, project_id: str) -> bool:
        # A folder allocated during an unresolved initial upload is not a
        # populated project and cannot be adopted for a rename.
        return db.execute("SELECT 1 FROM visible_artifacts a JOIN drive_refs r ON a.id=r.artifact_id "
                          "WHERE a.project=? LIMIT 1", (project_id,)).fetchone() is not None

    def _title(self, db, project_id: str, owner: str, title: str) -> Project:
        db.execute("UPDATE projects SET title=? WHERE id=? AND owner=?", (title, project_id, owner))
        project = self.store._project(db, project_id, owner)
        if project is None:
            raise DriveConflict("project no longer available")
        return project

    def prepare(self, project_id: str, owner: str, title: str) -> ProjectRename | Project | None:
        with self.store._conn(write=True) as db:
            project = self.store._project(db, project_id, owner)
            if project is None:
                return None
            active = self._active(db, project_id)
            if active is not None:
                if active.new_title != title:
                    raise DriveConflict("another Drive project rename is unresolved")
                return self._check(db, active, owner)
            folder = self._folder(db, project_id)
            if folder is None or title == project.title:
                return self._title(db, project_id, owner, title)
            root_id, folder_id, old_name, ready = folder
            if not ready or not self._has_drive_file(db, project_id):
                raise DriveConflict("Drive folder creation or initial upload is unresolved")
            preferred = _label(title)
            if preferred == _label(project.title):
                return self._title(db, project_id, owner, title)

            def occupied(key: str) -> bool:
                return (key == _key(UNFILED_NAME)
                        or db.execute("SELECT 1 FROM drive_folders WHERE name_key=? AND folder_id!=?",
                                      (key, folder_id)).fetchone() is not None
                        or db.execute("SELECT 1 FROM drive_project_renames WHERE new_name_key=?", (key,)).fetchone() is not None)

            new_name = _available_name(preferred, project_id, occupied)
            if new_name == old_name:
                return self._title(db, project_id, owner, title)
            operation = ProjectRename(project_id, secrets.token_urlsafe(16), root_id, folder_id,
                                      project.title, title, old_name, new_name, _key(new_name))
            db.execute(f"INSERT INTO drive_project_renames ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       astuple(operation))
            return operation

    def _check(self, db, operation: ProjectRename, owner: str) -> ProjectRename:
        project = self.store._project(db, operation.project_id, owner)
        active = self._active(db, operation.project_id)
        expected = (operation.root_id, operation.folder_id, operation.old_name, 1)
        if (project is None or project.title != operation.old_title
                or active is None or astuple(active)[:-1] != astuple(operation)[:-1]
                or self._folder(db, operation.project_id) != expected or not self._has_drive_file(db, operation.project_id)):
            raise DriveConflict("Drive project rename no longer matches stored metadata")
        return active

    def discard_unsent(self, operation: ProjectRename) -> None:
        with self.store._conn(write=True) as db:
            db.execute("DELETE FROM drive_project_renames WHERE operation_id=? AND sent=0", (operation.operation_id,))

    def execute(self, drive: DriveStorage, operation: ProjectRename, owner: str) -> Project:
        if drive.folder_id != operation.root_id:
            raise DriveConflict("Drive project rename has a different root")

        def observe() -> dict:
            location = drive.validate_folder(write=False)
            data = drive._metadata(operation.folder_id)
            drive._validate_child(data, operation.folder_id, location, scope=project_scope(operation.project_id))
            return data

        with self.store._conn() as db:
            active = self._check(db, operation, owner)

        def claim() -> None:
            with self.store._conn(write=True) as db:
                if self._check(db, operation, owner).sent:
                    raise DriveConflict("Drive project rename already dispatched")
                db.execute("UPDATE drive_project_renames SET sent=1 WHERE operation_id=?", (operation.operation_id,))

        verify_name_change(drive, file_id=operation.folder_id, operation_id=operation.operation_id,
                           old_name=operation.old_name, new_name=operation.new_name, sent=bool(active.sent),
                           observe=observe, claim=claim)
        with self.store._conn(write=True) as db:
            if not self._check(db, operation, owner).sent:
                raise DriveConflict("Drive project rename has no durable dispatch claim")
            db.execute("UPDATE drive_folders SET name=?,name_key=? WHERE scope=?",
                       (operation.new_name, operation.new_name_key, project_scope(operation.project_id)))
            project = self._title(db, operation.project_id, owner, operation.new_title)
            db.execute("DELETE FROM drive_project_renames WHERE operation_id=?", (operation.operation_id,))
            return project
