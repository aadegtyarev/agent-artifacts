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
import logging
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

log = logging.getLogger(__name__)

ID_RE = r"[A-Za-z0-9_-]{22}"
# Not starting with a dot: "." and ".." would never reach the app in a URL.
NAME_RE = r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}"
TOKEN_RE = r"[A-Za-z0-9_-]{43}"  # noqa: S105 (a pattern, not a secret)

# The schema at version 1, the first numbered one. Frozen: databases stamped 1
# have exactly this, so a later change is a new migration, never an edit here.
_SCHEMA_V1 = """
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
    approved_by TEXT,  -- the name of the person who approved it last
    rejected_by TEXT,  -- the name of the person who rejected it last
    agent TEXT,  -- NULL or one of AGENT_STATES
    PRIMARY KEY (artifact_id, n)
);
CREATE TABLE IF NOT EXISTS replies (
    artifact_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    author_key_hash TEXT,  -- NULL: written by the owner agent
    created_at REAL NOT NULL,
    by_agent INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (artifact_id, n) REFERENCES comments(artifact_id, n) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS replies_comment ON replies(artifact_id, n);
CREATE TABLE IF NOT EXISTS drafts (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    author_key_hash TEXT NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    anchor TEXT,  -- JSON; NULL: about the whole file
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'comment',  -- one of DRAFT_KINDS
    of_n INTEGER,  -- the comment a reply or a decision is about
    PRIMARY KEY (artifact_id, id)
);
CREATE INDEX IF NOT EXISTS drafts_updated ON drafts(updated_at);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

# Columns renamed before version 1, old name to new.
_RENAMED_BEFORE_V1 = {
    "comments": {"edit_token_hash": "author_key_hash"},
}


def _statements(script: str) -> list[str]:
    """The statements of ``script``, to run one by one inside a transaction
    (executescript would commit it first)."""
    statements, part = [], ""
    for line in script.splitlines(keepends=True):
        part += line
        if sqlite3.complete_statement(part):
            statements.append(part)
            part = ""
    return statements


def _shape(db: sqlite3.Connection) -> tuple[dict[str, dict[str, tuple]], set[str]]:
    """Each table's columns, name to (type, not null, default, primary key);
    and the names of the indexes."""
    master = db.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    tables, indexes = {}, set()
    for kind, name in master.fetchall():
        if kind == "table":
            tables[name] = {r[1]: r[2:] for r in db.execute(f"PRAGMA table_info({name})")}
        elif kind == "index":
            indexes.add(name)
    return tables, indexes


def _v1_shape() -> tuple[dict[str, dict[str, tuple]], set[str]]:
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(_SCHEMA_V1)
        return _shape(mem)
    finally:
        mem.close()


def _is_v1(db: sqlite3.Connection) -> bool:
    """Whether a database without a version was made by the code before
    numbered versions, after its last change: it has every table, column and
    index of version 1, so _migrate_to_v1 would add nothing. Columns no longer
    used may remain."""
    want_tables, want_indexes = _v1_shape()
    have_tables, have_indexes = _shape(db)
    return want_indexes <= have_indexes and all(
        set(columns) <= set(have_tables.get(table, ())) for table, columns in want_tables.items()
    )


def _migrate_to_v1(db: sqlite3.Connection) -> None:
    """Any shape made before numbered versions, empty included, to version 1:
    what every start did until then."""
    for table, renamed in _RENAMED_BEFORE_V1.items():
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for old, new in renamed.items():
            if old in have and new not in have:
                db.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    for statement in _statements(_SCHEMA_V1):
        db.execute(statement)
    # Tables made before some of their columns get the missing ones.
    added = set()
    for table, columns in _v1_shape()[0].items():
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for column, (type_, not_null, default, _) in columns.items():
            if column not in have:
                decl = type_ + " NOT NULL" * not_null
                decl += f" DEFAULT {default}" if default is not None else ""
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                added.add((table, column))
    if ("comments", "agent") in added:
        # Sent to the agent before its state was kept: still its work.
        db.execute(
            "UPDATE comments SET agent = 'waiting' WHERE for_agent = 1 AND resolved_at IS NULL"
        )
    _repair_comment_seq(db)


def _migrate_to_v2(db: sqlite3.Connection) -> None:
    """Comments lose for_agent and read_at: ``agent`` already says which are
    the agent's work, and nothing used when the owner had read a comment.
    DROP COLUMN needs SQLite 3.35; python:3.13-slim (Debian 13) has 3.46."""
    for column in ("for_agent", "read_at"):
        db.execute(f"ALTER TABLE comments DROP COLUMN {column}")


def _migrate_to_v3(db: sqlite3.Connection) -> None:
    """Artifacts get a title, what pages show; until now that was the name."""
    db.execute("ALTER TABLE artifacts ADD COLUMN title TEXT")
    db.execute("UPDATE artifacts SET title = name")


def _migrate_to_v4(db: sqlite3.Connection) -> None:
    """Projects. A file in one never expires, so expires_at becomes nullable;
    SQLite cannot alter a column's NOT NULL, so the artifacts table is built
    anew and the rows copied. Foreign keys are off while migrating (see
    Store.__init__): dropping the old table must not take its comments along."""
    db.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, "
        "owner TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE, "
        "title TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    db.execute("CREATE INDEX projects_owner ON projects(owner)")
    db.execute("""
        CREATE TABLE artifacts_new (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            size INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL,  -- NULL: in a project, kept until deleted
            comment_seq INTEGER NOT NULL DEFAULT 0,  -- the last comment number handed out
            replaced_at REAL,
            title TEXT,
            project TEXT REFERENCES projects(id) ON DELETE CASCADE,
            section TEXT,
            added_at REAL  -- when it joined its project, for the project's order
        )""")
    columns = "id, owner, name, kind, size, created_at, expires_at, comment_seq, replaced_at, title"
    db.execute(f"INSERT INTO artifacts_new ({columns}) SELECT {columns} FROM artifacts")  # noqa: S608
    db.execute("DROP TABLE artifacts")
    db.execute("ALTER TABLE artifacts_new RENAME TO artifacts")
    db.execute("CREATE INDEX artifacts_owner ON artifacts(owner)")
    db.execute("CREATE INDEX artifacts_expires ON artifacts(expires_at)")
    db.execute("CREATE INDEX artifacts_project ON artifacts(project)")


def _migrate_to_v5(db: sqlite3.Connection) -> None:
    """A key's drafts about one comment, found by an index: the project
    page's counters look them up for every open comment of every file."""
    db.execute("CREATE INDEX drafts_key ON drafts(artifact_id, author_key_hash, of_n)")


