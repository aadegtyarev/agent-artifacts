"""Strict, revision-guarded writes and readback for living review notes."""
from __future__ import annotations

from collections.abc import Callable

from .docs_report import _DOCS, DocsReport, ReportConflict
from .drive_storage import DriveError, _segment
from .report_compaction_dispatch import CompactionUnresolved
from .report_source import (ReportPresentation, _plain_body, _report_document, _single_tab,
                            _validate_document_presentation)
from .review_notes_body import NotesBody


def read_notes(report: DocsReport, doc_id: str, artifact_id: str, *, expected: NotesBody,
               presentation: ReportPresentation) -> tuple[str, str, int]:
    """Read the exact app-owned body and links or reject external changes."""
    data = _report_document(report, doc_id, artifact_id)
    try:
        tab, revision, doc = _single_tab(data)
        elements = doc["body"]["content"]
        if not isinstance(elements, list) or len(elements) < 2:
            raise ValueError
        _validate_document_presentation(doc, elements[0], presentation, document_id=doc_id,
                                        artifact_id=artifact_id, tab_id=tab)
        text, links, end = _plain_body(elements)
        if text != expected.text + "\n" or links != expected.links:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ReportConflict("review notes were modified or could not be verified") from None
    return tab, revision, end


def dispatch_notes(report: DocsReport, doc_id: str, artifact_id: str, *, candidate: NotesBody,
                   presentation: ReportPresentation, revision: str, end: int,
                   before_write: Callable[[str], None]) -> None:
    """Dispatch only once after persisting a claim; confirm complete readback."""
    tab = presentation.tab_id
    batch = candidate.batch(tab=tab, revision=revision, end=end)
    before_write(tab)
    try:
        report._json_request("POST", f"{_DOCS}/{_segment(doc_id)}:batchUpdate", json=batch)
        read_notes(report, doc_id, artifact_id, expected=candidate, presentation=presentation)
    except (DriveError, ValueError):
        raise CompactionUnresolved("review notes outcome is not confirmed") from None
