"""An opt-in Drive tree: one managed child folder per project or unfiled scope."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from pathlib import PurePath

from .drive_storage import DriveAccessError, DriveStorage

UNFILED = "unfiled"
UNFILED_NAME = "Без проекта"

SCHEMA = (
    "CREATE TABLE drive_folders (scope TEXT PRIMARY KEY, root_id TEXT NOT NULL, "
    "folder_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, "
    "ready INTEGER NOT NULL CHECK(ready IN (0, 1)))",
    "CREATE TABLE drive_names (artifact_id TEXT PRIMARY KEY, "
    "folder_id TEXT NOT NULL REFERENCES drive_folders(folder_id), "
    "preferred TEXT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL, UNIQUE(folder_id, name_key))",
)


def project_scope(project_id: str | None) -> str:
    return f"p-{project_id}" if project_id else UNFILED


def _key(name: str) -> str:
    return unicodedata.normalize("NFC", name.casefold())


def _label(name: str) -> str:
    text = " ".join(unicodedata.normalize("NFC", name).split())
    text = re.sub(r"[/\\]+", "–", text).strip(" .")
    return text[:160].rstrip() or "Untitled"


def display_name(title: str, filename: str) -> str:
    """Use the readable title and keep the source extension, once."""
    suffix = PurePath(filename).suffix
    stem = title[:-len(suffix)] if suffix and title.casefold().endswith(suffix.casefold()) else title
    return _label(stem) + suffix


def _available_name(preferred: str, stable: str, occupied: Callable[[str], bool], *, blob=False) -> str:
    if not occupied(_key(preferred)):
        return preferred
    extension = PurePath(preferred).suffix if blob else ""
    stem = preferred[:-len(extension)] if extension else preferred
    for length in (8, 12, 22):
        name = f"{stem} · {stable[:length]}{extension}"
        if not occupied(_key(name)):
            return name
    # The desired names are user text, including the suffix. Do not give up
    # uniqueness just because a person chose one of our suggested names.
    for number in range(2, 100):
        name = f"{stem} · {stable} ({number}){extension}"
        if not occupied(_key(name)):
            return name
    raise DriveAccessError("no available Drive display name")


class DriveLayout:
    def __init__(self, connect) -> None:
        self._conn = connect

    def folder(self, drive: DriveStorage, scope: str, preflight) -> str:
        def find(db):
            return db.execute("SELECT root_id, folder_id, ready FROM drive_folders WHERE scope = ?",
                              (scope,)).fetchone()

        with self._conn() as db:
            row = find(db)
        created = False
        if row is None:
            # Reserve an ID remotely, but do not hold the database write lock.
            folder_id = drive.generate_file_id()
            with self._conn(write=True) as db:
                preflight(db)
                row = find(db)
                if row is None:
                    title = UNFILED_NAME if scope == UNFILED else db.execute(
                        "SELECT title FROM visible_projects WHERE id = ?", (scope[2:],),
                    ).fetchone()[0]  # preflight checked the live, owned project

                    def occupied(key: str) -> bool:
                        return bool((scope != UNFILED and key == _key(UNFILED_NAME))
                                    or db.execute("SELECT 1 FROM drive_folders WHERE name_key=?", (key,)).fetchone()
                                    or db.execute("SELECT 1 FROM drive_project_renames WHERE new_name_key=?",
                                                  (key,)).fetchone())

                    label = _available_name(_label(title), scope.removeprefix("p-"), occupied)
                    db.execute("INSERT INTO drive_folders VALUES (?, ?, ?, ?, ?, 0)",
                               (scope, drive.folder_id, folder_id, label, _key(label)))
                    row = (drive.folder_id, folder_id, 0)
                    created = True
        root, folder_id, ready = row
        if root != drive.folder_id:
            raise DriveAccessError("configured Drive root does not match existing project folders")
        if created:
            drive.create_managed_folder(folder_id, label, scope)
        elif not ready:
            # A lost response is resolved only by a verified read. Never
            # repeat CREATE or allocate another folder on a missing response.
            drive.check_managed_folder(folder_id, scope)
        if not ready:
            with self._conn(write=True) as db:
                db.execute("UPDATE drive_folders SET ready = 1 WHERE scope = ? AND folder_id = ?",
                           (scope, folder_id))
        return folder_id

    def reserve_name(self, artifact_id: str, folder_id: str, preferred: str) -> str:
        with self._conn(write=True) as db:
            def occupied(key: str) -> bool:
                return bool(db.execute("SELECT 1 FROM drive_names WHERE folder_id=? AND name_key=?",
                                       (folder_id, key)).fetchone()
                            or db.execute("SELECT 1 FROM drive_renames WHERE folder_id=? AND new_name_key=?",
                                          (folder_id, key)).fetchone()
                            or db.execute("SELECT 1 FROM drive_moves WHERE new_folder=? AND new_name_key=?",
                                          (folder_id, key)).fetchone())

            name = _available_name(preferred, artifact_id, occupied, blob=True)
            db.execute("INSERT INTO drive_names VALUES (?, ?, ?, ?, ?)",
                       (artifact_id, folder_id, preferred, name, _key(name)))
            return name

    def release_unused_name(self, artifact_id: str) -> None:
        with self._conn(write=True) as db:
            db.execute("DELETE FROM drive_names WHERE artifact_id = ? AND NOT EXISTS "
                       "(SELECT 1 FROM drive_attempts WHERE artifact_id = ?) AND NOT EXISTS "
                       "(SELECT 1 FROM drive_refs WHERE artifact_id = ?)", (artifact_id,) * 3)
