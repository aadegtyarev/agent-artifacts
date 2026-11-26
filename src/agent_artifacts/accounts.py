"""Account ownership and browser credentials, separate from agent keys.

The store supplies transactions on the metadata database so claiming an agent,
consuming a pairing code and creating a device can each be atomic. Never
infer an owner from agent names, file links, email or unsigned profile data.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal

from .credentials import NAME_RE, hash_key, is_revoked, new_key, revoked_key_marker

CLAIM_TTL = 10 * 60
PAIR_TTL = 5 * 60
DEVICE_TTL = 30 * 24 * 60 * 60
MAX_DEVICES = 20
MAX_AGENTS = 50
PAIR_TRIES_PER_CLIENT = 12
PAIR_TRIES_TOTAL = 120
_RATE_WINDOW = 60
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE = re.compile(rf"[{_CODE_ALPHABET}]{{8}}")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")

SCHEMA = """
CREATE TABLE human_accounts (
    id TEXT PRIMARY KEY NOT NULL,
    google_subject TEXT UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE account_agents (
    agent TEXT PRIMARY KEY NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES human_accounts(id) ON DELETE CASCADE
);
CREATE INDEX account_agents_owner ON account_agents(account_id);
CREATE TABLE account_claims (
    agent TEXT PRIMARY KEY NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    credential_hash TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE account_devices (
    id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL REFERENCES human_accounts(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    UNIQUE(id, account_id)
);
CREATE INDEX account_devices_owner ON account_devices(account_id, expires_at);
CREATE TABLE account_pairings (
    account_id TEXT PRIMARY KEY NOT NULL REFERENCES human_accounts(id) ON DELETE CASCADE,
    issuer_id TEXT NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL,
    FOREIGN KEY (issuer_id, account_id) REFERENCES account_devices(id, account_id) ON DELETE CASCADE
);
CREATE TABLE account_pair_limit (
    scope TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    PRIMARY KEY (scope, window_start)
);
CREATE TABLE account_pair_salt (id INTEGER PRIMARY KEY CHECK (id = 1), salt BLOB NOT NULL);
CREATE TABLE account_recovery (
    account_id TEXT PRIMARY KEY NOT NULL REFERENCES human_accounts(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE
);
"""


def _digest(purpose: bytes, value: str) -> str:
    return hashlib.sha256(b"aa-account-v1\0" + purpose + b"\0" + value.encode()).hexdigest()


def _valid_token(token: str | None) -> bool:
    return bool(token and _TOKEN.fullmatch(token))


def _code_hash(code: str, salt: bytes) -> str:
    return hashlib.scrypt(code.encode("ascii"), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


class ClaimUnavailable(ValueError):
    """No current claim for this agent credential, or it already has an owner."""


class AccountFull(ValueError):
    """The account already has the maximum number of agents."""


class AgentNameUnavailable(ValueError):
    """An agent cannot be created or re-keyed under this name."""


@dataclass(frozen=True)
class LinkedAgent:
    name: str
    active: bool
    admin: bool


@dataclass(frozen=True)
class Device:
    id: str
    account_id: str
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class ClaimResult:
    account_id: str
    device_token: str | None  # issued only when the non-Google account is first created


@dataclass(frozen=True)
class PairResult:
    status: Literal["connected", "invalid", "limited", "full"]
    device_token: str | None = None


class Accounts:
    def __init__(
        self, connect: Callable[..., AbstractContextManager[sqlite3.Connection]],
        clock: Callable[[], float],
    ) -> None:
        self._conn, self._clock = connect, clock

    def google_account(self, subject: str) -> str:
        """Only call with the subject from a verified Google browser session."""
        with self._conn() as db:
            row = db.execute("SELECT id FROM human_accounts WHERE google_subject = ?", (subject,)).fetchone()
        if row:
            return row[0]
        with self._conn(write=True) as db:
            account_id = secrets.token_urlsafe(16)
            db.execute("INSERT INTO human_accounts VALUES (?, ?, ?) ON CONFLICT(google_subject) DO NOTHING",
                       (account_id, subject, self._clock()))
            return db.execute("SELECT id FROM human_accounts WHERE google_subject = ?", (subject,)).fetchone()[0]

    def google_subject_for_agent(self, authenticated_agent: str, credential_hash: str) -> str | None:
        """Resolve the link only while the credential still belongs to that agent."""
        with self._conn() as db:
            row = db.execute(
                "SELECT account.google_subject FROM account_agents AS owned "
                "JOIN human_accounts AS account ON account.id = owned.account_id "
                "JOIN agents AS agent ON agent.name = owned.agent "
                "WHERE agent.name = ? AND agent.key_hash = ?",
                (authenticated_agent, credential_hash),
            ).fetchone()
        return row[0] if row else None

    def linked_agents(self, account_id: str) -> list[LinkedAgent]:
        with self._conn() as db:
            return [LinkedAgent(name, not is_revoked(digest), bool(admin)) for name, digest, admin in db.execute(
                "SELECT a.name, a.key_hash, a.admin FROM account_agents own JOIN agents a ON a.name = own.agent "
                "WHERE own.account_id = ? ORDER BY a.name", (account_id,)
            )]

    def agent_names(self, account_id: str) -> list[str]:
        return [agent.name for agent in self.linked_agents(account_id)]

    def bound_account(self, db: sqlite3.Connection, device_token: str | None,
                      google_subject: str | None) -> str | None:
        """Recheck account binding inside an account-authorized transaction."""
        if google_subject is not None and device_token is None:
            row = db.execute("SELECT id FROM human_accounts WHERE google_subject = ?", (google_subject,)).fetchone()
            return row[0] if row else None
        if google_subject is None and device_token is not None:
            device = self._device(db, device_token)
            return device.account_id if device else None
        return None

    def issue_agent_key(self, name: str, *, device_token: str | None = None,
                        google_subject: str | None = None) -> str | None:
        """Create a new linked agent, or issue a fresh key for a revoked one.

        Authorization and the write are in the same metadata transaction.
        Workspace callers pass only a subject already verified by middleware.
        Existing active keys and admin keys cannot be rotated here.
        """
        if not re.fullmatch(NAME_RE, name):
            raise AgentNameUnavailable
        with self._conn(write=True) as db:
            account_id = self.bound_account(db, device_token, google_subject)
            if account_id is None:
                return None
            found = db.execute(
                "SELECT a.key_hash, a.admin, own.account_id FROM agents a LEFT JOIN account_agents own "
                "ON own.agent = a.name WHERE a.name = ?", (name,),
            ).fetchone()
            key = new_key()
            digest = hash_key(key)
            if found:
                old, admin, owner = found
                if owner != account_id or admin or not is_revoked(old):
                    raise AgentNameUnavailable
                db.execute("UPDATE agents SET key_hash = ? WHERE name = ?", (digest, name))
            else:
                if db.execute("SELECT 1 FROM invites WHERE name = ? AND expires_at > ?",
                              (name, self._clock())).fetchone():
                    raise AgentNameUnavailable
                count = db.execute("SELECT count(*) FROM account_agents WHERE account_id = ?", (account_id,)).fetchone()[0]
                if count >= MAX_AGENTS:
                    raise AccountFull
                try:
                    db.execute("INSERT INTO agents (name, key_hash) VALUES (?, ?)", (name, digest))
                except sqlite3.IntegrityError:
                    raise AgentNameUnavailable from None
                db.execute("INSERT INTO account_agents VALUES (?, ?)", (name, account_id))
        return key

    def revoke_agent_key(self, name: str, *, device_token: str | None = None,
                         google_subject: str | None = None) -> bool:
        with self._conn(write=True) as db:
            account_id = self.bound_account(db, device_token, google_subject)
            if account_id is None:
                return False
            found = db.execute(
                "SELECT a.key_hash FROM agents a JOIN account_agents own ON own.agent = a.name "
                "WHERE own.account_id = ? AND a.name = ? AND a.admin = 0", (account_id, name),
            ).fetchone()
            if not found or is_revoked(found[0]):
                return False
            db.execute("UPDATE agents SET key_hash = ? WHERE name = ?", (revoked_key_marker(), name))
            return True

    def issue_claim(self, credential_hash: str) -> str | None:
        """Look up the agent inside the write transaction. Return no API key."""
        token = secrets.token_urlsafe(32)
        with self._conn(write=True) as db:
            row = db.execute("SELECT name FROM agents WHERE key_hash = ?", (credential_hash,)).fetchone()
            if not row:
                return None
            agent = row[0]
            if db.execute("SELECT 1 FROM account_agents WHERE agent = ?", (agent,)).fetchone():
                raise ClaimUnavailable
            now = self._clock()
            db.execute("DELETE FROM account_claims WHERE expires_at <= ? OR agent = ?", (now, agent))
            db.execute("INSERT INTO account_claims VALUES (?, ?, ?, ?)",
                       (agent, _digest(b"claim", token), credential_hash, now + CLAIM_TTL))
        return token

    def _claim(self, db: sqlite3.Connection, token: str) -> str | None:
        row = db.execute(
            "SELECT c.agent FROM account_claims c JOIN agents a ON a.name = c.agent "
            "AND a.key_hash = c.credential_hash LEFT JOIN account_agents own ON own.agent = c.agent "
            "WHERE c.token_hash = ? AND c.expires_at > ? AND own.agent IS NULL",
            (_digest(b"claim", token), self._clock()),
        ).fetchone()
        return row[0] if row else None

    def claim_agent(self, token: str, *, device_token: str | None = None,
                    google_subject: str | None = None) -> ClaimResult:
        """Consume a claim after explicit browser confirmation. A stale browser
        credential must not silently create a different account."""
        if not _valid_token(token) or (device_token is not None and google_subject is not None):
            raise ClaimUnavailable
        with self._conn(write=True) as db:
            agent = self._claim(db, token)
            if agent is None:
                raise ClaimUnavailable
            browser_token = None
            if google_subject is not None:
                row = db.execute("SELECT id FROM human_accounts WHERE google_subject = ?", (google_subject,)).fetchone()
                if row is None:
                    raise ClaimUnavailable
                account_id = row[0]
            elif device_token is not None:
                device = self._device(db, device_token)
                if not device:
                    raise ClaimUnavailable
                account_id = device.account_id
            else:
                account_id = secrets.token_urlsafe(16)
                db.execute("INSERT INTO human_accounts VALUES (?, NULL, ?)", (account_id, self._clock()))
                browser_token = self._add_device(db, account_id)
            if db.execute("SELECT count(*) FROM account_agents WHERE account_id = ?", (account_id,)).fetchone()[0] >= MAX_AGENTS:
                raise AccountFull
            db.execute("INSERT INTO account_agents VALUES (?, ?)", (agent, account_id))
            db.execute("DELETE FROM account_claims WHERE agent = ?", (agent,))
        return ClaimResult(account_id, browser_token)

    def claim_name(self, token: str) -> str | None:
        if not _valid_token(token):
            return None
        with self._conn() as db:
            return self._claim(db, token)

    def _device(self, db: sqlite3.Connection, token: str | None) -> Device | None:
        if not _valid_token(token):
            return None
        row = db.execute("SELECT id, account_id, created_at, expires_at FROM account_devices "
                         "WHERE token_hash = ? AND expires_at > ?",
                         (_digest(b"device", token), self._clock())).fetchone()
        return Device(*row) if row else None

    def device(self, token: str | None) -> Device | None:
        with self._conn() as db:
            return self._device(db, token)

    def devices(self, session: str) -> list[Device]:
        with self._conn() as db:
            device = self._device(db, session)
            if not device:
                return []
            return [Device(*r) for r in db.execute(
                "SELECT id, account_id, created_at, expires_at FROM account_devices "
                "WHERE account_id = ? AND expires_at > ? ORDER BY created_at, id",
                (device.account_id, self._clock()),
            )]

    def revoke_device(self, session: str, target_id: str) -> bool:
        with self._conn(write=True) as db:
            device = self._device(db, session)
            return bool(device and db.execute("DELETE FROM account_devices WHERE id = ? AND account_id = ?",
                                              (target_id, device.account_id)).rowcount)

    def _add_device(self, db: sqlite3.Connection, account_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        db.execute("INSERT INTO account_devices VALUES (?, ?, ?, ?, ?)",
                   (secrets.token_urlsafe(16), account_id, _digest(b"device", token), now, now + DEVICE_TTL))
        return token

    def _can_recover(self, db: sqlite3.Connection, session: str) -> str | None:
        device = self._device(db, session)
        if not device:
            return None
        row = db.execute("SELECT id FROM human_accounts WHERE id = ? AND google_subject IS NULL",
                         (device.account_id,)).fetchone()
        return row[0] if row else None

    def has_recovery_code(self, session: str) -> bool:
        with self._conn() as db:
            account_id = self._can_recover(db, session)
            return account_id is not None and db.execute(
                "SELECT 1 FROM account_recovery WHERE account_id = ?", (account_id,),
            ).fetchone() is not None

    def issue_recovery_code(self, session: str) -> str | None:
        token = secrets.token_urlsafe(32)
        with self._conn(write=True) as db:
            account_id = self._can_recover(db, session)
            if account_id is None:
                return None
            db.execute("INSERT INTO account_recovery VALUES (?, ?) ON CONFLICT(account_id) "
                       "DO UPDATE SET token_hash = excluded.token_hash",
                       (account_id, _digest(b"recovery", token)))
        return token

    def recover(self, token: str) -> str | None:
        """Use the offline credential once, revoke old devices and pairings,
        and mint a new device as one transaction. Do not change agent keys.
        """
        if not _valid_token(token):
            return None
        with self._conn(write=True) as db:
            row = db.execute("DELETE FROM account_recovery WHERE token_hash = ? RETURNING account_id",
                             (_digest(b"recovery", token),)).fetchone()
            if row is None:
                return None
            account_id = row[0]
            db.execute("DELETE FROM account_devices WHERE account_id = ?", (account_id,))
            return self._add_device(db, account_id)

    def _salt(self, db: sqlite3.Connection) -> bytes:
        return db.execute("SELECT salt FROM account_pair_salt WHERE id = 1").fetchone()[0]

    def offer_pairing(self, session: str) -> str | None:
        with self._conn(write=True) as db:
            source = self._device(db, session)
            if not source:
                return None
            now = self._clock()
            if source.expires_at <= now:
                return None
            previous = db.execute("SELECT code_hash FROM account_pairings WHERE account_id = ?",
                                  (source.account_id,)).fetchone()
            db.execute("DELETE FROM account_devices WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM account_pairings WHERE expires_at <= ? OR account_id = ?", (now, source.account_id))
            salt = self._salt(db)
            while True:
                code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
                encoded = _code_hash(code, salt)
                if previous and previous[0] == encoded:
                    continue
                inserted = db.execute("INSERT INTO account_pairings VALUES (?, ?, ?, ?) "
                                      "ON CONFLICT(code_hash) DO NOTHING",
                                      (source.account_id, source.id, encoded, min(now + PAIR_TTL, source.expires_at)))
                if inserted.rowcount:
                    return code[:4] + "-" + code[4:]

    def has_pairing(self) -> bool:
        with self._conn() as db:
            return db.execute(
                "SELECT 1 FROM account_pairings p JOIN account_devices d ON d.id = p.issuer_id "
                "WHERE p.expires_at > ? AND d.expires_at > ? LIMIT 1", (self._clock(), self._clock())
            ).fetchone() is not None

    def _permit_pair_attempt(self, db: sqlite3.Connection, client: str, now: float) -> bool:
        bucket = int(now) // _RATE_WINDOW * _RATE_WINDOW
        db.execute("DELETE FROM account_pair_limit WHERE window_start < ?", (bucket,))
        scopes = ((_digest(b"limiter", client), PAIR_TRIES_PER_CLIENT), ("total", PAIR_TRIES_TOTAL))
        for scope, limit in scopes:
            row = db.execute("SELECT attempts FROM account_pair_limit WHERE scope = ? AND window_start = ?",
                             (scope, bucket)).fetchone()
            if row and row[0] >= limit:
                return False
        db.executemany("INSERT INTO account_pair_limit VALUES (?, ?, 1) "
                       "ON CONFLICT(scope, window_start) DO UPDATE SET attempts = attempts + 1",
                       [(scope, bucket) for scope, _ in scopes])
        return True

    def redeem_pairing(self, code: str, *, client: str) -> PairResult:
        normalized = code.strip().upper().replace("-", "") if len(code) <= 16 and code.isascii() else ""
        with self._conn(write=True) as db:
            now = self._clock()
            if not self._permit_pair_attempt(db, client, now):
                return PairResult("limited")
            if not _CODE.fullmatch(normalized):
                return PairResult("invalid")
            encoded = _code_hash(normalized, self._salt(db))
            row = db.execute(
                "SELECT p.account_id FROM account_pairings p JOIN account_devices d ON d.id = p.issuer_id "
                "WHERE p.code_hash = ? AND p.expires_at > ? AND d.expires_at > ?", (encoded, now, now)
            ).fetchone()
            if not row:
                return PairResult("invalid")
            account_id = row[0]
            db.execute("DELETE FROM account_devices WHERE expires_at <= ?", (now,))
            count = db.execute("SELECT count(*) FROM account_devices WHERE account_id = ?", (account_id,)).fetchone()[0]
            if count >= MAX_DEVICES:
                return PairResult("full")
            db.execute("DELETE FROM account_pairings WHERE account_id = ?", (account_id,))
            return PairResult("connected", self._add_device(db, account_id))
