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
"""


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


class QuotaExceeded(Exception):
    """The owner's own files plus the new one would pass their quota."""

    def __init__(self, used: int, quota: int) -> None:
        super().__init__(f"{used} of {quota} bytes used")
        self.used = used
        self.quota = quota


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Store:
    def __init__(
        self, data_dir: Path, *, max_total_bytes: int, user_quota_bytes: int | None = None
    ) -> None:
        self.files = data_dir / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.max_total_bytes = max_total_bytes
        self.user_quota_bytes = user_quota_bytes
        self._db = data_dir / "meta.sqlite"
        with self._conn() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()

    # Agents

    def add_agent(self, name: str) -> str:
        """Create an agent and return its key; only the hash is kept."""
        key = "aa_" + secrets.token_urlsafe(32)
        with self._conn() as db:
            db.execute("INSERT INTO agents VALUES (?, ?)", (name, hash_key(key)))
        return key

    def remove_agent(self, name: str) -> bool:
        with self._conn() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM artifacts WHERE owner = ?", (name,))]
            gone = db.execute("DELETE FROM agents WHERE name = ?", (name,)).rowcount > 0
        for artifact_id in ids:
            self._unlink(artifact_id)
        return gone

    def agents(self) -> list[str]:
        with self._conn() as db:
            return [r[0] for r in db.execute("SELECT name FROM agents ORDER BY name")]

    def agent_for_key(self, key: str) -> str | None:
        # Looked up by hash, so the key itself is never compared byte by byte.
        with self._conn() as db:
            row = db.execute(
                "SELECT name FROM agents WHERE key_hash = ?", (hash_key(key),)
            ).fetchone()
        return row[0] if row else None

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
        ttl: int,
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
                if self.user_quota_bytes is not None:
                    own = db.execute(
                        "SELECT COALESCE(SUM(size), 0) FROM artifacts "
                        "WHERE owner = ? AND id != ? AND expires_at > ?",
                        (owner, new_id, now),
                    ).fetchone()[0]
                    if own + size > self.user_quota_bytes:
                        raise QuotaExceeded(own, self.user_quota_bytes)
                used = db.execute(
                    "SELECT COALESCE(SUM(size), 0) FROM artifacts WHERE id != ? AND expires_at > ?",
                    (new_id, now),
                ).fetchone()[0]
                if used + size > self.max_total_bytes:
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
            if f.name not in known and f.stat().st_mtime < stale:
                f.unlink(missing_ok=True)
        return len(ids)

    def path(self, artifact_id: str) -> Path:
        return self.files / artifact_id

    def _unlink(self, artifact_id: str) -> None:
        self.path(artifact_id).unlink(missing_ok=True)
