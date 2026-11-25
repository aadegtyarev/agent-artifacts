"""Reversible account deletion on the same SQLite transaction as ownership.

Rows and bytes stay put until cleanup. Normal reads use the visibility views;
physical storage accounting and repair still see the underlying tables.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal

UNDO_SECONDS = 60
Kind = Literal["file", "project"]

SCHEMA = """
CREATE TABLE account_deletions (
    id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL REFERENCES human_accounts(id),
    artifact_id TEXT UNIQUE REFERENCES artifacts(id) ON DELETE CASCADE,
    project_id TEXT UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    files INTEGER NOT NULL,
    marked_at REAL NOT NULL,
    deadline REAL NOT NULL,
    CHECK ((artifact_id IS NULL) != (project_id IS NULL))
);
CREATE INDEX account_deletions_owner ON account_deletions(account_id, deadline);
CREATE INDEX account_deletions_deadline ON account_deletions(deadline);
CREATE VIEW visible_projects AS
    SELECT p.* FROM projects p WHERE NOT EXISTS (
        SELECT 1 FROM account_deletions d WHERE d.project_id = p.id
    );
CREATE VIEW visible_artifacts AS
    SELECT a.* FROM artifacts a WHERE NOT EXISTS (
        SELECT 1 FROM account_deletions d WHERE d.artifact_id = a.id
    ) AND NOT EXISTS (
        SELECT 1 FROM account_deletions d WHERE d.project_id = a.project
    );
