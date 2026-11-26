"""Durable, transport-independent state for the existing paced subscription.

A caller must retry PollConflict from a fresh checkpoint. A pending notice is
acknowledged only by an explicit ack, not by polling or attempting delivery.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import uuid
from contextlib import closing
from pathlib import Path

from artifacts_cli import ID_RE, _Subscription

MAX_PENDING = 1000
MAX_CHECKPOINT_BYTES = 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024
_RECIPIENT = re.compile(r"[A-Za-z0-9_.:@-]{1,128}\Z")


class PollConflict(Exception):
    """Another poll committed first; retry rather than use the stale snapshot."""


class StateFull(Exception):
    """A configured bound would be exceeded; nothing was discarded."""


def _id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError("invalid artifact ID")
    return value


def _number(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid count")
    return value


def _event(event, kind, target):
    # Construct an allowlisted record; never serialize arbitrary server data.
    if not isinstance(event, dict) or event.get("event") not in ("waiting", "accepted", "review_closed", "review_notes"):
        raise ValueError("invalid event")
    name = event["event"]
    if name in ("review_closed", "review_notes"):
        field = "closure" if name == "review_closed" else "submission"
        keys = {"event", "artifact", field} | ({"project"} if kind == "p" else set())
        if set(event) != keys or event.get("project" if kind == "p" else "artifact") != target:
            raise ValueError("invalid review event scope")
        result = {"event": name, "artifact": _id(event["artifact"]), field: _id(event[field])}
        if kind == "p":
            result["project"] = target
    elif kind == "a":
        if set(event) != {"event", "comments"} or not isinstance(event["comments"], list):
            raise ValueError("invalid file event")
        comments = event["comments"]
        if not comments or any(not isinstance(c, dict) or set(c) != {"n"} for c in comments):
            raise ValueError("invalid comments")
        result = {"event": name, "comments": [{"n": _number(c["n"])} for c in comments]}
    else:
        if set(event) != {"event", "project", "files"} or event["project"] != target:
            raise ValueError("invalid project event")
        files = event["files"]
        if not isinstance(files, list) or not files:
            raise ValueError("invalid files")
        if any(not isinstance(f, dict) or set(f) != {"id", name} for f in files):
            raise ValueError("invalid file summary")
        result = {
            "event": name,
            "project": target,
            "files": [{"id": _id(f["id"]), name: _number(f[name])} for f in files],
        }
    encoded = json.dumps(result, separators=(",", ":"))
    if len(encoded.encode()) > MAX_EVENT_BYTES:
        raise StateFull("event too large")
    return encoded


def _checkpoint(state):
    waiting = {}
    for fid, comments in state.waiting.items():
        waiting[_id(fid)] = {str(_number(n)): _number(count) for n, count in comments.items()}
    resolved = None if state.resolved is None else sorted(_number(n) for n in state.resolved)
    if state.since is not None and (
        type(state.since) not in (int, float) or not math.isfinite(state.since)
    ):
        raise ValueError("invalid server clock")
    data = {
        "waiting": waiting,
        "resolved": resolved,
        "since": state.since,
        "accepted": {_id(fid): _number(n) for fid, n in state.accepted.items()},
        "next_detail": None if state.next_detail is None else _id(state.next_detail),
        "review_closures": {_id(fid): _id(closure) for fid, closure in state.review_closures.items()},
        "review_notes": {_id(fid): _id(submission) for fid, submission in state.review_notes.items()},
    }
    encoded = json.dumps(data, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > MAX_CHECKPOINT_BYTES:
        raise StateFull("checkpoint too large")
    return encoded


def _restore(state, encoded):
    if encoded is None:
        return
    data = json.loads(encoded)
    state.waiting = {
        fid: {int(n): count for n, count in comments.items()}
        for fid, comments in data["waiting"].items()
    }
    state.resolved = None if data["resolved"] is None else set(data["resolved"])
    state.since, state.accepted, state.next_detail = (
        data["since"],
        data["accepted"],
        data["next_detail"],
    )
    state.review_closures = {_id(fid): _id(closure) for fid, closure in data.get("review_closures", {}).items()}
    state.review_notes = {_id(fid): _id(submission) for fid, submission in data.get("review_notes", {}).items()}


def _origin(origin):
    parsed = urllib.parse.urlsplit(origin)
    # urllib decodes the authority when sending the request. Reject escaped
    # authorities, including IPv6 zones, rather than give them another key.
    if "%" in parsed.netloc:
        raise ValueError("escaped authorities and IPv6 zones are unsupported watch origins")
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("expected service origin without credentials or path")
    # Accessing port validates it; canonicalize default ports and case.
    port = parsed.port
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{ipaddress.IPv6Address(host).compressed}]"
    suffix = (
        f":{port}" if port is not None and port != {"http": 80, "https": 443}[parsed.scheme] else ""
    )
    return f"{parsed.scheme}://{host}{suffix}"


def _pending(db, key):
    rows = db.execute(
        "SELECT id,event FROM pending WHERE origin=? AND kind=? AND target=? ORDER BY rowid",
        key,
    )
    return [{"id": event_id, "event": json.loads(event)} for event_id, event in rows]


def read_pending(directory, key):
    """Nonblocking read for a detached client. Never creates or locks a journal for writing."""
    path = Path(directory) / "watches.sqlite3"
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return []
    # Windows permission bits are synthetic; the local per-user directory
    # inherits its ACL. An explicit state directory must be private too.
    if os.name != "nt" and (mode | path.parent.stat().st_mode) & 0o077:
        raise PermissionError("watch state must be private")
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0)
        ) as db:
            return _pending(db, key)
    except sqlite3.OperationalError as e:
        # A collector may be initializing or committing while the reader attaches.
        if str(e) in {"database is locked", "database table is locked", "no such table: pending"}:
            return None
        raise


class WatchState:
    """One journal per origin and explicit file (a) or project (p) scope."""

    def __init__(self, directory, origin, kind, target):
        self.origin, self.kind, self.target = _origin(origin), kind, _id(target)
        if kind not in ("a", "p"):
            raise ValueError("scope must be a file or project")
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt" and directory.stat().st_mode & 0o077:
            raise PermissionError("watch state directory must be private")
        path = directory / "watches.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise PermissionError("watch state file must be private")
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS watches (
                origin TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0, checkpoint TEXT,
                last_success REAL, last_failure REAL, failure TEXT,
                poll_health TEXT NOT NULL DEFAULT 'not_started',
                last_delivery REAL, delivery_failure REAL, recipient TEXT,
                PRIMARY KEY(origin, kind, target));
            CREATE TABLE IF NOT EXISTS pending (
                id TEXT PRIMARY KEY, origin TEXT NOT NULL, kind TEXT NOT NULL,
                target TEXT NOT NULL, event TEXT NOT NULL,
                FOREIGN KEY(origin, kind, target) REFERENCES watches(origin, kind, target));
        """)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO watches(origin,kind,target) VALUES(?,?,?)", self.key
            )

    @property
    def key(self):
        return (self.origin, self.kind, self.target)

    def close(self):
        self.db.close()

    def poll(self, client):
        if _origin(client.url) != self.origin:
            raise ValueError("watch origin does not match the client's service origin")
        revision, checkpoint = self.db.execute(
            "SELECT revision,checkpoint FROM watches WHERE origin=? AND kind=? AND target=?",
            self.key,
        ).fetchone()
        state = _Subscription(client, self.target, self.kind)
        try:
            _restore(state, checkpoint)
            events = state.poll()
            encoded = [_event(event, self.kind, self.target) for event in events]
            snapshot = _checkpoint(state)
            self._commit(revision, snapshot, encoded, state.detail_failure)
        except PollConflict:
            raise
        except Exception as e:
            reason = "state_full" if isinstance(e, StateFull) else "poll_failed"
            if isinstance(e, urllib.error.HTTPError):
                reason = {
                    401: "unauthorized",
                    403: "forbidden",
                    404: "target_unavailable",
                    429: "rate_limited",
                }.get(e.code, "service_unavailable" if e.code >= 500 else "request_refused")
            with self.db:
                self.db.execute(
                    "UPDATE watches SET last_failure=?,failure=?,poll_health='failed' "
                    "WHERE origin=? AND kind=? AND target=? AND revision=?",
                    (time.time(), reason, *self.key, revision),
                )
            raise

    def _commit(self, revision, snapshot, encoded, detail_failure):
        now = time.time()
        with self.db:
            changed = self.db.execute(
                "UPDATE watches SET revision=revision+1,checkpoint=?,last_success=?,poll_health=?, "
                "last_failure=COALESCE(?,last_failure),failure=COALESCE(?,failure) "
                "WHERE origin=? AND kind=? AND target=? AND revision=?",
                (
                    snapshot,
                    now,
                    "degraded" if detail_failure else "ok",
                    now if detail_failure else None,
                    detail_failure,
                    *self.key,
                    revision,
                ),
            ).rowcount
            if not changed:
                raise PollConflict("checkpoint advanced during poll")
            count = self.db.execute(
                "SELECT count(*) FROM pending WHERE origin=? AND kind=? AND target=?", self.key
            ).fetchone()[0]
            if count + len(encoded) > MAX_PENDING:
                raise StateFull("pending mailbox full")
            for event in encoded:
                self.db.execute(
                    "INSERT INTO pending VALUES(?,?,?,?,?)", (uuid.uuid4().hex, *self.key, event)
                )

    def pending(self):
        return _pending(self.db, self.key)

    def ack(self, event_id):
        with self.db:
            return bool(
                self.db.execute(
                    "DELETE FROM pending WHERE id=? AND origin=? AND kind=? AND target=?",
                    (event_id, *self.key),
                ).rowcount
            )

    def delivery(self, recipient, *, succeeded):
        if recipient is not None and (
            not isinstance(recipient, str) or not _RECIPIENT.fullmatch(recipient)
        ):
            raise ValueError("invalid recipient")
        if type(succeeded) is not bool:
            raise ValueError("delivery outcome must be boolean")
        query = (
            "UPDATE watches SET recipient=?,last_delivery=? WHERE origin=? AND kind=? AND target=?"
            if succeeded
            else "UPDATE watches SET recipient=?,delivery_failure=? "
            "WHERE origin=? AND kind=? AND target=?"
        )
        with self.db:
            self.db.execute(query, (recipient, time.time(), *self.key))

    def status(self):
        row = self.db.execute(
            "SELECT w.last_success,w.last_failure,w.failure,w.poll_health,w.last_delivery, "
            "w.delivery_failure,w.recipient, (SELECT count(*) FROM pending p "
            "WHERE p.origin=w.origin AND p.kind=w.kind AND p.target=w.target) "
            "FROM watches w WHERE w.origin=? AND w.kind=? AND w.target=?",
            self.key,
        ).fetchone()
        return dict(
            zip(
                (
                    "last_success",
                    "last_failure",
                    "failure",
                    "poll_health",
                    "last_delivery",
                    "delivery_failure",
                    "recipient",
                    "pending",
                ),
                row,
                strict=True,
            )
        )
