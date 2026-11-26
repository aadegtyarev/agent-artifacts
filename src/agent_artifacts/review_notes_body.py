"""Render the plain body and exact citations of a managed living review Doc."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .docs_report import _DOC_JSON_LIMIT, ReportLimitError, _artifact_id, _origin
from .report_compaction import _MAX_REQUEST_BYTES, _MAX_ROWS, _text, _utf16
from .store import ID_RE


@dataclass(frozen=True)
class NotesSection:
    version_id: str
    notes: tuple[str, ...]
    result: tuple[str, str] | None = None


@dataclass(frozen=True)
class NotesBody:
    text: str
    # Offsets in UTF-16 from the beginning of the text, not Docs's index 1.
    links: tuple[tuple[int, int, str], ...]

    def batch(self, *, tab: str, revision: str, end: int) -> dict:
        """Plan a replacement against a caller-verified body, tab and revision."""
        requests = []
        if end > 2:
            requests.append({"deleteContentRange": {"range": {
                "startIndex": 1, "endIndex": end - 1, "tabId": tab}}})
        requests.append({"insertText": {"location": {"index": 1, "tabId": tab}, "text": self.text}})
        for start, stop, url in self.links:
            requests.append({"updateTextStyle": {
                "range": {"startIndex": 1 + start, "endIndex": 1 + stop, "tabId": tab},
                "textStyle": {"link": {"url": url}}, "fields": "link"}})
        batch = {"writeControl": {"requiredRevisionId": revision}, "requests": requests}
        if len(json.dumps(batch, ensure_ascii=True, separators=(",", ":"))) > _MAX_REQUEST_BYTES:
            raise ReportLimitError("review notes exceed the batch size limit")
        return batch


def render_notes(origin: str, artifact_id: str, sections: tuple[NotesSection, ...], *,
                 outcome: str | None = None) -> NotesBody:
    """One section per exact version. The caller establishes the reviewed set."""
    _artifact_id(artifact_id)
    origin = _origin(origin)
    if not 1 <= len(sections) <= _MAX_ROWS:
        raise ReportLimitError("review notes have too many sections")
    if outcome is None:
        parts = ["Review notes\n"]
    else:
        _text(outcome, "review outcome")
        parts = [f"Review outcome\n{outcome}\n\nReview notes\n"]
    offset = _utf16(parts[0])
    links = []
    seen: set[str] = set()
    for section in sections:
        version = section.version_id
        if not re.fullmatch(ID_RE, version) or version in seen:
            raise ValueError("invalid or duplicate reviewed version")
        seen.add(version)
        if (outcome is None) != (section.result is None) or (outcome is None and not section.notes):
            raise ValueError("a review result belongs to the closing outcome")
        if len(section.notes) > _MAX_ROWS:
            raise ReportLimitError("review version has too many rounds")
        url = f"{origin}/a/{artifact_id}/versions/{version}"
        if len(url) > 512:
            raise ValueError("review version URL is too long")
        title = "\nReviewed version\n"
        start = offset + _utf16(title)
        links.append((start, start + _utf16(url), url))
        entries = [title, url, "\n"]
        if section.result is not None:
            decision, reason = section.result
            _text(decision, "review decision")
            _text(reason, "review reason")
            entries.append(f"Decision: {decision}\nReason: {reason}\n")
        for i, note in enumerate(section.notes, 1):
            _text(note, "round notes")
            entries.append(f"Round {i}: {note}\n")
        if not section.notes:
            entries.append("Round notes: Not recorded\n")
        body = "".join(entries)
        parts.append(body)
        offset += _utf16(body)
        if 16384 + 64 * offset > _DOC_JSON_LIMIT:
            raise ReportLimitError("review notes exceed the readback budget")
    text = "".join(parts)
    linked_runs = sum((end - start) * (len(json.dumps(url, ensure_ascii=True)) + 64) for start, end, url in links)
    if 16384 + 64 * offset + 128 * text.count("\n") + linked_runs > _DOC_JSON_LIMIT:
        raise ReportLimitError("review notes exceed the readback budget")
    return NotesBody(text, tuple(links))