def _repair_comment_seq(db: sqlite3.Connection) -> None:
    """A counter behind its comments (added to a database that had comments,
    or comments written by an older version after a rollback) would hand out
    their numbers again. Cheap, and a no-op when nothing is behind."""
    last = "(SELECT MAX(n) FROM comments WHERE artifact_id = artifacts.id)"
    db.execute(
        f"UPDATE artifacts SET comment_seq = {last} "  # noqa: S608 (a constant)
        f"WHERE comment_seq < COALESCE({last}, 0)"
    )


# _MIGRATIONS[i] takes a database from version i (PRAGMA user_version) to
# i + 1. Append only: each runs once, in one transaction with its version bump,
# so a start cut short leaves a version the next start goes on from.
_MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migrate_to_v1,
    _migrate_to_v2,
    _migrate_to_v3,
    _migrate_to_v4,
    _migrate_to_v5,
)


def _migrate(db: sqlite3.Connection) -> None:
    """Run the migrations ``db`` has not had; it is in autocommit mode."""
    while True:
        db.execute("BEGIN IMMEDIATE")  # one start at a time decides
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version >= len(_MIGRATIONS):
                # Up to date, or made by a newer version: left as it is, said
                # in the log, since older code may not understand it.
                if version > len(_MIGRATIONS):
                    log.warning(
                        "the database is at version %d, newer than this code (%d)",
                        version,
                        len(_MIGRATIONS),
                    )
                else:
                    _repair_comment_seq(db)
                db.execute("COMMIT")
                return
            # Already at version 1 without the number: only stamped, so its
            # fixes do not run on today's data again.
            if not (version == 0 and _is_v1(db)):
                _MIGRATIONS[version](db)
            db.execute(f"PRAGMA user_version = {version + 1}")
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise


SETTINGS = ("user_quota_bytes", "max_total_bytes", "ttl_seconds")


@dataclass(frozen=True)
class Artifact:
    id: str
    owner: str
    name: str
    kind: str
    size: int
    created_at: float
    expires_at: float | None  # None: in a project, kept until deleted
    comments: int = 0
    open_comments: int = 0
    updated_at: float = 0.0  # the last upload: replaced, else created
    title: str = ""
    project: str | None = None
    section: str | None = None


# An artifact row with its comment counts, for Artifact(*row).
_ARTIFACT_SQL = (
    "SELECT a.id, a.owner, a.name, a.kind, a.size, a.created_at, a.expires_at, "
    "(SELECT COUNT(*) FROM comments c WHERE c.artifact_id = a.id), "
    "(SELECT COUNT(*) FROM comments c WHERE c.artifact_id = a.id AND c.resolved_at IS NULL), "
    "COALESCE(a.replaced_at, a.created_at), "
    # Rows written by older code after a rollback have no title.
    "COALESCE(a.title, a.name), a.project, a.section "
    "FROM artifacts a"
)

MAX_COMMENTS = 200
MAX_DRAFTS_PER_KEY = 50  # one author key's drafts on one file
MAX_DRAFTS = 200  # all drafts on one file
DRAFT_TTL_SECONDS = 7 * 24 * 3600  # a draft not changed for this long is deleted
# More files missing at one purge than this is taken for a missing folder.
LOST_AT_ONCE = 5
MAX_REPLIES = 100  # replies in one comment's chat
MAX_FILE_REPLIES = 1000  # replies in all the chats of one file

# What the owner agent is doing about a comment; NULL: nothing asked of it.
# waiting: sent to it; working: taken; done, wrote, asked: answered.
AGENT_STATES = ("waiting", "working", "done", "wrote", "asked")
ANSWERED = ("done", "wrote", "asked")  # the states a person can accept
# A comment or a reply carries text; a decision (approve, reject, accept)
# carries only the comment it is about.
DRAFT_KINDS = ("comment", "reply", "approve", "reject", "accept")
DECISIONS = ("approve", "reject", "accept")


@dataclass(frozen=True)
class Reply:
    author: str
    text: str
    created_at: float
    mine: bool
    by_agent: bool


@dataclass(frozen=True)
class Comment:
    n: int
    author: str
    text: str
    anchor: str | None  # JSON
    created_at: float
    resolved_at: float | None
    before_replace: bool
    mine: bool
    approved_by: str | None = None
    rejected_by: str | None = None
    agent: str | None = None  # one of AGENT_STATES
    replies: tuple[Reply, ...] = ()


# :key is the author key hash asking, or NULL: then no comment is its own.
_COMMENT_SQL = (
    "SELECT c.n, c.author, c.text, c.anchor, c.created_at, c.resolved_at, "
    "a.replaced_at IS NOT NULL AND COALESCE(c.written_at, c.created_at) < a.replaced_at, "
    "COALESCE(c.author_key_hash = :key, 0), "
    "c.approved_by, c.rejected_by, c.agent "
    "FROM comments c JOIN artifacts a ON a.id = c.artifact_id "
    "WHERE c.artifact_id = :id AND (a.expires_at IS NULL OR a.expires_at > :now)"
)

# The replies on the artifact :id, oldest first, each marked as :key's or not.
_REPLY_SQL = (
    "SELECT n, author, text, created_at, COALESCE(author_key_hash = :key, 0), by_agent "
    "FROM replies WHERE artifact_id = :id ORDER BY created_at, rowid"
)


@dataclass(frozen=True)
class Draft:
    id: str
    author: str
    text: str
    anchor: str | None  # JSON
    created_at: float
    updated_at: float
    kind: str = "comment"  # one of DRAFT_KINDS
    of_n: int | None = None  # the comment a reply or a decision is about


