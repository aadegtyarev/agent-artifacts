"""Durable, owner-scoped native report operations. Never replay a possible POST."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from .docs_report import DocsReport, ReportConflict, ReportLimitError, ReportRound
from .drive_storage import _reference
from .report_compaction import CompactionRow, VerifiedReportSnapshot, _text, plan_compaction
from .report_compaction_dispatch import compact_report, dispatch_verified
from .report_compaction_readback import verify_compacted_report
from .report_source import ExpectedReportRound, ReportPresentation, read_blank_report, read_report_source
from .review_notes_body import NotesBody, NotesSection, render_notes
from .review_notes_doc import dispatch_notes, read_notes
from .review_outcome import outcome_with_evidence, thread_line, thread_numbers
from .review_submissions import close_source, next_notes_source, read_evidence
from .store import ID_RE

if TYPE_CHECKING:
    from .store import Store

_MAX_ROUNDS = 128

SCHEMA = """
CREATE TABLE review_documents (
    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON DELETE RESTRICT,
    file_id TEXT NOT NULL,
    create_sent INTEGER NOT NULL DEFAULT 0 CHECK (create_sent IN (0, 1)),
    doc_id TEXT UNIQUE,
    presentation TEXT
);
CREATE TABLE review_operations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL REFERENCES review_documents(artifact_id) ON DELETE RESTRICT,
    operation_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('round', 'close')),
    payload_hash TEXT NOT NULL,
    payload TEXT,
    source TEXT,
    sent INTEGER NOT NULL DEFAULT 0 CHECK (sent IN (0, 1)),
    confirmed_at REAL,
    requested_at REAL NOT NULL,
    UNIQUE (artifact_id, operation_key)
);
CREATE UNIQUE INDEX one_unconfirmed_review_operation ON review_operations (artifact_id) WHERE confirmed_at IS NULL;
CREATE UNIQUE INDEX one_review_closure ON review_operations (artifact_id) WHERE kind = 'close';
"""


class ReportNotFound(LookupError):
    """The caller has no eligible live owned Drive artifact or report."""


class ReportPending(RuntimeError):
    """A possibly dispatched operation is not confirmed or another one holds the slot."""


class ReportNotesPending(ReportPending):
    """Published round notes must be verified before adding the outcome."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _key(value: object) -> str:
    key = _reference(value)
    if len(key) > 128 or key.startswith(("browser-", "notes-")):
        raise ValueError("invalid operation key")
    return key


def _choices(items: object) -> dict[str, tuple[str, str]]:
    if not isinstance(items, list) or not 1 <= len(items) <= _MAX_ROUNDS:
        raise ValueError("invalid closure version rows")
    choices = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"version_id", "decision", "reason"}:
            raise ValueError("invalid closure version row")
        version = item["version_id"]
        if not isinstance(version, str) or not re.fullmatch(ID_RE, version) or version in choices:
            raise ValueError("invalid or duplicate closure version")
        decision, reason = item["decision"], item["reason"]
        reason = "Not recorded" if reason is None else reason
        _text(decision, "decision")
        _text(reason, "reason")
        choices[version] = (decision, reason)
    return choices


@dataclass(frozen=True)
class ReportTask:
    sequence: int
    artifact_id: str
    owner: str
    file_id: str
    kind: str
    key: str
    payload_hash: str
    payload: str = field(repr=False)
    confirmed_doc: str | None = None


@dataclass(frozen=True)
class ReportResult:
    document_id: str
    newly_confirmed: bool


@dataclass(frozen=True)
class _State:
    create_sent: bool
    doc_id: str | None
    presentation: str | None
    sent: bool
    confirmed: bool


