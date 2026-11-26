"""Bind browser review publications to exact Drive versions and a final boundary."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE review_submissions (
    sequence INTEGER PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES drive_versions(version_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    closing INTEGER NOT NULL CHECK (closing IN (0, 1)),
    published_at REAL NOT NULL,
    threads TEXT NOT NULL,
    UNIQUE (artifact_id, request_id)
);
CREATE INDEX review_submissions_artifact ON review_submissions(artifact_id, sequence);
CREATE UNIQUE INDEX review_submissions_one_close ON review_submissions(artifact_id) WHERE closing=1;
"""


class SubmissionUnavailable(Exception):
    """There is no matching reviewable version, or this browser cannot close it."""


class SubmissionConflict(Exception):
    """The request changed on retry, or the review already has a closing boundary."""


@dataclass(frozen=True)
class ReviewSubmission:
    subject: str  # only from the authenticated Workspace session
    request_id: str
    version_id: str
    closing: bool

    @classmethod
    def parse(cls, subject: str, value: object) -> ReviewSubmission:
        if not isinstance(value, dict) or value.keys() != {"id", "version", "close"}:
            raise ValueError("invalid review submission")
        request_id, version_id, closing = value["id"], value["version"], value["close"]
        if (any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_-]{22}", x)
                for x in (request_id, version_id)) or not isinstance(closing, bool)):
            raise ValueError("invalid review submission")
        return cls(subject, request_id, version_id, closing)

    def fingerprint(self, *, key_hash: str, text: str | None, for_agent: bool) -> str:
        # Drafts are read once inside the publication transaction. A retry
        # must not consume drafts that appeared after the first publication.
        body = json.dumps([key_hash, self.version_id, self.closing, text, for_agent], ensure_ascii=True)
        return hashlib.sha256(body.encode("ascii")).hexdigest()

    def check(self, db: sqlite3.Connection, artifact_id: str, fingerprint: str) -> tuple[str, list[int] | None]:
        version = db.execute(
            "SELECT v.kind, EXISTS (SELECT 1 FROM account_agents own JOIN human_accounts h ON h.id=own.account_id "
            "WHERE own.agent=a.owner AND h.google_subject=?) FROM visible_artifacts a "
            "JOIN drive_refs r ON r.artifact_id=a.id "
            "JOIN drive_versions v ON v.artifact_id=a.id AND v.file_id=r.file_id "
            "WHERE a.id=? AND v.version_id=? AND a.project IS NOT NULL AND a.expires_at IS NULL",
            (self.subject, artifact_id, self.version_id),
        ).fetchone()
        if version is None or (self.closing and not version[1]):
            raise SubmissionUnavailable
        existing = db.execute("SELECT fingerprint,threads FROM review_submissions WHERE artifact_id=? AND request_id=?",
                              (artifact_id, self.request_id)).fetchone()
        if existing is not None:
            if existing[0] != fingerprint:
                raise SubmissionConflict
            return version[0], json.loads(existing[1])
        if self.closing and db.execute(
            "SELECT 1 FROM review_submissions WHERE artifact_id=? AND closing=1 UNION ALL "
            "SELECT 1 FROM review_documents WHERE artifact_id=? LIMIT 1",
            (artifact_id, artifact_id),
        ).fetchone() is not None:
            raise SubmissionConflict
        return version[0], None

    def record(self, db: sqlite3.Connection, artifact_id: str, fingerprint: str, now: float, threads: set[int]) -> None:
        row = db.execute("INSERT INTO review_submissions(artifact_id,version_id,request_id,fingerprint,closing,published_at,threads) "
                         "VALUES (?,?,?,?,?,?,?)", (artifact_id, self.version_id, self.request_id, fingerprint,
                                                    self.closing, now, json.dumps(sorted(threads))))
        if self.closing:
            # Comments/replies are append-only in discussion (a whole thread
            # can be deleted). Record only the reply boundary and decisions,
            # not duplicate texts or an ambiguous wall-clock cutoff.
            live = {n: [replies, "approved" if approved else "rejected" if rejected else None, bool(resolved)]
                    for n, approved, rejected, resolved, replies in db.execute(
                        "SELECT c.n,c.approved_by,c.rejected_by,c.resolved_at IS NOT NULL, "
                        "(SELECT COUNT(*) FROM replies r WHERE r.artifact_id=c.artifact_id AND r.n=c.n) "
                        "FROM comments c WHERE c.artifact_id=?", (artifact_id,))}
            evidence = {}
            for (saved,) in db.execute("SELECT threads FROM review_submissions WHERE artifact_id=?", (artifact_id,)):
                evidence.update((n, live[n]) for n in json.loads(saved) if n in live)
            db.execute("UPDATE review_submissions SET evidence=? WHERE sequence=?",
                       (json.dumps(evidence, sort_keys=True), row.lastrowid))


