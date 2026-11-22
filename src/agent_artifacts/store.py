"""Artifacts on disk, metadata and agent key hashes in SQLite.

Every call opens its own connection, so the store is safe to use from the
thread pool without a lock; SQLite serialises the writes.

For async callers: short indexed reads (a key, an artifact, an invite) may run
on the event loop; writes, aggregates and anything touching files go to the
thread pool, since a write may wait for another one's lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from . import kinds

ID_RE = r"[A-Za-z0-9_-]{22}"
# Not starting with a dot: "." and ".." would never reach the app in a URL.
NAME_RE = r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}"
TOKEN_RE = r"[A-Za-z0-9_-]{43}"  # noqa: S105 (a pattern, not a secret)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    admin INTEGER NOT NULL DEFAULT 0,
    quota_bytes INTEGER,  -- NULL: the common quota
    last_upload_at REAL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    comment_seq INTEGER NOT NULL DEFAULT 0,  -- the last comment number handed out
    replaced_at REAL
);
CREATE INDEX IF NOT EXISTS artifacts_owner ON artifacts(owner);
CREATE INDEX IF NOT EXISTS artifacts_expires ON artifacts(expires_at);
CREATE TABLE IF NOT EXISTS invites (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    anchor TEXT,  -- JSON; NULL: about the whole file
    author_key_hash TEXT NOT NULL,  -- SHA-256 of the viewer's author key
    created_at REAL NOT NULL,  -- published
    written_at REAL,  -- its draft made; NULL: as created_at
    read_at REAL,
    resolved_at REAL,
    for_agent INTEGER NOT NULL DEFAULT 0,  -- the review was sent to the agent
    PRIMARY KEY (artifact_id, n)
);
CREATE TABLE IF NOT EXISTS drafts (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    author_key_hash TEXT NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    anchor TEXT,  -- JSON; NULL: about the whole file
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (artifact_id, id)
);
CREATE INDEX IF NOT EXISTS drafts_updated ON drafts(updated_at);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

# Columns added after the first release, for databases made before.
_ADDED_COLUMNS = {
    "agents": {
        "admin": "INTEGER NOT NULL DEFAULT 0",
        "quota_bytes": "INTEGER",
        "last_upload_at": "REAL",
    },
    "artifacts": {
        "comment_seq": "INTEGER NOT NULL DEFAULT 0",
        "replaced_at": "REAL",
    },
    "comments": {
        "written_at": "REAL",
        "for_agent": "INTEGER NOT NULL DEFAULT 0",
    },
}
# Columns renamed since, old name to new, for databases made before.
_RENAMED_COLUMNS = {
    "comments": {"edit_token_hash": "author_key_hash"},
}

SETTINGS = ("user_quota_bytes", "max_total_bytes", "ttl_seconds")


@dataclass(frozen=True)
class Artifact:
    id: str
    owner: str
    name: str
    kind: str
    size: int
    created_at: float
    expires_at: float
    comments: int = 0
    open_comments: int = 0


# An artifact row with its comment counts, for Artifact(*row).
_ARTIFACT_SQL = (
    "SELECT a.id, a.owner, a.name, a.kind, a.size, a.created_at, a.expires_at, "
    "(SELECT COUNT(*) FROM comments c WHERE c.artifact_id = a.id), "
    "(SELECT COUNT(*) FROM comments c WHERE c.artifact_id = a.id AND c.resolved_at IS NULL) "
    "FROM artifacts a"
)

MAX_COMMENTS = 200
MAX_DRAFTS_PER_KEY = 50  # one author key's drafts on one file
MAX_DRAFTS = 200  # all drafts on one file
DRAFT_TTL_SECONDS = 7 * 24 * 3600  # a draft not changed for this long is deleted


@dataclass(frozen=True)
class Comment:
    n: int
    author: str
    text: str
    anchor: str | None  # JSON
    created_at: float
    read_at: float | None
    resolved_at: float | None
    before_replace: bool
    mine: bool
    for_agent: bool


# :key is the author key hash asking, or NULL: then no comment is its own.
_COMMENT_SQL = (
    "SELECT c.n, c.author, c.text, c.anchor, c.created_at, c.read_at, c.resolved_at, "
    "a.replaced_at IS NOT NULL AND COALESCE(c.written_at, c.created_at) < a.replaced_at, "
    "COALESCE(c.author_key_hash = :key, 0), c.for_agent "
    "FROM comments c JOIN artifacts a ON a.id = c.artifact_id "
    "WHERE c.artifact_id = :id AND a.expires_at > :now"
)


@dataclass(frozen=True)
class Draft:
    id: str
    author: str
    text: str
    anchor: str | None  # JSON
    created_at: float
    updated_at: float


# One author key's live drafts on a live artifact.
_DRAFT_SQL = (
    "SELECT d.id, d.author, d.text, d.anchor, d.created_at, d.updated_at "
    "FROM drafts d JOIN artifacts a ON a.id = d.artifact_id "
    "WHERE d.artifact_id = :id AND d.author_key_hash = :key "
    "AND a.expires_at > :now AND d.updated_at > :stale"
)


# The draft :draft of the author key :key, live on a live artifact.
_OWN_DRAFT = (
    "artifact_id = :id AND id = :draft AND author_key_hash = :key AND updated_at > :stale "
    "AND EXISTS (SELECT 1 FROM artifacts WHERE id = :id AND expires_at > :now)"
)


class TooManyComments(Exception):
    pass


class TooManyDrafts(Exception):
    pass


class NothingToPublish(Exception):
    pass


class StorageFull(Exception):
    pass


class NameTaken(Exception):
    pass


@dataclass(frozen=True)
class Invite:
    id: str
    name: str
    created_by: str
    expires_at: float


class QuotaExceeded(Exception):
    """The owner's own files plus the new one would pass their quota."""

    def __init__(self, used: int, quota: int) -> None:
        super().__init__(f"{used} of {quota} bytes used")
        self.used = used
        self.quota = quota