class ReviewReports:
    def __init__(self, store: Store, public_url: str) -> None:
        self.store = store
        self.origin = public_url.rstrip("/")

    def _owned_file(self, db, artifact_id: str, owner: str) -> str:
        # Ordinary version URLs expire for unfiled artifacts. Do not promise a
        # lasting Docs citation without a retention policy for those objects.
        row = db.execute(
            "SELECT d.file_id FROM visible_artifacts a JOIN drive_refs d ON d.artifact_id = a.id "
            "WHERE a.id = ? AND a.owner = ? AND a.project IS NOT NULL AND a.expires_at IS NULL",
            (artifact_id, owner),
        ).fetchone()
        if row is None:
            raise ReportNotFound("a live owned Drive artifact in a project is required")
        if db.execute("SELECT 1 FROM drive_renames WHERE artifact_id=?", (artifact_id,)).fetchone():
            raise ReportPending("Drive rename is unresolved")
        return row[0]

    @staticmethod
    def _check_threads(db, artifact_id: str, threads: tuple[int, ...]) -> None:
        for n in threads:
            if db.execute("SELECT 1 FROM comments WHERE artifact_id = ? AND n = ?", (artifact_id, n)).fetchone() is None:
                raise ValueError("thread reference is not published on this artifact")

    @staticmethod
    def _check_version(db, artifact_id: str, file_id: str, version: str) -> None:
        if db.execute("SELECT 1 FROM drive_versions WHERE artifact_id = ? AND file_id = ? AND version_id = ?",
                      (artifact_id, file_id, version)).fetchone() is None:
            raise ReportNotFound("published version not found on this artifact")

    def _round_sources(self, db, artifact_id: str, *, before: int | None = None) -> list[dict]:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT source FROM review_operations WHERE artifact_id = ? AND kind = 'round' "
            "AND operation_key NOT LIKE 'notes-%' AND confirmed_at IS NOT NULL "
            "AND (? IS NULL OR sequence < ?) ORDER BY sequence",
            (artifact_id, before, before),
        )]

    def _reserve(self, db, artifact_id, owner, file_id, kind, key, payload, source=None) -> ReportTask:
        digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
        doc = db.execute("SELECT file_id, doc_id FROM review_documents WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if doc is not None and doc[0] != file_id:
            raise ReportConflict("report belongs to a different Drive file")
        saved = db.execute(
            "SELECT sequence, kind, payload_hash, confirmed_at FROM review_operations "
            "WHERE artifact_id = ? AND operation_key = ?", (artifact_id, key),
        ).fetchone()
        if saved is not None:
            sequence, mode, known, confirmed = saved
            if mode != kind or known != digest:
                raise ReportConflict("report key already used with different input")
            return ReportTask(sequence, artifact_id, owner, file_id, kind, key, digest, payload,
                              doc[1] if confirmed is not None and doc is not None else None)
        if db.execute("SELECT 1 FROM review_operations WHERE artifact_id = ? "
                      "AND (confirmed_at IS NULL OR kind = 'close')", (artifact_id,)).fetchone():
            raise ReportPending("report has an unresolved operation or is closed")
        if doc is None:
            db.execute("INSERT INTO review_documents (artifact_id, file_id) VALUES (?, ?)", (artifact_id, file_id))
        sequence = db.execute(
            "INSERT INTO review_operations (artifact_id, operation_key, kind, payload_hash, payload, source, requested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, key, kind, digest, payload, source, self.store.clock()),
        ).lastrowid
        return ReportTask(sequence, artifact_id, owner, file_id, kind, key, digest, payload)

    @staticmethod
    def _notes_sources(db, artifact_id: str, *, before: int | None = None) -> list[dict]:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT source FROM review_operations WHERE artifact_id=? AND kind='round' "
            "AND operation_key LIKE 'notes-%' AND confirmed_at IS NOT NULL "
            "AND (? IS NULL OR sequence<?) ORDER BY sequence", (artifact_id, before, before),
        )]

    def _notes_body(self, artifact_id: str, notes: list[dict], *, summary: str | None = None,
                    rows: list[dict] | None = None) -> NotesBody:
        versions: dict[str, list[str]] = {}
        for item in notes:
            versions.setdefault(item["version"], []).append(item["text"])
        if rows is None:
            sections = tuple(NotesSection(version, tuple(entries)) for version, entries in versions.items())
        else:
            choices = {row["version_url"].rsplit("/", 1)[-1]: (row["decision"], row["reason"]) for row in rows}
            if not versions.keys() <= choices.keys():
                raise ReportConflict("verified review notes have versions outside the closing capture")
            sections = tuple(NotesSection(v, tuple(versions.get(v, ())), result) for v, result in choices.items())
        return render_notes(self.origin, artifact_id, sections, outcome=summary)

    def prepare_notes(self, artifact_id: str, owner: str, fields: dict) -> ReportTask:
        request, summary = fields["submission_id"], fields["summary"]
        if not isinstance(request, str) or not re.fullmatch(ID_RE, request):
            raise ValueError("invalid notes submission ID")
        _text(summary, "round summary")
        threads = thread_numbers(fields.get("threads", []))
        key = "notes-" + request
        with self.store._conn(write=True) as db:
            file_id = self._owned_file(db, artifact_id, owner)
            known = db.execute("SELECT source FROM review_operations WHERE artifact_id=? AND operation_key=?",
                               (artifact_id, key)).fetchone()
            if known:
                version = json.loads(known[0])["version"]
            else:
                if self._round_sources(db, artifact_id):
                    raise ReportConflict("legacy report is not a living notes document")
                round_ = next_notes_source(db, artifact_id, owner, self.origin)
                if round_ is None or round_["id"] != request:
                    raise ReportNotFound("no matching pending notes source")
                version = round_["version_id"]
                if any(n not in round_["threads"] or read_evidence(db, artifact_id, owner, request, n,
                                                                  closing=False) is None for n in threads):
                    raise ValueError("notes reference uncaptured or unavailable evidence")
            self._check_version(db, artifact_id, file_id, version)
            item = {"version": version, "text": summary + ("\n" + thread_line(threads) if threads else "")}
            payload = _json({"notes": item, "threads": threads})
            if not known:
                previous = self._notes_sources(db, artifact_id)
                self._notes_body(artifact_id, [*previous, item])
            return self._reserve(db, artifact_id, owner, file_id, "round", key, payload, _json(item))

    def prepare_round(self, artifact_id: str, owner: str, fields: dict) -> ReportTask:
        key = _key(fields["key"])
        version = fields["version_id"]
        if not isinstance(version, str) or not re.fullmatch(ID_RE, version):
            raise ReportNotFound("published version not found")
        threads = thread_numbers(fields.get("threads", []))
        items = fields["open_items"]
        if not isinstance(items, str):
            raise ValueError("invalid open items")
        if threads:
            items += "\n" + thread_line(threads)
        candidate = ReportRound(key, f"{self.origin}/a/{artifact_id}/versions/{version}",
                                fields["goal"], fields["decision"], fields["reasons"], items)
        rendered = DocsReport.render_for(self.origin, artifact_id, candidate)
        expected = asdict(rendered)
        del expected["text"]
        source = _json({"version": version, "expected": expected})
        payload = _json({"candidate": asdict(candidate), "threads": threads})
        with self.store._conn(write=True) as db:
            file_id = self._owned_file(db, artifact_id, owner)
            self._reject_manual_on_living(db, artifact_id)
            self._reject_manual_after_closure(db, artifact_id)
            self._check_version(db, artifact_id, file_id, version)
            self._check_threads(db, artifact_id, threads)
            if (len(self._round_sources(db, artifact_id)) >= _MAX_ROUNDS and not db.execute(
                "SELECT 1 FROM review_operations WHERE artifact_id = ? AND operation_key = ?",
                (artifact_id, key),
            ).fetchone()):
                raise ReportLimitError("report has too many rounds")
            return self._reserve(db, artifact_id, owner, file_id, "round", key, payload, source)

    def prepare_close(self, artifact_id: str, owner: str, fields: dict) -> ReportTask:
        if fields["closed"] is not True:
            raise ValueError("explicit review closure is required")
        key = _key(fields["key"])
        summary, threads = outcome_with_evidence(fields["outcome"])
        _text(summary, "summary")
        choices = _choices(fields["rows"])
        with self.store._conn(write=True) as db:
            file_id = self._owned_file(db, artifact_id, owner)
            self._reject_manual_on_living(db, artifact_id)
            self._reject_manual_after_closure(db, artifact_id)
            sources = self._round_sources(db, artifact_id)
            if not sources:
                raise ReportNotFound("no confirmed report rounds")
            versions = {source["version"]: source["expected"]["version_url"] for source in sources}
            if (set(versions) != set(choices) or any(url != f"{self.origin}/a/{artifact_id}/versions/{version}"
                                                   for version, url in versions.items())):
                raise ValueError("closure rows must keep every published version on the app origin")
            for version in versions:
                self._check_version(db, artifact_id, file_id, version)
            self._check_threads(db, artifact_id, threads)
            rows = [asdict(CompactionRow(url, *choices[v])) for v, url in versions.items()]
            payload = _json({"summary": summary, "rows": rows, "threads": threads})
            return self._reserve(db, artifact_id, owner, file_id, "close", key, payload)

    @staticmethod
    def _reject_manual_on_living(db, artifact_id: str) -> None:
        if db.execute("SELECT 1 FROM review_operations WHERE artifact_id=? AND operation_key LIKE 'notes-%'",
                      (artifact_id,)).fetchone():
            raise ReportConflict("living review notes require their recorded source")

    @staticmethod
    def _reject_manual_after_closure(db, artifact_id: str) -> None:
        if db.execute("SELECT 1 FROM review_submissions WHERE artifact_id=? AND closing=1", (artifact_id,)).fetchone():
            raise ReportConflict("browser closure requires its recorded source")

    def prepare_final(self, artifact_id: str, owner: str, fields: dict) -> ReportTask:
        closure_id = fields["closure_id"]
        if not isinstance(closure_id, str) or not re.fullmatch(ID_RE, closure_id):
            raise ValueError("invalid closure ID")
        summary, threads = outcome_with_evidence(fields["outcome"], allow_unknown_choice=True)
        choices = _choices(fields["rows"])
        key = "browser-" + closure_id
        with self.store._conn(write=True) as db:
            file_id = self._owned_file(db, artifact_id, owner)
            closure = close_source(db, artifact_id, owner, self.origin)
            if closure is None or closure["id"] != closure_id:
                raise ReportNotFound("no matching browser closure")
            if next_notes_source(db, artifact_id, owner, self.origin) is not None:
                raise ReportNotesPending("verify the captured round notes before finishing the document")
            versions = {row["version_id"]: row["version_url"] for row in closure["versions"]}
            if set(versions) != set(choices):
                raise ValueError("keep every reviewed version, and only reviewed versions")
            evidence = {n for row in closure["versions"] for n in row["threads"]}
            if any(n not in evidence for n in threads):
                raise ValueError("evidence was not part of the closing review")
            self._check_threads(db, artifact_id, threads)
            if db.execute("SELECT 1 FROM review_operations WHERE artifact_id=? AND kind='round' "
                          "AND operation_key NOT LIKE 'notes-%' LIMIT 1", (artifact_id,)).fetchone():
                raise ReportConflict("report has older manual rounds; cannot replace them as a new blank Doc")
            rows = tuple(CompactionRow(url, *choices[v]) for v, url in versions.items())
            rows_json = [asdict(row) for row in rows]
            full = {"closure_id": closure_id, "summary": summary, "rows": rows_json, "threads": threads}
            previous = self._notes_sources(db, artifact_id)
            if previous:
                self._notes_body(artifact_id, previous, summary=summary, rows=rows_json)
                full["living"] = True
            else:
                # Keep the verified behavior for older closures and for those
                # without any confirmed notes yet.
                preflight = _blank_snapshot("preflight", artifact_id, "preflight", "preflight", rows)
                plan_compaction(preflight, public_url=self.origin, operation_key=key, summary=summary, rows=rows)
            return self._reserve(db, artifact_id, owner, file_id, "close", key, _json(full))

    def _state(self, db, task: ReportTask) -> _State:
        if self._owned_file(db, task.artifact_id, task.owner) != task.file_id:
            raise ReportConflict("artifact Drive file changed")
        row = db.execute(
            "SELECT d.create_sent, d.doc_id, d.presentation, o.sent, o.confirmed_at "
            "FROM review_operations o JOIN review_documents d ON o.artifact_id = d.artifact_id "
            "WHERE o.sequence = ? AND o.artifact_id = ? AND d.file_id = ? AND o.payload_hash = ?",
            (task.sequence, task.artifact_id, task.file_id, task.payload_hash),
        ).fetchone()
        if row is None:
            raise ReportPending("report operation no longer available")
        return _State(bool(row[0]), row[1], row[2], bool(row[3]), row[4] is not None)

    def _read(self, task: ReportTask) -> _State:
        with self.store._conn() as db:
            return self._state(db, task)

    def _ensure_doc(self, report: DocsReport, task: ReportTask, state: _State, *, create: bool) -> str:
        if state.doc_id is not None:
            return state.doc_id
        if not create or state.sent:
            raise ReportConflict("report operation has no verified Doc mapping")

        def before_create() -> None:
            with self.store._conn(write=True) as db:
                current = self._state(db, task)
                if current.create_sent or current.doc_id is not None or current.confirmed:
                    raise ReportPending("report create already dispatched")
                db.execute("UPDATE review_documents SET create_sent = 1 WHERE artifact_id = ?", (task.artifact_id,))

        doc = (report.lookup(task.artifact_id) if state.create_sent else
               report.create(task.artifact_id, claim=task.sequence, before_create=before_create))
        if doc is None:
            raise ReportPending("report creation has not been confirmed")
        with self.store._conn(write=True) as db:
            current = self._state(db, task)
            if current.doc_id is None:
                if not current.create_sent:
                    raise ReportConflict("report has no durable create claim")
                if db.execute("SELECT 1 FROM review_documents WHERE doc_id = ?", (doc,)).fetchone():
                    raise ReportConflict("mapped Doc already belongs to another artifact")
                db.execute("UPDATE review_documents SET doc_id = ? WHERE artifact_id = ?", (doc, task.artifact_id))
            elif current.doc_id != doc:
                raise ReportConflict("report document mapping changed")
        return doc

    def _remember_presentation(self, task: ReportTask, doc: str, start: ReportPresentation) -> None:
        value = _json(asdict(start))
        with self.store._conn(write=True) as db:
            state = self._state(db, task)
            if state.doc_id != doc or state.presentation not in (None, value):
                raise ReportConflict("report presentation changed")
            if state.presentation is None:
                if state.sent or state.confirmed:
                    raise ReportConflict("report has no trusted blank presentation")
                db.execute("UPDATE review_documents SET presentation = ? WHERE artifact_id = ?",
                           (value, task.artifact_id))

    def _claim(self, task: ReportTask, doc: str, baseline: ReportPresentation, *, tab: str | None = None) -> None:
        with self.store._conn(write=True) as db:
            state = self._state(db, task)
            if (state.confirmed or state.sent or state.doc_id != doc or state.presentation != _json(asdict(baseline))
                    or (tab is not None and baseline.tab_id != tab)):
                raise ReportPending("report operation already dispatched or changed")
            db.execute("UPDATE review_operations SET sent = 1 WHERE sequence = ?", (task.sequence,))

    def _round(self, report: DocsReport, task: ReportTask, doc: str, inputs: dict) -> None:
        with self.store._conn() as db:
            state = self._state(db, task)
            previous = self._round_sources(db, task.artifact_id, before=task.sequence)
            candidate, = db.execute("SELECT source FROM review_operations WHERE sequence = ?",
                                    (task.sequence,)).fetchone()
        expected = tuple(ExpectedReportRound(**item["expected"]) for item in previous)
        newest = ExpectedReportRound(**json.loads(candidate)["expected"])
        baseline = ReportPresentation(**json.loads(state.presentation)) if state.presentation else None
        if state.sent:
            if baseline is None:
                raise ReportConflict("report has no trusted blank presentation")
        else:
            if expected:
                if baseline is None:
                    raise ReportConflict("report has no trusted blank presentation")
                verified = read_report_source(report, doc, task.artifact_id, expected_rounds=expected,
                                              presentation=baseline)
                revision = verified.revision_id
            else:
                blank, revision = read_blank_report(report, doc, task.artifact_id)
                self._remember_presentation(task, doc, blank)
                baseline = blank
            if not report.append(doc, task.artifact_id, ReportRound(**inputs["candidate"]),
                                 expected_revision=revision, before_write=lambda: self._claim(task, doc, baseline)):
                raise ReportConflict("unclaimed round already present")
        # A text marker alone does not establish that the hyperlink was written.
        read_report_source(report, doc, task.artifact_id, expected_rounds=(*expected, newest), presentation=baseline)

    def _close(self, report: DocsReport, task: ReportTask, doc: str, inputs: dict) -> None:
        with self.store._conn() as db:
            state = self._state(db, task)
            source = self._round_sources(db, task.artifact_id)
        if not state.presentation or not source:
            raise ReportConflict("report has no trusted source")
        baseline = ReportPresentation(**json.loads(state.presentation))
        rows = tuple(CompactionRow(**row) for row in inputs["rows"])
        expected = tuple(ExpectedReportRound(**item["expected"]) for item in source)
        args = {"operation_key": task.key, "summary": inputs["summary"], "rows": rows, "presentation": baseline}
        if state.sent:
            verify_compacted_report(report, doc, task.artifact_id, tab_id=baseline.tab_id, **args)
        else:
            compact_report(report, doc, task.artifact_id, expected_rounds=expected,
                           before_write=lambda tab: self._claim(task, doc, baseline, tab=tab), **args)

    def _final(self, report: DocsReport, task: ReportTask, doc: str, inputs: dict, state: _State) -> None:
        rows = tuple(CompactionRow(**row) for row in inputs["rows"])
        args = {"operation_key": task.key, "summary": inputs["summary"], "rows": rows}
        if state.sent:
            if state.presentation is None:
                raise ReportConflict("report has no trusted blank presentation")
            baseline = ReportPresentation(**json.loads(state.presentation))
            verify_compacted_report(report, doc, task.artifact_id, tab_id=baseline.tab_id,
                                    presentation=baseline, **args)
            return
        baseline, revision = read_blank_report(report, doc, task.artifact_id)
        self._remember_presentation(task, doc, baseline)
        snapshot = _blank_snapshot(doc, task.artifact_id, baseline.tab_id, revision, rows)
        dispatch_verified(report, snapshot, presentation=baseline,
                          before_write=lambda tab: self._claim(task, doc, baseline, tab=tab), **args)

    def _write_notes(self, report: DocsReport, task: ReportTask, doc: str, inputs: dict) -> None:
        with self.store._conn() as db:
            state = self._state(db, task)
            previous = self._notes_sources(db, task.artifact_id, before=task.sequence)
        if task.kind == "close":
            candidate = self._notes_body(task.artifact_id, previous, summary=inputs["summary"], rows=inputs["rows"])
        else:
            candidate = self._notes_body(task.artifact_id, [*previous, inputs["notes"]])
        start = ReportPresentation(**json.loads(state.presentation)) if state.presentation else None
        if state.sent:
            if start is None:
                raise ReportConflict("report has no trusted blank presentation")
            read_notes(report, doc, task.artifact_id, expected=candidate, presentation=start)
            return
        if previous:
            if start is None:
                raise ReportConflict("report has no trusted blank presentation")
            before = self._notes_body(task.artifact_id, previous)
            _, revision, end = read_notes(report, doc, task.artifact_id, expected=before, presentation=start)
        else:
            start, revision = read_blank_report(report, doc, task.artifact_id)
            self._remember_presentation(task, doc, start)
            end = 2
        dispatch_notes(report, doc, task.artifact_id, candidate=candidate, presentation=start,
                       revision=revision, end=end, before_write=lambda tab: self._claim(task, doc, start, tab=tab))

    def execute(self, task: ReportTask, report: DocsReport) -> ReportResult:
        state = self._read(task)
        if state.confirmed:
            if state.doc_id is None:
                raise ReportConflict("confirmed report has no Doc mapping")
            return ReportResult(state.doc_id, False)
        inputs = json.loads(task.payload)
        final = task.kind == "close" and "closure_id" in inputs
        doc = self._ensure_doc(report, task, state, create=task.kind == "round" or final)
        try:
            if "notes" in inputs or inputs.get("living") is True:
                self._write_notes(report, task, doc, inputs)
            elif final:
                self._final(report, task, doc, inputs, state)
            elif task.kind == "round":
                self._round(report, task, doc, inputs)
            else:
                self._close(report, task, doc, inputs)
        except ReportLimitError:
            # A deterministic preflight limit may be fixed with a smaller
            # payload. Do not release a claim that might already be dispatched.
            with self.store._conn(write=True) as db:
                self._state(db, task)
                db.execute("DELETE FROM review_operations WHERE sequence = ? AND sent = 0 AND confirmed_at IS NULL",
                           (task.sequence,))
            raise
        with self.store._conn(write=True) as db:
            state = self._state(db, task)
            if not state.sent or state.doc_id != doc:
                raise ReportConflict("report operation has no durable dispatch claim")
            if state.confirmed:
                return ReportResult(doc, False)
            db.execute("UPDATE review_operations SET confirmed_at = ?, payload = NULL WHERE sequence = ?",
                       (self.store.clock(), task.sequence))
        return ReportResult(doc, True)


def _blank_snapshot(doc: str, artifact_id: str, tab: str, revision: str,
                    rows: tuple[CompactionRow, ...]) -> VerifiedReportSnapshot:
    # Version URLs are bound to the SQLite review, not read from the blank body.
    return VerifiedReportSnapshot(doc, artifact_id, revision, tab, 2,
                                  frozenset(row.version_url for row in rows), True)