def browser_state(db: sqlite3.Connection, artifact_id: str, subject: str, *, read_only: bool) -> str | None:
    """Use only after checking the viewer's access to the artifact."""
    row = db.execute(
        "SELECT EXISTS (SELECT 1 FROM account_agents own JOIN human_accounts h ON h.id=own.account_id "
        "WHERE own.agent=a.owner AND h.google_subject=?), "
        "EXISTS (SELECT 1 FROM review_submissions s WHERE s.artifact_id=a.id AND s.closing=1), "
        "o.sequence IS NOT NULL, CASE WHEN o.confirmed_at IS NOT NULL THEN d.doc_id END "
        "FROM visible_artifacts a JOIN drive_refs r ON r.artifact_id=a.id "
        "LEFT JOIN review_documents d ON d.artifact_id=a.id AND d.file_id=r.file_id "
        "LEFT JOIN review_operations o ON o.artifact_id=d.artifact_id AND o.kind='close' "
        "WHERE a.id=? AND a.project IS NOT NULL AND a.expires_at IS NULL",
        (subject, artifact_id),
    ).fetchone()
    if row is None:
        return None
    may_close, submitted, in_docs, doc_id = row
    if doc_id:
        return "ready:" + doc_id
    if submitted or in_docs:
        return "pending"
    return "closable" if may_close and not read_only else "open"


def pending_closures(db: sqlite3.Connection, owner: str, *, artifact_id: str | None = None,
                     project_id: str | None = None) -> dict[str, str]:
    """Owner-scoped opaque identifiers only; no display names or review bodies."""
    if (artifact_id is None) == (project_id is None):
        raise ValueError("exactly one watch scope is required")
    rows = db.execute(
        "SELECT a.id,s.request_id FROM visible_artifacts a JOIN visible_projects p ON p.id=a.project "
        "JOIN review_submissions s ON s.artifact_id=a.id AND s.closing=1 "
        "JOIN drive_refs r ON r.artifact_id=a.id "
        "JOIN drive_versions v ON v.version_id=s.version_id AND v.file_id=r.file_id "
        "WHERE a.owner=:owner AND a.expires_at IS NULL "
        "AND ((:artifact IS NOT NULL AND a.id=:artifact) OR (:project IS NOT NULL AND a.project=:project)) "
        "AND NOT EXISTS (SELECT 1 FROM review_operations o WHERE o.artifact_id=a.id "
        "AND o.kind='close' AND o.confirmed_at IS NOT NULL) ORDER BY s.sequence",
        {"owner": owner, "artifact": artifact_id, "project": project_id},
    ).fetchall()
    return dict(rows)


def read_evidence(db: sqlite3.Connection, artifact_id: str, owner: str, closure_id: str, n: int) -> dict | None:
    """Read one retained published thread exactly through its captured reply boundary."""
    row = db.execute(
        "SELECT s.evidence,c.text,c.by_agent FROM visible_artifacts a JOIN drive_refs d ON d.artifact_id=a.id "
        "JOIN review_submissions s ON s.artifact_id=a.id AND s.closing=1 "
        "JOIN drive_versions v ON v.version_id=s.version_id AND v.file_id=d.file_id "
        "JOIN comments c ON c.artifact_id=a.id "
        "WHERE a.id=? AND a.owner=? AND s.request_id=? AND c.n=? AND a.expires_at IS NULL",
        (artifact_id, owner, closure_id, n),
    ).fetchone()
    if row is None or row[0] is None:
        return None  # a pre-capture backup cannot establish a reply boundary
    boundary = json.loads(row[0]).get(str(n))
    if boundary is None:
        return None
    count, decision, resolved = boundary
    replies = db.execute("SELECT text,by_agent FROM replies WHERE artifact_id=? AND n=? ORDER BY rowid LIMIT ?",
                         (artifact_id, n, count)).fetchall()
    if len(replies) != count:
        return None
    return {"n": n, "closure_id": closure_id, "text": row[1], "by_agent": bool(row[2]),
            "decision": decision, "resolved": resolved,
            "replies": [{"text": text, "by_agent": bool(by_agent)} for text, by_agent in replies]}


def close_source(db: sqlite3.Connection, artifact_id: str, owner: str, origin: str) -> dict | None:
    """The ordered rounds through the single, authenticated closing publication."""
    rows = db.execute(
        "SELECT c.request_id,c.published_at,c.version_id,s.version_id,s.threads "
        "FROM visible_artifacts a JOIN drive_refs r ON r.artifact_id=a.id "
        "JOIN review_submissions c ON c.artifact_id=a.id AND c.closing=1 "
        "JOIN review_submissions s ON s.artifact_id=a.id AND s.sequence<=c.sequence "
        "JOIN drive_versions v ON v.version_id=s.version_id AND v.file_id=r.file_id "
        "WHERE a.id=? AND a.owner=? AND a.project IS NOT NULL AND a.expires_at IS NULL ORDER BY s.sequence",
        (artifact_id, owner),
    ).fetchall()
    if not rows:
        return None
    closing_id, closed_at, final_version = rows[-1][:3]
    collected: dict[str, set[int]] = {}
    for _, _, _, version_id, threads in rows:
        collected.setdefault(version_id, set()).update(json.loads(threads))
    if final_version not in collected:
        return None
    base = f"{origin}/a/{artifact_id}/versions"
    return {"id": closing_id, "closed_at": closed_at, "final_version_id": final_version,
            "versions": [{"version_id": version_id, "version_url": f"{base}/{version_id}",
                          "threads": sorted(threads)} for version_id, threads in collected.items()]}
