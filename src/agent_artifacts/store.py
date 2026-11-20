"""Artifacts on disk, metadata and agent key hashes in SQLite.

Every call opens its own connection, so the store is safe to use from the
thread pool without a lock; SQLite serialises the writes.
"""

from __future__ import annotations

import hashlib
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
NAME_RE = r"[A-Za-z0-9_.-]{1,64}"
TOKEN_RE = r"[A-Za-z0-9_-]{43}"  # noqa: S105 (a pattern, not a secret)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
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
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

# Columns added after the first release, for databases created before them.
_AGENT_COLUMNS = {
    "admin": "INTEGER NOT NULL DEFAULT 0",
    "quota_bytes": "INTEGER",  # NULL: the common quota
    "last_upload_at": "REAL",
}

LIMITS = ("user_quota_bytes", "max_total_bytes", "ttl_seconds")


@dataclass(frozen=True)
class Artifact:
    id: str
    owner: str
    name: str
    kind: str
    size: int
    created_at: float
    expires_at: float


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
        # Starting values; an admin can override them, see set_limits().
        self.defaults = {
            "user_quota_bytes": user_quota_bytes,
            "max_total_bytes": max_total_bytes,
            "ttl_seconds": ttl_seconds,
        }
        self._db = data_dir / "meta.sqlite"
        with self._conn() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(_SCHEMA)
            have = {r[1] for r in db.execute("PRAGMA table_info(agents)")}
            for column, decl in _AGENT_COLUMNS.items():
                if column not in have:
                    db.execute(f"ALTER TABLE agents ADD COLUMN {column} {decl}")

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

    def limits(self, db: sqlite3.Connection | None = None) -> dict[str, int | None]:
        if db is None:
            with self._conn() as db:
                return self.limits(db)
        return self.defaults | dict(db.execute("SELECT key, value FROM settings").fetchall())

    def set_limits(self, **values: int) -> None:
        assert set(values) <= set(LIMITS), values
        with self._conn() as db:
            db.executemany(
                "INSERT INTO settings VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                values.items(),
            )

    def quota_for(self, name: str, db: sqlite3.Connection | None = None) -> int | None:
        if db is None:
            with self._conn() as db:
                return self.quota_for(name, db)
        row = db.execute("SELECT quota_bytes FROM agents WHERE name = ?", (name,)).fetchone()
        own = row[0] if row else None
        return own if own is not None else self.limits(db)["user_quota_bytes"]

    # Agents

    def add_agent(self, name: str, *, admin: bool = False) -> str:
        """Create an agent and return its key; only the hash is kept."""
        key = "aa_" + secrets.token_urlsafe(32)
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

    def agent_infos(self) -> list[AgentInfo]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT a.name, a.admin, a.quota_bytes, a.last_upload_at, "
                "COALESCE(SUM(f.size), 0), COUNT(f.id) FROM agents a "
                "LEFT JOIN artifacts f ON f.owner = a.name AND f.expires_at > ? "
                "GROUP BY a.name ORDER BY a.name",
                (time.time(),),
            ).fetchall()
        return [AgentInfo(r[0], bool(r[1]), *r[2:]) for r in rows]

    def new_key(self, name: str) -> str:
        """Give ``name`` a new key; the old one stops working at once."""
        key = "aa_" + secrets.token_urlsafe(32)
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
        key = "aa_" + secrets.token_urlsafe(32)
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
        sql = "SELECT * FROM artifacts WHERE id = ? AND expires_at > ?"
        args: tuple = (artifact_id, time.time())
        if owner is not None:
            sql += " AND owner = ?"
            args += (owner,)
        with self._conn() as db:
            row = db.execute(sql, args).fetchone()
        return Artifact(*row) if row else None

    def list(self, owner: str) -> list[Artifact]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT * FROM artifacts WHERE owner = ? AND expires_at > ? ORDER BY created_at",
                (owner, time.time()),
            ).fetchall()
        return [Artifact(*r) for r in rows]

    def usage(self, owner: str) -> tuple[int, int]:
        """Bytes and number of ``owner``'s live artifacts."""
        with self._conn() as db:
            return db.execute(
                "SELECT COALESCE(SUM(size), 0), COUNT(*) FROM artifacts "
                "WHERE owner = ? AND expires_at > ?",
                (owner, time.time()),
            ).fetchone()

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
                limits = self.limits(db)
                ttl = limits["ttl_seconds"]
                quota = self.quota_for(owner, db)
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
                if used + size > limits["max_total_bytes"]:
                    raise StorageFull
                if artifact_id is None:
                    db.execute(
                        "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (new_id, owner, name, kind, size, now, now + ttl),
                    )
                    created = now
                else:
                    row = db.execute(
                        "UPDATE artifacts SET name = ?, kind = ?, size = ?, expires_at = ? "
                        "WHERE id = ? AND owner = ? AND expires_at > ? RETURNING created_at",
                        (name, kind, size, now + ttl, new_id, owner, now),
                    ).fetchone()
                    if row is None:
                        return None
                    created = row[0]
                db.execute("UPDATE agents SET last_upload_at = ? WHERE name = ?", (now, owner))
                os.replace(tmp, self.files / new_id)
        finally:
            tmp.unlink(missing_ok=True)
        return Artifact(new_id, owner, name, kind, size, created, now + ttl)

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