def _new_key() -> str:
    return "aa_" + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass(frozen=True)
class AgentInfo:
    name: str
    admin: bool
    quota_bytes: int | None  # its own quota, None for the common one
    last_upload_at: float | None
    used_bytes: int
    files: int


class Store:
    def __init__(
        self,
        data_dir: Path,
        *,
        max_total_bytes: int,
        user_quota_bytes: int | None = None,
        ttl_seconds: int = 48 * 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.clock = clock  # the time now, in seconds; tests move it
        self.files = data_dir / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        # Starting values; an admin can override them, see set_settings().
        self.defaults = {
            "user_quota_bytes": user_quota_bytes,
            "max_total_bytes": max_total_bytes,
            "ttl_seconds": ttl_seconds,
        }
        self._db = data_dir / "meta.sqlite"
        with self._conn() as db:
            db.execute("PRAGMA journal_mode = WAL")
            for table, renamed in _RENAMED_COLUMNS.items():
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for old, new in renamed.items():
                    if old in have and new not in have:
                        db.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
            db.executescript(_SCHEMA)
            for table, columns in _ADDED_COLUMNS.items():
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for column, decl in columns.items():
                    if column not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            # A counter behind its comments (added to a database that had
            # comments) would hand out their numbers again.
            last = "(SELECT MAX(n) FROM comments WHERE artifact_id = artifacts.id)"
            db.execute(
                f"UPDATE artifacts SET comment_seq = {last} "  # noqa: S608 (a constant)
                f"WHERE comment_seq < COALESCE({last}, 0)"
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()

    # Limits

    def settings(self, db: sqlite3.Connection | None = None) -> dict[str, int | None]:
        if db is None:
            with self._conn() as db:
                return self.settings(db)
        return self.defaults | dict(db.execute("SELECT key, value FROM settings").fetchall())

    def set_settings(self, **values: int) -> None:
        assert set(values) <= set(SETTINGS), values
        with self._conn() as db:
            db.executemany(
                "INSERT INTO settings VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                values.items(),
            )

    def _quota_for(self, name: str, db: sqlite3.Connection) -> int | None:
        row = db.execute("SELECT quota_bytes FROM agents WHERE name = ?", (name,)).fetchone()
        own = row[0] if row else None
        return own if own is not None else self.settings(db)["user_quota_bytes"]

    # Agents

    def add_agent(self, name: str, *, admin: bool = False) -> str:
        """Create an agent and return its key; only the hash is kept."""
        key = _new_key()
        with self._conn() as db:
            db.execute(
                "INSERT INTO agents (name, key_hash, admin) VALUES (?, ?, ?)",
                (name, hash_key(key), int(admin)),
            )
        return key

    def set_admin(self, name: str, admin: bool) -> bool:
        with self._conn() as db:
            sql = "UPDATE agents SET admin = ? WHERE name = ?"
            return db.execute(sql, (int(admin), name)).rowcount > 0

    def set_quota(self, name: str, quota_bytes: int | None) -> bool:
        """Give ``name`` its own quota, or None for the common one."""
        with self._conn() as db:
            sql = "UPDATE agents SET quota_bytes = ? WHERE name = ?"
            return db.execute(sql, (quota_bytes, name)).rowcount > 0

    def is_admin(self, name: str) -> bool:
        with self._conn() as db:
            row = db.execute("SELECT admin FROM agents WHERE name = ?", (name,)).fetchone()
        return bool(row and row[0])

    def agent_infos(self, name: str | None = None) -> list[AgentInfo]:
        """Every agent with its space in use, or only ``name``."""
        with self._conn() as db:
            rows = db.execute(
                "SELECT a.name, a.admin, a.quota_bytes, a.last_upload_at, "
                "COALESCE(SUM(f.size), 0), COUNT(f.id) FROM agents a "
                "LEFT JOIN artifacts f ON f.owner = a.name AND f.expires_at > :now "
                "WHERE :name IS NULL OR a.name = :name GROUP BY a.name ORDER BY a.name",
                {"now": self.clock(), "name": name},
            ).fetchall()
        return [AgentInfo(r[0], bool(r[1]), *r[2:]) for r in rows]

    def agent_info(self, name: str) -> AgentInfo | None:
        found = self.agent_infos(name)
        return found[0] if found else None

    def new_key(self, name: str) -> str:
        """Give ``name`` a new key; the old one stops working at once."""
        key = _new_key()
        with self._conn() as db:
            db.execute("UPDATE agents SET key_hash = ? WHERE name = ?", (hash_key(key), name))
        return key

    def remove_agent(self, name: str) -> bool:
        with self._conn() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM artifacts WHERE owner = ?", (name,))]
            gone = db.execute("DELETE FROM agents WHERE name = ?", (name,)).rowcount > 0
        for artifact_id in ids:
            self._unlink(artifact_id)
        return gone

    def agent_for_key(self, key: str) -> str | None:
        # Looked up by hash, so the key itself is never compared byte by byte.
        with self._conn() as db:
            row = db.execute(
                "SELECT name FROM agents WHERE key_hash = ?", (hash_key(key),)
            ).fetchone()
        return row[0] if row else None

    # Invites

    def add_invite(self, name: str, *, by: str, ttl: int) -> tuple[Invite, str]:
        """A one-time invite for a new agent ``name``, and its token; only the
        token's hash is kept. Raises NameTaken if the name is in use."""
        token = secrets.token_urlsafe(32)
        invite = Invite(secrets.token_urlsafe(6), name, by, self.clock() + ttl)
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM invites WHERE expires_at <= ?", (self.clock(),))
            if db.execute("SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone():
                raise NameTaken
            try:
                db.execute(
                    "INSERT INTO invites VALUES (?, ?, ?, ?, ?)",
                    (invite.id, hash_key(token), name, by, invite.expires_at),
                )
            except sqlite3.IntegrityError:
                raise NameTaken from None
        return invite, token

    def invites(self) -> list[Invite]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT id, name, created_by, expires_at FROM invites "
                "WHERE expires_at > ? ORDER BY expires_at",
                (self.clock(),),
            ).fetchall()
        return [Invite(*r) for r in rows]

    def invite_for_token(self, token: str) -> Invite | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT id, name, created_by, expires_at FROM invites "
                "WHERE token_hash = ? AND expires_at > ?",
                (hash_key(token), self.clock()),
            ).fetchone()
        return Invite(*row) if row else None

    def cancel_invite(self, invite_id: str) -> bool:
        with self._conn() as db:
            return db.execute("DELETE FROM invites WHERE id = ?", (invite_id,)).rowcount > 0

    def claim_invite(self, token: str) -> tuple[str, str] | None:
        """Use up the invite: create its agent and return (name, key). None if
        the token is unknown, used or expired; NameTaken if the name is in use."""
        key = _new_key()
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "DELETE FROM invites WHERE token_hash = ? AND expires_at > ? RETURNING name",
                (hash_key(token), self.clock()),
            ).fetchone()
            if row is None:
                return None
            try:
                db.execute(
                    "INSERT INTO agents (name, key_hash) VALUES (?, ?)", (row[0], hash_key(key))
                )
            except sqlite3.IntegrityError:  # made by hand since; the invite stays
                raise NameTaken from None
        return row[0], key

    # Artifacts

    def get(self, artifact_id: str, *, owner: str | None = None) -> Artifact | None:
        """A live artifact, optionally only if ``owner`` owns it."""
        with self._conn() as db:
            return self._artifact(db, artifact_id, owner)

    def _artifact(
        self, db: sqlite3.Connection, artifact_id: str, owner: str | None = None
    ) -> Artifact | None:
        row = db.execute(
            f"{_ARTIFACT_SQL} WHERE a.id = :id AND a.expires_at > :now "
            "AND (:owner IS NULL OR a.owner = :owner)",
            {"id": artifact_id, "now": self.clock(), "owner": owner},
        ).fetchone()
        return Artifact(*row) if row else None

    def list(self, owner: str) -> list[Artifact]:
        with self._conn() as db:
            rows = db.execute(
                f"{_ARTIFACT_SQL} WHERE a.owner = ? AND a.expires_at > ? ORDER BY a.created_at",
                (owner, self.clock()),
            ).fetchall()
        return [Artifact(*r) for r in rows]

    def put(
        self,
        src: BinaryIO,
        *,
        owner: str,
        name: str,
        kind: str,
        size: int,
        artifact_id: str | None = None,
    ) -> Artifact | None:
        """Store a new artifact, or replace ``artifact_id`` if ``owner`` owns
        it. Returns None when there is no such artifact of ``owner``'s."""
        new_id = artifact_id or secrets.token_urlsafe(16)
        tmp = self.files / f".{new_id}.{secrets.token_hex(4)}.tmp"
        with open(tmp, "wb") as out:
            shutil.copyfileobj(src, out)
        now = self.clock()
        try:
            with self._conn() as db:
                # Hold the write lock from the size checks to the insert, so two
                # uploads cannot both fit into the same free space.
                db.execute("BEGIN IMMEDIATE")
                settings = self.settings(db)
                ttl = settings["ttl_seconds"]
                quota = self._quota_for(owner, db)
                if quota is not None:
                    own = db.execute(
                        "SELECT COALESCE(SUM(size), 0) FROM artifacts "
                        "WHERE owner = ? AND id != ? AND expires_at > ?",
                        (owner, new_id, now),
                    ).fetchone()[0]
                    if own + size > quota:
                        raise QuotaExceeded(own, quota)
                used = db.execute(
                    "SELECT COALESCE(SUM(size), 0) FROM artifacts WHERE id != ? AND expires_at > ?",
                    (new_id, now),
                ).fetchone()[0]
                if used + size > settings["max_total_bytes"]:
                    raise StorageFull
                if artifact_id is None:
                    db.execute(
                        "INSERT INTO artifacts (id, owner, name, kind, size, created_at, "
                        "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (new_id, owner, name, kind, size, now, now + ttl),
                    )
                else:
                    # Read under the lock, so a comment posted before this
                    # replace always has an earlier created_at.
                    row = db.execute(
                        "UPDATE artifacts SET name = ?, kind = ?, size = ?, expires_at = ?, "
                        "replaced_at = ? WHERE id = ? AND owner = ? AND expires_at > ? "
                        "RETURNING id",
                        (name, kind, size, now + ttl, self.clock(), new_id, owner, now),
                    ).fetchone()
                    if row is None:
                        return None
                db.execute("UPDATE agents SET last_upload_at = ? WHERE name = ?", (now, owner))
                artifact = self._artifact(db, new_id)
                os.replace(tmp, self.files / new_id)
        finally:
            tmp.unlink(missing_ok=True)
        return artifact

    # Comments and drafts; they go with their artifact (ON DELETE CASCADE).

    def comments(
        self, artifact_id: str, *, owner: str | None = None, key_hash: str | None = None
    ) -> list[Comment] | None:
        """The comments of a live artifact, oldest first; None if there is no
        such artifact (of ``owner``'s). The owner gets them all and marks them
        read in the same transaction, so it marks exactly what it returns;
        anyone else gets the open ones, each marked as ``key_hash``'s or not."""
        with self._conn() as db:
            if owner is not None:
                db.execute("BEGIN IMMEDIATE")
            if self._artifact(db, artifact_id, owner) is None:
                return None
            args = {"id": artifact_id, "now": self.clock(), "key": key_hash}
            if owner is not None:
                db.execute(
                    "UPDATE comments SET read_at = :now "
                    "WHERE artifact_id = :id AND read_at IS NULL",
                    args,
                )
                rows = db.execute(f"{_COMMENT_SQL} ORDER BY c.n", args).fetchall()
            else:
                rows = db.execute(
                    f"{_COMMENT_SQL} AND c.resolved_at IS NULL ORDER BY c.n", args
                ).fetchall()
        return [_comment(r) for r in rows]

    def resolve_comment(
        self, artifact_id: str, n: int, resolved: bool, *, owner: str
    ) -> Comment | None:
        """Mark a comment resolved or open, read or not."""
        with self._conn() as db:
            if self._artifact(db, artifact_id, owner) is None:
                return None
            db.execute(
                "UPDATE comments SET resolved_at = CASE WHEN :resolved "
                "THEN COALESCE(resolved_at, :now) END WHERE artifact_id = :id AND n = :n",
                {"id": artifact_id, "n": n, "resolved": resolved, "now": self.clock()},
            )
            return self._comment(db, artifact_id, n)

    def delete_comment(self, artifact_id: str, n: int, *, owner: str) -> bool:
        with self._conn() as db:
            if self._artifact(db, artifact_id, owner) is None:
                return False
            sql = "DELETE FROM comments WHERE artifact_id = ? AND n = ?"
            return db.execute(sql, (artifact_id, n)).rowcount > 0

    def _comment(self, db: sqlite3.Connection, artifact_id: str, n: int) -> Comment | None:
        row = db.execute(
            f"{_COMMENT_SQL} AND c.n = :n",
            {"id": artifact_id, "now": self.clock(), "n": n, "key": None},
        ).fetchone()
        return _comment(row) if row else None

    def _draft_args(self, artifact_id: str, key_hash: str, **more: object) -> dict:
        now = self.clock()
        return {
            "id": artifact_id,
            "key": key_hash,
            "now": now,
            "stale": now - DRAFT_TTL_SECONDS,
            **more,
        }

    def drafts(self, artifact_id: str, key_hash: str) -> list[Draft] | None:
        """``key_hash``'s drafts on a live artifact, oldest first; None if
        there is no such artifact."""
        with self._conn() as db:
            if self._artifact(db, artifact_id) is None:
                return None
            args = self._draft_args(artifact_id, key_hash)
            rows = db.execute(f"{_DRAFT_SQL} ORDER BY d.created_at, d.rowid", args).fetchall()
        return [Draft(*r) for r in rows]

    def add_draft(
        self, artifact_id: str, key_hash: str, *, author: str, text: str, anchor: str | None
    ) -> Draft | None:
        """A new draft; None if the artifact is gone. Raises TooManyDrafts."""
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._artifact(db, artifact_id) is None:
                return None
            args = self._draft_args(artifact_id, key_hash)
            # Expired drafts make room before they are counted.
            db.execute("DELETE FROM drafts WHERE artifact_id = :id AND updated_at <= :stale", args)
            mine, total = db.execute(
                "SELECT COALESCE(SUM(author_key_hash = :key), 0), COUNT(*) FROM drafts "
                "WHERE artifact_id = :id",
                args,
            ).fetchone()
            if mine >= MAX_DRAFTS_PER_KEY or total >= MAX_DRAFTS:
                raise TooManyDrafts
            draft = Draft(secrets.token_urlsafe(8), author, text, anchor, args["now"], args["now"])
            db.execute(
                "INSERT INTO drafts (artifact_id, id, author_key_hash, author, text, anchor, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, draft.id, key_hash, author, text, anchor, args["now"], args["now"]),
            )
        return draft

    def edit_draft(self, artifact_id: str, key_hash: str, draft_id: str, text: str) -> Draft | None:
        """Change a draft's text; None if there is no such draft of ``key_hash``'s."""
        with self._conn() as db:
            row = db.execute(
                f"UPDATE drafts SET text = :text, updated_at = :now WHERE {_OWN_DRAFT} "  # noqa: S608
                "RETURNING id, author, text, anchor, created_at, updated_at",
                self._draft_args(artifact_id, key_hash, draft=draft_id, text=text),
            ).fetchone()
        return Draft(*row) if row else None

    def delete_draft(self, artifact_id: str, key_hash: str, draft_id: str) -> bool:
        with self._conn() as db:
            args = self._draft_args(artifact_id, key_hash, draft=draft_id)
            sql = f"DELETE FROM drafts WHERE {_OWN_DRAFT}"  # noqa: S608 (a constant)
            return db.execute(sql, args).rowcount > 0

    def drop_drafts(self, artifact_id: str, *, owner: str) -> bool:
        """Delete every draft on the owner's file, unread; False if there is
        no such artifact of ``owner``'s."""
        with self._conn() as db:
            if self._artifact(db, artifact_id, owner) is None:
                return False
            db.execute("DELETE FROM drafts WHERE artifact_id = ?", (artifact_id,))
        return True

    def publish(
        self,
        artifact_id: str,
        key_hash: str,
        *,
        author: str,
        text: str | None,
        for_agent: bool = False,
    ) -> list[Comment] | None:
        """Finish ``key_hash``'s review: its drafts on the artifact, in the
        order made, then ``text`` as a comment about the whole file, become
        comments with the next numbers, all or none, marked as sent to the
        agent when ``for_agent``. None if the artifact is gone; raises
        NothingToPublish, TooManyComments."""
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            artifact = self._artifact(db, artifact_id)
            if artifact is None:
                return None
            # The file's type now: a draft pinned to a type it no longer has
            # (replaced since) becomes a comment about the whole file.
            pin = kinds.get(artifact.kind).pin
            args = self._draft_args(artifact_id, key_hash)
            drafts = db.execute(
                "SELECT d.rowid, d.author, d.text, d.anchor, d.created_at FROM drafts d "
                "WHERE d.artifact_id = :id AND d.author_key_hash = :key "
                "AND d.updated_at > :stale ORDER BY d.created_at, d.rowid",
                args,
            ).fetchall()
            items = [
                (d_author, d_text, d_anchor if _anchor_fits(d_anchor, pin) else None, written)
                for _, d_author, d_text, d_anchor, written in drafts
            ]
            if text is not None:
                items.append((author, text, None, args["now"]))
            if not items:
                raise NothingToPublish
            if artifact.comments + len(items) > MAX_COMMENTS:
                raise TooManyComments
            (last,) = db.execute(
                "UPDATE artifacts SET comment_seq = comment_seq + ? WHERE id = ? "
                "RETURNING comment_seq",
                (len(items), artifact_id),
            ).fetchone()
            first = last - len(items) + 1
            db.executemany(
                "INSERT INTO comments (artifact_id, n, author, text, anchor, author_key_hash, "
                "created_at, written_at, for_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (artifact_id, first + i, a, t, anchor, key_hash, args["now"], written, for_agent)
                    for i, (a, t, anchor, written) in enumerate(items)
                ],
            )
            db.executemany("DELETE FROM drafts WHERE rowid = ?", [(d[0],) for d in drafts])
            rows = db.execute(
                f"{_COMMENT_SQL} AND c.n >= :first ORDER BY c.n", args | {"first": first}
            ).fetchall()
        return [_comment(r) for r in rows]

    def delete(self, artifact_id: str, *, owner: str) -> bool:
        with self._conn() as db:
            sql = "DELETE FROM artifacts WHERE id = ? AND owner = ?"
            gone = db.execute(sql, (artifact_id, owner)).rowcount > 0
        if gone:
            self._unlink(artifact_id)
        return gone

    def purge_expired(self) -> int:
        with self._conn() as db:
            expired = db.execute("SELECT id FROM artifacts WHERE expires_at <= ?", (self.clock(),))
            ids = [r[0] for r in expired]
            db.executemany("DELETE FROM artifacts WHERE id = ?", [(i,) for i in ids])
            db.execute(
                "DELETE FROM drafts WHERE updated_at <= ?", (self.clock() - DRAFT_TTL_SECONDS,)
            )
        for artifact_id in ids:
            self._unlink(artifact_id)
        # Leftovers of a crash mid-upload: temp files, and files whose row
        # never got committed.
        with self._conn() as db:
            known = {r[0] for r in db.execute("SELECT id FROM artifacts")}
        stale = time.time() - 3600  # file times are the real clock's
        for f in self.files.iterdir():
            try:
                old = f.stat().st_mtime < stale
            except FileNotFoundError:  # an upload finished or a delete ran meanwhile
                continue
            if f.name not in known and old:
                f.unlink(missing_ok=True)
        return len(ids)

    def path(self, artifact_id: str) -> Path:
        return self.files / artifact_id

    def _unlink(self, artifact_id: str) -> None:
        self.path(artifact_id).unlink(missing_ok=True)


def _comment(row: tuple) -> Comment:
    return Comment(
        *row[:-3], before_replace=bool(row[-3]), mine=bool(row[-2]), for_agent=bool(row[-1])
    )


def _anchor_fits(anchor: str | None, pin: str | None) -> bool:
    """Whether a stored anchor is of the type the file's pins take now."""
    return anchor is not None and pin is not None and json.loads(anchor).get("type") == pin