"""


@dataclass(frozen=True)
class Scope:
    title: str
    count: int
    fingerprint: str


@dataclass(frozen=True)
class DeleteResult:
    status: Literal["missing", "confirm", "changed", "deleted"]
    scope: Scope | None = None
    deletion_id: str | None = None


@dataclass(frozen=True)
class PendingDeletion:
    id: str
    kind: Kind
    title: str
    files: int
    deadline: float
    can_undo: bool


class Trash:
    def __init__(self, connect: Callable[..., AbstractContextManager[sqlite3.Connection]],
                 clock: Callable[[], float],
                 account_for: Callable[[sqlite3.Connection, str | None, str | None], str | None],
                 draft_ttl: int) -> None:
        self._conn, self._clock, self._account_for, self._draft_ttl = connect, clock, account_for, draft_ttl

    def _scope(self, db: sqlite3.Connection, account_id: str, kind: Kind,
               target: str, now: float) -> Scope | None:
        if kind == "project":
            project = db.execute(
                "SELECT p.title FROM visible_projects p JOIN account_agents own ON own.agent = p.owner "
                "WHERE p.id = ? AND own.account_id = ?", (target, account_id),
            ).fetchone()
            if project is None:
                return None
            files = db.execute("SELECT id FROM visible_artifacts WHERE project = ? ORDER BY id", (target,)).fetchall()
            # Hold consent to the set that the confirmation covered. A new
            # file taking an old one's place is still a change of scope.
            stamp = hashlib.sha256(project[0].encode() + b"\0" + b"\0".join(r[0].encode() for r in files))
            return Scope(project[0], len(files), stamp.hexdigest())
        if kind != "file":
            return None
        file = db.execute(
            "SELECT COALESCE(a.title, a.name) FROM visible_artifacts a JOIN account_agents own ON own.agent = a.owner "
            "WHERE a.id = ? AND own.account_id = ? AND (a.expires_at IS NULL OR a.expires_at > ?)",
            (target, account_id, now),
        ).fetchone()
        return Scope(file[0], 1, "") if file else None

    def delete(self, kind: Kind, target: str, *, fingerprint: str | None = None,
               device_token: str | None = None, google_subject: str | None = None) -> DeleteResult:
        with self._conn(write=True) as db:
            account_id = self._account_for(db, device_token, google_subject)
            if account_id is None:
                return DeleteResult("missing")
            now = self._clock()
            scope = self._scope(db, account_id, kind, target, now)
            if not scope:
                return DeleteResult("missing")
            if kind == "project" and fingerprint != scope.fingerprint:
                return DeleteResult("confirm" if fingerprint is None else "changed", scope)
            deletion_id = secrets.token_urlsafe(16)
            db.execute(
                "INSERT INTO account_deletions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (deletion_id, account_id, target if kind == "file" else None,
                 target if kind == "project" else None, scope.title, scope.count, now, now + UNDO_SECONDS),
            )
            return DeleteResult("deleted", scope, deletion_id)

    def pending(self, account_id: str) -> list[PendingDeletion]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT d.id, CASE WHEN d.artifact_id IS NULL THEN 'project' ELSE 'file' END, "
                "d.title, d.files, d.deadline, parent.id IS NULL FROM account_deletions d "
                "LEFT JOIN artifacts a ON a.id = d.artifact_id "
                "LEFT JOIN account_deletions parent ON parent.project_id = a.project "
                "WHERE d.account_id = ? AND d.deadline > ? ORDER BY d.marked_at DESC, d.id",
                (account_id, self._clock()),
            ).fetchall()
        return [PendingDeletion(*row[:-1], bool(row[-1])) for row in rows]

    def undo(self, deletion_id: str, *, device_token: str | None = None,
             google_subject: str | None = None) -> Literal["restored", "missing", "project_hidden"]:
        with self._conn(write=True) as db:
            account_id = self._account_for(db, device_token, google_subject)
            if account_id is None:
                return "missing"
            now = self._clock()
            row = db.execute(
                "SELECT d.artifact_id, d.project_id, d.marked_at, a.project "
                "FROM account_deletions d LEFT JOIN artifacts a ON a.id = d.artifact_id "
                "LEFT JOIN projects p ON p.id = d.project_id JOIN account_agents own "
                "ON own.agent = COALESCE(a.owner, p.owner) AND own.account_id = d.account_id "
                "WHERE d.id = ? AND d.account_id = ? AND d.deadline > ?", (deletion_id, account_id, now),
            ).fetchone()
            if row is None:
                return "missing"
            file, project, marked, parent = row
            pause = max(0.0, now - marked)
            if file:
                if parent and db.execute("SELECT 1 FROM account_deletions WHERE project_id = ?", (parent,)).fetchone():
                    return "project_hidden"
                db.execute("UPDATE artifacts SET expires_at = expires_at + ? WHERE id = ? AND expires_at IS NOT NULL",
                           (pause, file))
                db.execute("UPDATE drafts SET updated_at = updated_at + ? WHERE artifact_id = ? AND updated_at > ?",
                           (pause, file, marked - self._draft_ttl))
            else:
                # A file marked earlier has its own lifetime and independent
                # Undo action. Restore only the children hidden by this project.
                db.execute(
                    "UPDATE drafts SET updated_at = updated_at + ? WHERE updated_at > ? "
                    "AND artifact_id IN (SELECT id FROM artifacts a WHERE project = ? "
                    "AND NOT EXISTS (SELECT 1 FROM account_deletions d WHERE d.artifact_id = a.id))",
                    (pause, marked - self._draft_ttl, project),
                )
            db.execute("DELETE FROM account_deletions WHERE id = ?", (deletion_id,))
            return "restored"


def collect_expired(db: sqlite3.Connection, now: float) -> list[str]:
    """Delete due metadata under the caller's write lock, returning byte IDs."""
    ids = [r[0] for r in db.execute(
        "SELECT a.id FROM artifacts a WHERE EXISTS (SELECT 1 FROM account_deletions d "
        "WHERE d.deadline <= :now AND (d.artifact_id = a.id OR d.project_id = a.project)) "
        "OR (a.expires_at <= :now AND NOT EXISTS (SELECT 1 FROM account_deletions d "
        "WHERE d.artifact_id = a.id OR d.project_id = a.project))", {"now": now},
    )]
    db.execute("DELETE FROM projects WHERE id IN (SELECT project_id FROM account_deletions "
               "WHERE project_id IS NOT NULL AND deadline <= ?)", (now,))
    db.executemany("DELETE FROM artifacts WHERE id = ?", [(id_,) for id_ in ids])
    return ids