_DRAFT_COLUMNS = "id, author, text, anchor, created_at, updated_at, kind, of_n"

# One author key's live drafts on a live artifact.
_DRAFT_SQL = (
    "SELECT d.id, d.author, d.text, d.anchor, d.created_at, d.updated_at, d.kind, d.of_n "
    "FROM drafts d JOIN artifacts a ON a.id = d.artifact_id "
    "WHERE d.artifact_id = :id AND d.author_key_hash = :key "
    "AND (a.expires_at IS NULL OR a.expires_at > :now) AND d.updated_at > :stale"
)


# The draft :draft of the author key :key, live on a live artifact.
_OWN_DRAFT = (
    "artifact_id = :id AND id = :draft AND author_key_hash = :key AND updated_at > :stale "
    "AND EXISTS (SELECT 1 FROM artifacts WHERE id = :id "
    "AND (expires_at IS NULL OR expires_at > :now))"
)


class TooManyComments(Exception):
    pass


class TooManyDrafts(Exception):
    pass


class NothingToPublish(Exception):
    pass


class TooManyReplies(Exception):
    pass


class NoSuchComment(Exception):
    """A reply or a decision about a comment that is not open on the file."""


class DraftRefused(Exception):
    """A decision the author key may not make on that comment now."""


class StorageFull(Exception):
    pass


class NoSuchProject(Exception):
    """A project field naming no project of the owner's."""


class SectionWithoutProject(Exception):
    pass


class TooMany(Exception):
    """Over one of the fixed limits on projects, their files or sections."""


MAX_PROJECTS = 100  # per agent
MAX_PROJECT_FILES = 500
MAX_SECTIONS = 50  # per project


@dataclass(frozen=True)
class Project:
    id: str
    owner: str
    title: str
    created_at: float
    files: int
    used_bytes: int


