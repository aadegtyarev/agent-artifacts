"""Artifacts on disk, metadata and agent key hashes in SQLite.

Every call opens its own connection, so the store is safe to use from the
thread pool without a lock; SQLite serialises the writes.

For async callers: short indexed reads (a key, an artifact, an invite) may run
on the event loop; writes, aggregates and anything touching files go to the
thread pool, since a write may wait for another one's lock.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

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
    edit_token_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    edited_at REAL,
    read_at REAL,
    resolved_at REAL,
    PRIMARY KEY (artifact_id, n)
);
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


@dataclass(frozen=True)
class Comment:
    n: int
    author: str
    text: str
    anchor: str | None  # JSON
    created_at: float
    edited_at: float | None
    read_at: float | None
    resolved_at: float | None
    before_replace: bool


_COMMENT_SQL = (
    "SELECT c.n, c.author, c.text, c.anchor, c.created_at, c.edited_at, c.read_at, "
    "c.resolved_at, a.replaced_at IS NOT NULL AND c.created_at < a.replaced_at "
    "FROM comments c JOIN artifacts a ON a.id = c.artifact_id "
    "WHERE c.artifact_id = :id AND a.expires_at > :now"
)


class TooManyComments(Exception):
    pass


class WrongToken(Exception):
    pass


class AlreadyRead(Exception):
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
    ) -> None:
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
            db.executescript(_SCHEMA)
            for table, columns in _ADDED_COLUMNS.items():
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for column, decl in columns.items():
                    if column not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

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
                {"now": time.time(), "name": name},
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
        invite = Invite(secrets.token_urlsafe(6), name, by, time.time() + ttl)
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM invites WHERE expires_at <= ?", (time.time(),))
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
                (time.time(),),
            ).fetchall()
        return [Invite(*r) for r in rows]

    def invite_for_token(self, token: str) -> Invite | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT id, name, created_by, expires_at FROM invites "
                "WHERE token_hash = ? AND expires_at > ?",
                (hash_key(token), time.time()),
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
                (hash_key(token), time.time()),
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
            {"id": artifact_id, "now": time.time(), "owner": owner},
        ).fetchone()
        return Artifact(*row) if row else None

    def list(self, owner: str) -> list[Artifact]:
        with self._conn() as db:
            rows = db.execute(
                f"{_ARTIFACT_SQL} WHERE a.owner = ? AND a.expires_at > ? ORDER BY a.created_at",
                (owner, time.time()),
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
        now = time.time()
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
                        (name, kind, size, now + ttl, time.time(), new_id, owner, now),
                    ).fetchone()
                    if row is None:
                        return None
                db.execute("UPDATE agents SET last_upload_at = ? WHERE name = ?", (now, owner))
                artifact = self._artifact(db, new_id)
                os.replace(tmp, self.files / new_id)
        finally:
            tmp.unlink(missing_ok=True)
        return artifact

    # Comments; they go with their artifact (ON DELETE CASCADE).

    def comments(self, artifact_id: str, *, owner: str | None = None) -> list[Comment] | None:
        """The comments of a live artifact, oldest first; None if there is no
        such artifact (of ``owner``'s). The owner's read marks them read in the
        same transaction, so it marks exactly what it returns."""
        with self._conn() as db:
            if owner is not None:
                db.execute("BEGIN IMMEDIATE")
            if self._artifact(db, artifact_id, owner) is None:
                return None
            args = {"id": artifact_id, "now": time.time()}
            if owner is not None:
                db.execute(
                    "UPDATE comments SET read_at = :now "
                    "WHERE artifact_id = :id AND read_at IS NULL",
                    args,
                )
            rows = db.execute(f"{_COMMENT_SQL} ORDER BY c.n", args).fetchall()
        return [_comment(r) for r in rows]

    def add_comment(
        self, artifact_id: str, *, author: str, text: str, anchor: str | None, token_hash: str
    ) -> Comment | None:
        """Post a comment; None if the artifact is gone. Raises TooManyComments."""
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            args = {"id": artifact_id, "now": time.time()}
            row = db.execute(
                "SELECT (SELECT COUNT(*) FROM comments WHERE artifact_id = :id) FROM artifacts "
                "WHERE id = :id AND expires_at > :now",
                args,
            ).fetchone()
            if row is None:
                return None
            if row[0] >= MAX_COMMENTS:
                raise TooManyComments
            (n,) = db.execute(
                "UPDATE artifacts SET comment_seq = comment_seq + 1 WHERE id = :id "
                "RETURNING comment_seq",
                args,
            ).fetchone()
            db.execute(
                "INSERT INTO comments (artifact_id, n, author, text, anchor, edit_token_hash, "
                "created_at) VALUES (:id, :n, :author, :text, :anchor, :token_hash, :now)",
                args
                | {
                    "n": n,
                    "author": author,
                    "text": text,
                    "anchor": anchor,
                    "token_hash": token_hash,
                },
            )
            return self._comment(db, artifact_id, n)

    def resolve_comment(
        self, artifact_id: str, n: int, resolved: bool, *, owner: str | None = None
    ) -> Comment | None:
        """Mark a comment resolved or open; anyone may, read or not."""
        with self._conn() as db:
            if self._artifact(db, artifact_id, owner) is None:
                return None
            db.execute(
                "UPDATE comments SET resolved_at = CASE WHEN :resolved "
                "THEN COALESCE(resolved_at, :now) END WHERE artifact_id = :id AND n = :n",
                {"id": artifact_id, "n": n, "resolved": resolved, "now": time.time()},
            )
            return self._comment(db, artifact_id, n)

    def edit_comment(
        self, artifact_id: str, n: int, *, token_hash: str, text: str
    ) -> Comment | None:
        """Change the text by the poster's token. Raises WrongToken, AlreadyRead."""
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._check_token(db, artifact_id, n, token_hash):
                return None
            db.execute(
                "UPDATE comments SET text = :text, edited_at = :now "
                "WHERE artifact_id = :id AND n = :n",
                {"id": artifact_id, "n": n, "text": text, "now": time.time()},
            )
            return self._comment(db, artifact_id, n)

    def delete_comment(
        self,
        artifact_id: str,
        n: int,
        *,
        owner: str | None = None,
        token_hash: str | None = None,
    ) -> bool:
        """Delete by the owner, any time, or by the poster's token until read.
        Raises WrongToken, AlreadyRead."""
        assert (owner is None) != (token_hash is None)
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            if token_hash is not None:
                if not self._check_token(db, artifact_id, n, token_hash):
                    return False
            elif self._artifact(db, artifact_id, owner) is None:
                return False
            sql = "DELETE FROM comments WHERE artifact_id = ? AND n = ?"
            return db.execute(sql, (artifact_id, n)).rowcount > 0

    def _comment(self, db: sqlite3.Connection, artifact_id: str, n: int) -> Comment | None:
        row = db.execute(
            f"{_COMMENT_SQL} AND c.n = :n", {"id": artifact_id, "now": time.time(), "n": n}
        ).fetchone()
        return _comment(row) if row else None

    def _check_token(
        self, db: sqlite3.Connection, artifact_id: str, n: int, token_hash: str
    ) -> bool:
        """False if there is no such comment; the token before the read state."""
        row = db.execute(
            "SELECT c.edit_token_hash, c.read_at FROM comments c "
            "JOIN artifacts a ON a.id = c.artifact_id "
            "WHERE c.artifact_id = :id AND c.n = :n AND a.expires_at > :now",
            {"id": artifact_id, "n": n, "now": time.time()},
        ).fetchone()
        if row is None:
            return False
        if not hmac.compare_digest(row[0], token_hash):
            raise WrongToken
        if row[1] is not None:
            raise AlreadyRead
        return True

    def delete(self, artifact_id: str, *, owner: str) -> bool:
        with self._conn() as db:
            sql = "DELETE FROM artifacts WHERE id = ? AND owner = ?"
            gone = db.execute(sql, (artifact_id, owner)).rowcount > 0
        if gone:
            self._unlink(artifact_id)
        return gone

    def purge_expired(self) -> int:
        with self._conn() as db:
            expired = db.execute("SELECT id FROM artifacts WHERE expires_at <= ?", (time.time(),))
            ids = [r[0] for r in expired]
            db.executemany("DELETE FROM artifacts WHERE id = ?", [(i,) for i in ids])
        for artifact_id in ids:
            self._unlink(artifact_id)
        # Leftovers of a crash mid-upload: temp files, and files whose row
        # never got committed.
        with self._conn() as db:
            known = {r[0] for r in db.execute("SELECT id FROM artifacts")}
        stale = time.time() - 3600
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
    return Comment(*row[:-1], before_replace=bool(row[-1]))