_PROJECT_SQL = (
    "SELECT p.id, p.owner, p.title, p.created_at, COUNT(a.id), COALESCE(SUM(a.size), 0) "
    "FROM projects p LEFT JOIN artifacts a ON a.project = p.id"
)


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
        db = sqlite3.connect(self._db, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            # Off while migrating, as SQLite says for rebuilding a table: a
            # table dropped and made anew (version 4) must not cascade to the
            # rows that point at it. Every other connection turns them on.
            db.execute("PRAGMA foreign_keys = OFF")
            _migrate(db)
        finally:
            db.close()

    @contextmanager
    def _conn(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """A connection; with ``write``, inside one transaction that holds the
        write lock from its first statement, so whatever a method checks
        before it writes still holds when it commits."""
        db = sqlite3.connect(self._db, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
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
        with self._conn(write=True) as db:
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
        with self._conn(write=True) as db:
            db.execute(
                "INSERT INTO agents (name, key_hash, admin) VALUES (?, ?, ?)",
                (name, hash_key(key), int(admin)),
            )
        return key

    def set_admin(self, name: str, admin: bool) -> bool:
        with self._conn(write=True) as db:
            sql = "UPDATE agents SET admin = ? WHERE name = ?"
            return db.execute(sql, (int(admin), name)).rowcount > 0

    def set_quota(self, name: str, quota_bytes: int | None) -> bool:
        """Give ``name`` its own quota, or None for the common one."""
        with self._conn(write=True) as db:
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
                "LEFT JOIN artifacts f ON f.owner = a.name "
                "AND (f.expires_at IS NULL OR f.expires_at > :now) "
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
        with self._conn(write=True) as db:
            db.execute("UPDATE agents SET key_hash = ? WHERE name = ?", (hash_key(key), name))
        return key

    def remove_agent(self, name: str) -> bool:
        with self._conn(write=True) as db:
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
        with self._conn(write=True) as db:
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
        with self._conn(write=True) as db:
            return db.execute("DELETE FROM invites WHERE id = ?", (invite_id,)).rowcount > 0

    def claim_invite(self, token: str) -> tuple[str, str] | None:
        """Use up the invite: create its agent and return (name, key). None if
        the token is unknown, used or expired; NameTaken if the name is in use."""
        key = _new_key()
        with self._conn(write=True) as db:
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
            f"{_ARTIFACT_SQL} WHERE a.id = :id AND (a.expires_at IS NULL OR a.expires_at > :now) "
            "AND (:owner IS NULL OR a.owner = :owner)",
            {"id": artifact_id, "now": self.clock(), "owner": owner},
        ).fetchone()
        return Artifact(*row) if row else None

    def list(self, owner: str) -> list[Artifact]:
        with self._conn() as db:
            rows = db.execute(
                f"{_ARTIFACT_SQL} WHERE a.owner = ? AND (a.expires_at IS NULL OR a.expires_at > ?) "
                "ORDER BY a.created_at",
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
        title: str | None = None,
        project: str | None = None,
        section: str | None = None,
    ) -> Artifact | None:
        """Store a new artifact, or replace ``artifact_id`` if ``owner`` owns
        it. Returns None when there is no such artifact of ``owner``'s.
        Without a ``title`` a new artifact takes its name, a replaced one keeps
        its title; the same for ``project`` and ``section``, which cannot be
        cleared here. Raises what _placed raises, storing nothing."""
        new_id = artifact_id or secrets.token_urlsafe(16)
        tmp = self.files / f".{new_id}.{secrets.token_hex(4)}.tmp"
        with open(tmp, "wb") as out:
            shutil.copyfileobj(src, out)
        now = self.clock()
        live = "(expires_at IS NULL OR expires_at > ?)"
        try:
            with self._conn(write=True) as db:
                # Hold the write lock from the size checks to the insert, so two
                # uploads cannot both fit into the same free space.
                settings = self.settings(db)
                ttl = settings["ttl_seconds"]
                quota = self._quota_for(owner, db)
                if quota is not None:
                    own = db.execute(
                        f"SELECT COALESCE(SUM(size), 0) FROM artifacts "  # noqa: S608
                        f"WHERE owner = ? AND id != ? AND {live}",
                        (owner, new_id, now),
                    ).fetchone()[0]
                    if own + size > quota:
                        raise QuotaExceeded(own, quota)
                used = db.execute(
                    f"SELECT COALESCE(SUM(size), 0) FROM artifacts WHERE id != ? AND {live}",  # noqa: S608
                    (new_id, now),
                ).fetchone()[0]
                if used + size > settings["max_total_bytes"]:
                    raise StorageFull
                old = (None, None, None)
                if artifact_id is not None:
                    old = db.execute(
                        f"SELECT project, section, added_at FROM artifacts "  # noqa: S608
                        f"WHERE id = ? AND owner = ? AND {live}",
                        (new_id, owner, now),
                    ).fetchone()
                    if old is None:
                        return None
                given = {"project": project} if project is not None else {}
                if section is not None:
                    given["section"] = section
                placed = self._placed(db, owner, new_id, old, given, now)
                expires = None if placed[0] is not None else now + ttl
                if artifact_id is None:
                    db.execute(
                        "INSERT INTO artifacts (id, owner, name, title, kind, size, created_at, "
                        "expires_at, project, section, added_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (new_id, owner, name, title or name, kind, size, now, expires, *placed),
                    )
                else:
                    # Read under the lock, so a comment posted before this
                    # replace always has an earlier created_at.
                    db.execute(
                        "UPDATE artifacts SET name = ?, title = COALESCE(?, title), kind = ?, "
                        "size = ?, expires_at = ?, replaced_at = ?, project = ?, section = ?, "
                        "added_at = ? WHERE id = ?",
                        (name, title, kind, size, expires, self.clock(), *placed, new_id),
                    )
                db.execute("UPDATE agents SET last_upload_at = ? WHERE name = ?", (now, owner))
                artifact = self._artifact(db, new_id)
                os.replace(tmp, self.files / new_id)
        finally:
            tmp.unlink(missing_ok=True)
        return artifact

    def edit(self, artifact_id: str, *, owner: str, changes: dict) -> Artifact | None:
        """Change a live artifact's ``title``, ``project`` or ``section``
        (the keys of ``changes``; None clears the last two) without uploading
        it again; None when ``owner`` has no such artifact. Raises what
        _placed raises."""
        now = self.clock()
        with self._conn(write=True) as db:
            row = db.execute(
                "SELECT project, section, added_at, expires_at FROM artifacts "
                "WHERE id = ? AND owner = ? AND (expires_at IS NULL OR expires_at > ?)",
                (artifact_id, owner, now),
            ).fetchone()
            if row is None:
                return None
            placed = self._placed(db, owner, artifact_id, row[:3], changes, now)
            expires = row[3]
            if placed[0] is not None:
                expires = None
            elif row[0] is not None:  # taken out: a new lifetime from now
                expires = now + self.settings(db)["ttl_seconds"]
            db.execute(
                "UPDATE artifacts SET title = COALESCE(?, title), expires_at = ?, project = ?, "
                "section = ?, added_at = ? WHERE id = ?",
                (changes.get("title"), expires, *placed, artifact_id),
            )
            return self._artifact(db, artifact_id)

    def _placed(
        self,
        db: sqlite3.Connection,
        owner: str,
        artifact_id: str,
        old: tuple,
        given: dict,
        now: float,
    ) -> tuple[str | None, str | None, float | None]:
        """The (project, section, added_at) a file has after ``given``
        changes to its ``old`` ones. Moving keeps the section unless one is
        given; out of a project there is none. Raises SectionWithoutProject,
        NoSuchProject (not ``owner``'s), TooMany."""
        project, section, added_at = old
        if "project" in given and given["project"] != project:
            project = given["project"]
            added_at = None if project is None else now
            section = section if project is not None else None
        if "section" in given:
            section = given["section"]
        if project is None:
            if section is not None:
                raise SectionWithoutProject
            return None, None, None
        sql = "SELECT 1 FROM projects WHERE id = ? AND owner = ?"
        if db.execute(sql, (project, owner)).fetchone() is None:
            raise NoSuchProject
        files, same, sections = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(section = :section), 0), COUNT(DISTINCT section) "
            "FROM artifacts WHERE project = :project AND id != :id",
            {"project": project, "section": section, "id": artifact_id},
        ).fetchone()
        if files >= MAX_PROJECT_FILES:
            raise TooMany(f"a project holds at most {MAX_PROJECT_FILES} files")
        if section is not None and not same and sections >= MAX_SECTIONS:
            raise TooMany(f"a project holds at most {MAX_SECTIONS} sections")
        return project, section, added_at

    # Projects; their files go with them (ON DELETE CASCADE).

    def add_project(self, owner: str, title: str) -> Project:
        """Raises TooMany past MAX_PROJECTS."""
        project = Project(secrets.token_urlsafe(16), owner, title, self.clock(), 0, 0)
        with self._conn(write=True) as db:
            sql = "SELECT COUNT(*) FROM projects WHERE owner = ?"
            if db.execute(sql, (owner,)).fetchone()[0] >= MAX_PROJECTS:
                raise TooMany(f"an agent has at most {MAX_PROJECTS} projects")
            db.execute(
                "INSERT INTO projects (id, owner, title, created_at) VALUES (?, ?, ?, ?)",
                (project.id, owner, title, project.created_at),
            )
        return project

    def projects(self, owner: str) -> list[Project]:
        with self._conn() as db:
            rows = db.execute(
                f"{_PROJECT_SQL} WHERE p.owner = ? GROUP BY p.id ORDER BY p.created_at, p.id",
                (owner,),
            ).fetchall()
        return [Project(*r) for r in rows]

    def _project(
        self, db: sqlite3.Connection, project_id: str, owner: str | None
    ) -> Project | None:
        row = db.execute(
            f"{_PROJECT_SQL} WHERE p.id = :id AND (:owner IS NULL OR p.owner = :owner) "
            "GROUP BY p.id",
            {"id": project_id, "owner": owner},
        ).fetchone()
        return Project(*row) if row else None

    def project(self, project_id: str, *, owner: str) -> tuple[Project, list[Artifact]] | None:
        """``owner``'s project and its files, in the project page's order."""
        return self._project_files(project_id, owner)

    def public_project(self, project_id: str) -> tuple[Project, list[Artifact]] | None:
        """Anyone's project and its files, for its page: whoever has the link
        may see it. Kept apart from ``project`` so an API call cannot drop
        the owner check by leaving it out."""
        return self._project_files(project_id, None)

    def _project_files(
        self, project_id: str, owner: str | None
    ) -> tuple[Project, list[Artifact]] | None:
        """A project (``owner``'s only, unless None) and its files in the
        project page's order: those without a section first, then each
        section by when its earliest file was added; inside, by when added;
        ties by id."""
        with self._conn() as db:
            project = self._project(db, project_id, owner)
            if project is None:
                return None
            rows = db.execute(
                f"{_ARTIFACT_SQL} WHERE a.project = ? ORDER BY a.added_at, a.id",  # noqa: S608
                (project_id,),
            ).fetchall()
            # When each section's earliest file was added: one grouped
            # query, not a subquery per row.
            first = dict(
                db.execute(
                    "SELECT section, MIN(added_at) FROM artifacts "
                    "WHERE project = ? AND section IS NOT NULL GROUP BY section",
                    (project_id,),
                ).fetchall()
            )
        files = [Artifact(*r) for r in rows]
        loose = [a for a in files if a.section is None]
        sections: dict[str, list[Artifact]] = {}
        for a in files:
            if a.section is not None:
                sections.setdefault(a.section, []).append(a)
        order = sorted(sections, key=lambda s: (first[s], s))
        return project, loose + [a for s in order for a in sections[s]]

    def project_summary(self, project_id: str, key_hash: str | None) -> dict | None:
        """For the project page's dot: per file of the project, the open
        comments waiting for the person with ``key_hash`` (None: a stranger)
        and that key's live drafts, its unfinished review; None if there is
        no such project. Waiting: the agent answered (done, wrote, asked), or
        a colleague's comment awaits approval; less those this key already
        decided or replied to in a live draft."""
        now = self.clock()
        args = {"p": project_id, "key": key_hash, "now": now, "stale": now - DRAFT_TTL_SECONDS}
        with self._conn() as db:
            if db.execute("SELECT 1 FROM projects WHERE id = :p", args).fetchone() is None:
                return None
            rows = db.execute(
                "SELECT a.id, "
                # A file without open comments joins one row of NULLs: c.n tells.
                "COALESCE(SUM(c.n IS NOT NULL AND (c.agent IN ('done', 'wrote', 'asked') "
                "OR (c.agent IS NULL "
                "AND NOT COALESCE(c.author_key_hash = :key, 0) AND c.approved_by IS NULL "
                "AND c.rejected_by IS NULL)) AND (:key IS NULL OR NOT EXISTS (SELECT 1 FROM drafts d "
                "WHERE d.artifact_id = c.artifact_id AND d.author_key_hash = :key "
                "AND d.of_n = c.n AND d.kind != 'comment' "
                "AND d.updated_at > :stale))), 0), "
                # Without a key, = NULL matches no draft. A reply or decision
                # about a comment since closed is not counted: the viewer
                # hides it, so it is nothing to finish.
                "(SELECT COUNT(*) FROM drafts d WHERE d.artifact_id = a.id "
                "AND d.author_key_hash = :key AND d.updated_at > :stale "
                "AND (d.kind = 'comment' OR EXISTS (SELECT 1 FROM comments c2 "
                "WHERE c2.artifact_id = d.artifact_id AND c2.n = d.of_n "
                "AND c2.resolved_at IS NULL))) "
                "FROM artifacts a LEFT JOIN comments c "
                "ON c.artifact_id = a.id AND c.resolved_at IS NULL "
                "WHERE a.project = :p AND (a.expires_at IS NULL OR a.expires_at > :now) "
                "GROUP BY a.id",
                args,
            ).fetchall()
        return {r[0]: {"waiting_you": r[1], "drafts": r[2]} for r in rows}

    def agent_summary(
        self, project_id: str, *, owner: str, since: float | None
    ) -> tuple[float, list[tuple[str, str, int, int]]] | None:
        """For the owner agent's ``wait`` on a whole project, in one query:
        the time now and, per live file of ``owner``'s project in the order
        added, its id, title, open comments waiting for the agent and
        comments resolved after ``since`` (none without it); None if there
        is no such project of ``owner``'s."""
        now = self.clock()
        since = now if since is None else since
        args = {"p": project_id, "owner": owner, "now": now, "since": since}
        with self._conn() as db:
            sql = "SELECT 1 FROM projects WHERE id = :p AND owner = :owner"
            if db.execute(sql, args).fetchone() is None:
                return None
            rows = db.execute(
                "SELECT a.id, COALESCE(a.title, a.name), "
                "COALESCE(SUM(c.agent = 'waiting' AND c.resolved_at IS NULL), 0), "
                "COALESCE(SUM(c.resolved_at > :since), 0) "
                "FROM artifacts a LEFT JOIN comments c ON c.artifact_id = a.id "
                "WHERE a.project = :p AND (a.expires_at IS NULL OR a.expires_at > :now) "
                "GROUP BY a.id ORDER BY a.added_at, a.id",
                args,
            ).fetchall()
        return now, [tuple(r) for r in rows]

    def rename_project(self, project_id: str, *, owner: str, title: str) -> Project | None:
        with self._conn(write=True) as db:
            db.execute(
                "UPDATE projects SET title = ? WHERE id = ? AND owner = ?",
                (title, project_id, owner),
            )
            return self._project(db, project_id, owner)

    def delete_project(self, project_id: str, *, owner: str) -> bool:
        """The project, its files and their comments: the rows in one
        transaction, then the files."""
        with self._conn(write=True) as db:
            sql = "SELECT id FROM artifacts WHERE project = ?"
            ids = [r[0] for r in db.execute(sql, (project_id,))]
            sql = "DELETE FROM projects WHERE id = ? AND owner = ?"
            gone = db.execute(sql, (project_id, owner)).rowcount > 0
        if gone:
            for artifact_id in ids:
                self._unlink(artifact_id)
        return gone

    # Comments and drafts; they go with their artifact (ON DELETE CASCADE).

    def comments(
        self, artifact_id: str, *, owner: str | None = None, key_hash: str | None = None
    ) -> tuple[float, list[Comment]] | None:
        """When a live artifact was last uploaded and its comments, oldest
        first; None if there is no such artifact (of ``owner``'s). The time
        comes from the same lookup, read before the comments, so a replace in
        between shows on the next poll. The owner gets them all; anyone else
        gets the open ones, each marked as ``key_hash``'s or not."""
        with self._conn() as db:
            artifact = self._artifact(db, artifact_id, owner)
            if artifact is None:
                return None
            args = {"id": artifact_id, "now": self.clock(), "key": key_hash}
            if owner is not None:
                return artifact.updated_at, _load(db, "ORDER BY c.n", args)
            return artifact.updated_at, _load(db, "AND c.resolved_at IS NULL ORDER BY c.n", args)

    def resolve_comment(
        self, artifact_id: str, n: int, resolved: bool, *, owner: str
    ) -> Comment | None:
        """Mark a comment resolved or open."""
        with self._conn(write=True) as db:
            if self._artifact(db, artifact_id, owner) is None:
                return None
            db.execute(
                "UPDATE comments SET resolved_at = CASE WHEN :resolved "
                "THEN COALESCE(resolved_at, :now) END WHERE artifact_id = :id AND n = :n",
                {"id": artifact_id, "n": n, "resolved": resolved, "now": self.clock()},
            )
            return self._comment(db, artifact_id, n)

    def delete_comment(self, artifact_id: str, n: int, *, owner: str) -> bool:
        with self._conn(write=True) as db:
            if self._artifact(db, artifact_id, owner) is None:
                return False
            sql = "DELETE FROM comments WHERE artifact_id = ? AND n = ?"
            return db.execute(sql, (artifact_id, n)).rowcount > 0

    def _comment(self, db: sqlite3.Connection, artifact_id: str, n: int) -> Comment | None:
        args = {"id": artifact_id, "now": self.clock(), "n": n, "key": None}
        found = _load(db, "AND c.n = :n", args)
        return found[0] if found else None

    def take(self, artifact_id: str, *, owner: str) -> list[Comment] | None:
        """The owner agent takes every open comment waiting for it: they
        become working. Returns those; None if there is no such artifact."""
        with self._conn(write=True) as db:
            if self._artifact(db, artifact_id, owner) is None:
                return None
            args = {"id": artifact_id, "now": self.clock(), "key": None}
            taken = db.execute(
                "UPDATE comments SET agent = 'working' WHERE artifact_id = :id "
                "AND agent = 'waiting' AND resolved_at IS NULL RETURNING n",
                args,
            ).fetchall()
            if not taken:
                return []
            numbers = ", ".join(str(r[0]) for r in taken)
            return _load(db, f"AND c.n IN ({numbers}) ORDER BY c.n", args)

    def agent_reply(
        self, artifact_id: str, n: int, text: str, state: str, *, owner: str
    ) -> Comment | None:
        """The owner agent's reply in comment ``n``'s chat, setting its
        agent state; None if there is no such comment. Raises TooManyReplies."""
        assert state in ANSWERED, state
        with self._conn(write=True) as db:
            if self._artifact(db, artifact_id, owner) is None:
                return None
            args = {"id": artifact_id, "n": n, "now": self.clock(), "state": state}
            if not db.execute(
                "SELECT 1 FROM comments WHERE artifact_id = :id AND n = :n", args
            ).fetchone():
                return None
            self._check_replies(db, artifact_id, [n])
            db.execute(
                "INSERT INTO replies (artifact_id, n, author, text, author_key_hash, created_at, "
                "by_agent) VALUES (:id, :n, 'agent', :text, NULL, :now, 1)",
                args | {"text": text},
            )
            db.execute(
                "UPDATE comments SET agent = :state WHERE artifact_id = :id AND n = :n", args
            )
            return self._comment(db, artifact_id, n)

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
        self,
        artifact_id: str,
        key_hash: str,
        *,
        author: str,
        text: str,
        anchor: str | None,
        kind: str = "comment",
        of_n: int | None = None,
    ) -> Draft | None:
        """A new draft; None if the artifact is gone. A reply or a decision
        is about the open comment ``of_n``; a decision replaces the key's
        earlier one on that comment. Raises TooManyDrafts, NoSuchComment,
        DraftRefused."""
        assert kind in DRAFT_KINDS and (of_n is None) == (kind == "comment"), (kind, of_n)
        with self._conn(write=True) as db:
            if self._artifact(db, artifact_id) is None:
                return None
            return self._add_draft(db, artifact_id, key_hash, author, text, anchor, kind, of_n)

    def add_decisions(
        self, artifact_id: str, key_hash: str, *, author: str, decisions: list[tuple[str, int]]
    ) -> list[Draft] | None:
        """Decision drafts ``(kind, of_n)`` in one go, each as add_draft makes
        it: all of them or, if one is refused, none. None if the artifact is
        gone. Raises what add_draft raises."""
        assert all(kind in DECISIONS for kind, _ in decisions), decisions
        with self._conn(write=True) as db:
            if self._artifact(db, artifact_id) is None:
                return None
            # An exception leaves the transaction, which rolls the earlier ones back.
            return [
                self._add_draft(db, artifact_id, key_hash, author, "", None, kind, of_n)
                for kind, of_n in decisions
            ]

    def _add_draft(
        self,
        db: sqlite3.Connection,
        artifact_id: str,
        key_hash: str,
        author: str,
        text: str,
        anchor: str | None,
        kind: str,
        of_n: int | None,
    ) -> Draft:
        args = self._draft_args(artifact_id, key_hash, of=of_n)
        if of_n is not None:
            about = db.execute(
                "SELECT author_key_hash = :key, agent FROM comments "
                "WHERE artifact_id = :id AND n = :of AND resolved_at IS NULL",
                args,
            ).fetchone()
            if about is None:
                raise NoSuchComment
            own, agent = about
            if kind in ("approve", "reject") and own:
                raise DraftRefused(f"you cannot {kind} your own comment")
            if kind == "accept" and agent not in ANSWERED:
                raise DraftRefused("the agent has not answered this comment yet")
            if kind in DECISIONS:
                db.execute(
                    "DELETE FROM drafts WHERE artifact_id = :id AND author_key_hash = :key "
                    "AND of_n = :of AND kind IN ('approve', 'reject', 'accept')",
                    args,
                )
        # Expired drafts make room before they are counted.
        db.execute("DELETE FROM drafts WHERE artifact_id = :id AND updated_at <= :stale", args)
        mine, total = db.execute(
            "SELECT COALESCE(SUM(author_key_hash = :key), 0), COUNT(*) FROM drafts "
            "WHERE artifact_id = :id",
            args,
        ).fetchone()
        if mine >= MAX_DRAFTS_PER_KEY or total >= MAX_DRAFTS:
            raise TooManyDrafts
        now = args["now"]
        draft = Draft(secrets.token_urlsafe(8), author, text, anchor, now, now, kind, of_n)
        db.execute(
            "INSERT INTO drafts (artifact_id, id, author_key_hash, author, text, anchor, "
            "created_at, updated_at, kind, of_n) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, draft.id, key_hash, author, text, anchor, now, now, kind, of_n),
        )
        return draft

    def edit_draft(self, artifact_id: str, key_hash: str, draft_id: str, text: str) -> Draft | None:
        """Change the text of a comment or reply draft; None if there is no
        such draft of ``key_hash``'s, or it is a decision, which has no text."""
        with self._conn(write=True) as db:
            row = db.execute(
                f"UPDATE drafts SET text = :text, updated_at = :now WHERE {_OWN_DRAFT} "  # noqa: S608
                f"AND kind IN ('comment', 'reply') RETURNING {_DRAFT_COLUMNS}",
                self._draft_args(artifact_id, key_hash, draft=draft_id, text=text),
            ).fetchone()
        return Draft(*row) if row else None

    def delete_draft(self, artifact_id: str, key_hash: str, draft_id: str) -> bool:
        with self._conn(write=True) as db:
            args = self._draft_args(artifact_id, key_hash, draft=draft_id)
            sql = f"DELETE FROM drafts WHERE {_OWN_DRAFT}"  # noqa: S608 (a constant)
            return db.execute(sql, args).rowcount > 0

    def delete_drafts(self, artifact_id: str, key_hash: str, draft_ids: list[str]) -> bool:
        """Delete those of ``draft_ids`` that are ``key_hash``'s drafts, in one
        go; the others are left alone. False if there is no such artifact."""
        with self._conn(write=True) as db:
            if self._artifact(db, artifact_id) is None:
                return False
            args = self._draft_args(artifact_id, key_hash)
            db.executemany(
                "DELETE FROM drafts WHERE artifact_id = :id AND author_key_hash = :key "
                "AND id = :draft",
                [args | {"draft": d} for d in draft_ids],
            )
        return True

    def drop_drafts(self, artifact_id: str, *, owner: str) -> bool:
        """Delete every draft on the owner's file, unread; False if there is
        no such artifact of ``owner``'s."""
        with self._conn(write=True) as db:
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
        """Finish ``key_hash``'s review, all or nothing. Its comment drafts on
        the artifact, in the order made, then ``text`` as a comment about the
        whole file, become comments with the next numbers; its replies join
        their comments' chats; its decisions take effect: approve and reject
        record the draft's author (the later one clears the other; reject
        also takes the comment off the agent's work), accept resolves the
        comment. Replies and decisions about a comment no longer open are
        dropped. When ``for_agent`` (the review is sent to the agent), the new
        comments and those replied to or approved go to the agent: agent
        "waiting". An accept takes
        effect only on a comment the agent has answered, after the rest of the
        review, and not on one this review replies to (a reply asks for more).

        Returns the comments made or touched and still open, by number; None
        if the artifact is gone. Raises NothingToPublish, TooManyComments,
        TooManyReplies."""
        with self._conn(write=True) as db:
            artifact = self._artifact(db, artifact_id)
            if artifact is None:
                return None
            # The file's type now: a draft pinned to a type it no longer has
            # (replaced since) becomes a comment about the whole file.
            pin = kinds.get(artifact.kind).pin
            args = self._draft_args(artifact_id, key_hash)
            drafts = db.execute(
                "SELECT d.rowid, d.author, d.text, d.anchor, d.created_at, d.kind, d.of_n "
                "FROM drafts d WHERE d.artifact_id = :id AND d.author_key_hash = :key "
                "AND d.updated_at > :stale ORDER BY d.created_at, d.rowid",
                args,
            ).fetchall()
            if not drafts and text is None:
                raise NothingToPublish
            items = [
                (d_author, d_text, d_anchor if _anchor_fits(d_anchor, pin) else None, written)
                for _, d_author, d_text, d_anchor, written, kind, _ in drafts
                if kind == "comment"
            ]
            if text is not None:
                items.append((author, text, None, args["now"]))
            if artifact.comments + len(items) > MAX_COMMENTS:
                raise TooManyComments
            open_n = {
                r[0]
                for r in db.execute(
                    "SELECT n FROM comments WHERE artifact_id = :id AND resolved_at IS NULL", args
                )
            }
            about = [
                (kind, of_n, d_author, d_text)
                for _, d_author, d_text, _, _, kind, of_n in drafts
                if kind != "comment" and of_n in open_n
            ]
            replies = [(of_n, a, t) for kind, of_n, a, t in about if kind == "reply"]
            self._check_replies(db, artifact_id, [of_n for of_n, _, _ in replies])
            touched: set[int] = set()
            if items:
                (last,) = db.execute(
                    "UPDATE artifacts SET comment_seq = comment_seq + ? WHERE id = ? "
                    "RETURNING comment_seq",
                    (len(items), artifact_id),
                ).fetchone()
                first = last - len(items) + 1
                db.executemany(
                    "INSERT INTO comments (artifact_id, n, author, text, anchor, "
                    "author_key_hash, created_at, written_at, agent) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (artifact_id, first + i, a, t, anchor, key_hash, args["now"], written)
                        + ("waiting" if for_agent else None,)
                        for i, (a, t, anchor, written) in enumerate(items)
                    ],
                )
                touched.update(range(first, last + 1))
            db.executemany(
                "INSERT INTO replies (artifact_id, n, author, text, author_key_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(artifact_id, n, a, t, key_hash, args["now"]) for n, a, t in replies],
            )
            replied = {n for n, _, _ in replies}
            to_agent = set(replied)
            accepts = []
            for kind, n, d_author, _ in about:
                if kind == "approve":
                    sql = "approved_by = :name, rejected_by = NULL"
                    to_agent.add(n)
                elif kind == "reject":
                    sql = "approved_by = NULL, rejected_by = :name, agent = NULL"
                    to_agent.discard(n)
                else:
                    if kind == "accept" and n not in replied:
                        accepts.append(n)
                    continue
                db.execute(
                    f"UPDATE comments SET {sql} WHERE artifact_id = :id AND n = :n",  # noqa: S608
                    args | {"n": n, "name": d_author},
                )
                touched.add(n)
            touched |= to_agent
            if for_agent and to_agent:
                db.executemany(
                    "UPDATE comments SET agent = 'waiting' "
                    "WHERE artifact_id = ? AND n = ? AND resolved_at IS NULL",
                    [(artifact_id, n) for n in to_agent],
                )
            # Last, on the state the rest left: someone may have sent the
            # comment back to the agent since the accept was drafted.
            db.executemany(
                "UPDATE comments SET resolved_at = COALESCE(resolved_at, ?) "
                "WHERE artifact_id = ? AND n = ? AND agent IN ('done', 'wrote', 'asked')",
                [(args["now"], artifact_id, n) for n in accepts],
            )
            db.executemany("DELETE FROM drafts WHERE rowid = ?", [(d[0],) for d in drafts])
            if not touched:
                return []
            numbers = ", ".join(str(n) for n in sorted(touched))
            return _load(db, f"AND c.n IN ({numbers}) AND c.resolved_at IS NULL ORDER BY c.n", args)

    def _check_replies(self, db: sqlite3.Connection, artifact_id: str, numbers: list[int]) -> None:
        """Raise TooManyReplies if adding a reply to each of ``numbers`` (one
        per mention) would pass MAX_REPLIES on any comment or
        MAX_FILE_REPLIES on the file."""
        if not numbers:
            return
        (total,) = db.execute(
            "SELECT COUNT(*) FROM replies WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if total + len(numbers) > MAX_FILE_REPLIES:
            raise TooManyReplies
        for n in set(numbers):
            (have,) = db.execute(
                "SELECT COUNT(*) FROM replies WHERE artifact_id = ? AND n = ?", (artifact_id, n)
            ).fetchone()
            if have + numbers.count(n) > MAX_REPLIES:
                raise TooManyReplies

    def delete(self, artifact_id: str, *, owner: str) -> bool:
        with self._conn(write=True) as db:
            sql = "DELETE FROM artifacts WHERE id = ? AND owner = ?"
            gone = db.execute(sql, (artifact_id, owner)).rowcount > 0
        if gone:
            self._unlink(artifact_id)
        return gone

    def purge_expired(self) -> int:
        """Removes expired artifacts and those whose file is lost; returns
        how many."""
        with self._conn(write=True) as db:
            # A file in a project (expires_at NULL) never matches.
            expired = db.execute("SELECT id FROM artifacts WHERE expires_at <= ?", (self.clock(),))
            ids = [r[0] for r in expired]
            db.executemany("DELETE FROM artifacts WHERE id = ?", [(i,) for i in ids])
            # Rows whose file is gone (a database restored without its files)
            # would be a 404 that counts toward the quota for good; they go
            # with their comments. Looked for under the write lock: an upload
            # puts its file in place before its row commits, so a committed
            # row without a file never gets it back.
            rows = db.execute("SELECT id, owner, name FROM artifacts").fetchall()
            lost = [r for r in rows if self._lost(r[0])]
            # Many at once is a files folder not (yet) there — a restore in
            # progress, an unmounted volume — not lost files: nothing goes,
            # and the log says so.
            if len(lost) > LOST_AT_ONCE:
                log.error(
                    "%d of %d files are missing; none removed: check the files folder",
                    len(lost),
                    len(rows),
                )
                lost = []
            db.executemany("DELETE FROM artifacts WHERE id = ?", [(r[0],) for r in lost])
            db.execute(
                "DELETE FROM drafts WHERE updated_at <= ?", (self.clock() - DRAFT_TTL_SECONDS,)
            )
        # The owner and the name, not the id: that is the link.
        for _, owner, name in lost:
            log.warning("removed %s's %r: its file is missing", owner, name)
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
        return len(ids) + len(lost)

    def _lost(self, artifact_id: str) -> bool:
        """Only a file that is not there: a permission or I/O error keeps the row."""
        try:
            os.stat(self.path(artifact_id))
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False

    def path(self, artifact_id: str) -> Path:
        return self.files / artifact_id

    def _unlink(self, artifact_id: str) -> None:
        self.path(artifact_id).unlink(missing_ok=True)


def _load(db: sqlite3.Connection, where: str, args: dict) -> list[Comment]:
    """The comments ``_COMMENT_SQL`` + ``where`` selects, with their replies."""
    rows = db.execute(f"{_COMMENT_SQL} {where}", args).fetchall()
    replies: dict[int, list[Reply]] = {}
    if rows:
        for n, author, text, created_at, mine, by_agent in db.execute(_REPLY_SQL, args):
            replies.setdefault(n, []).append(
                Reply(author, text, created_at, bool(mine), bool(by_agent))
            )
    return [_comment(r, tuple(replies.get(r[0], ()))) for r in rows]


def _comment(row: tuple, replies: tuple[Reply, ...] = ()) -> Comment:
    *fields, before_replace, mine, approved_by, rejected_by, agent = row
    return Comment(
        *fields,
        before_replace=bool(before_replace),
        mine=bool(mine),
        approved_by=approved_by,
        rejected_by=rejected_by,
        agent=agent,
        replies=replies,
    )


def _anchor_fits(anchor: str | None, pin: str | None) -> bool:
    """Whether a stored anchor is of the type the file's pins take now."""
    return anchor is not None and pin is not None and json.loads(anchor).get("type") == pin
