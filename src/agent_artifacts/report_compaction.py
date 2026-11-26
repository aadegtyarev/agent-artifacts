"""Pure, offline planner for a future explicitly requested Docs report compaction.

This module never reads or writes Google. Its input is a caller-verified snapshot:
the complete set of exact linked versions and an app-authored-only body in the
same tagged document and tab. Store uses a bounded Docs GET, requires an explicit
closure assertion, serializes report writes and reconciles ambiguous outcomes.
Real Google behavior and the caller's closure assertion remain unvalidated.
The planner cannot verify the semantic fidelity of caller-supplied reasons.

The index model follows the official InsertTableRequest and tables guide: inserting
at L adds a preceding newline and puts the table at S=L+1; an empty cell paragraph
starts at S+3+row*(1+2*columns)+2*column. This is an offline model, not evidence of
real tenant behavior. Preserve the final body newline when deleting old content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .docs_report import _DOC_JSON_LIMIT, ReportLimitError, _artifact_id, _origin
from .drive_storage import _reference
from .store import ID_RE

_MAX_ROWS = 128
_MAX_FIELD = 4000
_MAX_REQUEST_BYTES = 512 * 1024
_COLUMNS = 3
_MARKER = "[agent-artifacts-compact:"


class CompactionInputError(ValueError):
    """The supplied snapshot or replacement is unsafe or unsupported."""


@dataclass(frozen=True)
class VerifiedReportSnapshot:
    """External verification is mandatory; construction alone does not establish it.

    `end_index` is the exclusive Docs body end, including its mandatory final
    newline. `version_urls` contains every hyperlink from every published round;
    a future reader must reject unsupported structures and non-round hyperlinks.
    """

    document_id: str
    artifact_id: str
    revision_id: str
    tab_id: str
    end_index: int
    version_urls: frozenset[str]
    app_authored_only: bool


@dataclass(frozen=True)
class CompactionRow:
    version_url: str
    decision: str
    reason: str


@dataclass(frozen=True)
class CompactionPlan:
    document_id: str
    operation_key: str
    digest: str
    batch: dict


def _utf16(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text(value: str, name: str) -> None:
    if (not isinstance(value, str) or len(value) > _MAX_FIELD or not value.strip()
            or _MARKER in value
            or any(ord(c) <= 8 or 12 <= ord(c) <= 31
                   or 0xD800 <= ord(c) <= 0xF8FF for c in value)):
        raise CompactionInputError(f"invalid {name}")


def plan_compaction(snapshot: VerifiedReportSnapshot, *, public_url: str, operation_key: str,
                    summary: str, rows: tuple[CompactionRow, ...]) -> CompactionPlan:
    """Construct a bounded batch only; do not dispatch or infer closure.

    Existing URLs must come from an independently verified source snapshot, not
    an arbitrary caller assertion. Replacement URL sets must match exactly.
    Reasons must be nonempty; use an explicit "Not recorded" where appropriate.
    The operation marker is proposed reconciliation metadata, not proof of commit.
    """
    if not isinstance(snapshot, VerifiedReportSnapshot) or snapshot.app_authored_only is not True:
        raise CompactionInputError("verified app-authored snapshot required")
    if not isinstance(public_url, str) or len(public_url) > 300:
        raise CompactionInputError("invalid snapshot identity or origin")
    try:
        _reference(snapshot.document_id)
        _artifact_id(snapshot.artifact_id)
        _reference(operation_key)
        origin = _origin(public_url)
    except (ValueError, TypeError):
        raise CompactionInputError("invalid snapshot identity or origin") from None
    if len(operation_key) > 128:
        raise CompactionInputError("operation key is too long")
    if (any(not isinstance(v, str) or not 0 < len(v) <= 1024
            or any(not 32 <= ord(c) <= 126 for c in v)
            for v in (snapshot.revision_id, snapshot.tab_id))
            or type(snapshot.end_index) is not int
            or not 2 <= snapshot.end_index <= _DOC_JSON_LIMIT):
        raise CompactionInputError("invalid snapshot revision, tab or body end")
    if (not isinstance(snapshot.version_urls, frozenset) or not snapshot.version_urls
            or len(snapshot.version_urls) > _MAX_ROWS or not isinstance(rows, tuple)
            or not 1 <= len(rows) <= _MAX_ROWS):
        raise CompactionInputError("invalid version links or table size")
    _text(summary, "summary")
    pattern = re.compile(re.escape(origin) + rf"/a/{snapshot.artifact_id}/versions/{ID_RE}")
    if any(not isinstance(url, str) or len(url) > 512 or not pattern.fullmatch(url)
           for url in snapshot.version_urls):
        raise CompactionInputError("invalid verified version link")
    for row in rows:
        if (not isinstance(row, CompactionRow) or not isinstance(row.version_url, str)
                or len(row.version_url) > 512):
            raise CompactionInputError("invalid version row")
        _text(row.decision, "decision")
        _text(row.reason, "reason")
        if not pattern.fullmatch(row.version_url):
            raise CompactionInputError("invalid replacement version link")
    if {row.version_url for row in rows} != snapshot.version_urls:
        raise CompactionInputError("replacement links differ from the verified report")

    canonical = json.dumps([summary, [[r.version_url, r.decision, r.reason] for r in rows]],
                           ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    heading = f"{_MARKER}{operation_key}:{digest}]\n{summary}\n"
    cells = [("Version", "Decision", "Reason"), *(
        (row.version_url, row.decision, row.reason) for row in rows)]
    total_units = _utf16(heading) + sum(_utf16(cell) for row in cells for cell in row)
    newlines = heading.count("\n") + sum(cell.count("\n") for row in cells for cell in row)
    # Charge one text run per UTF-16 unit, including repeated escaped link URLs
    # and their style wrappers in every linked run in a projected Docs response.
    linked_runs = sum(_utf16(row.version_url) *
                      (len(json.dumps(row.version_url, ensure_ascii=True)) + 64) for row in rows)
    if (16384 + 64 * total_units + 128 * newlines + 1024 * len(cells) + linked_runs
            > _DOC_JSON_LIMIT):
        raise ReportLimitError("compacted report exceeds reconciliation read budget")
    tab = snapshot.tab_id
    requests = []
    if snapshot.end_index > 2:
        requests.append({"deleteContentRange": {"range": {
            "startIndex": 1, "endIndex": snapshot.end_index - 1, "tabId": tab}}})
    requests.append({"insertText": {"location": {"index": 1, "tabId": tab}, "text": heading}})
    location = 1 + _utf16(heading)
    requests.append({"insertTable": {"rows": len(cells), "columns": _COLUMNS,
                                     "location": {"index": location, "tabId": tab}}})
    start = location + 1
    for r in reversed(range(len(cells))):
        for c in reversed(range(_COLUMNS)):
            text = cells[r][c]
            index = start + 3 + r * (1 + 2 * _COLUMNS) + 2 * c
            requests.append({"insertText": {"location": {"index": index, "tabId": tab},
                                             "text": text}})
            if r and c == 0:
                requests.append({"updateTextStyle": {
                    "range": {"startIndex": index, "endIndex": index + _utf16(text),
                              "tabId": tab},
                    "textStyle": {"link": {"url": text}}, "fields": "link"}})
    batch = {"writeControl": {"requiredRevisionId": snapshot.revision_id}, "requests": requests}
    if len(json.dumps(batch, ensure_ascii=True, separators=(",", ":")).encode("ascii")) > (
            _MAX_REQUEST_BYTES):
        raise ReportLimitError("compaction request exceeds size limit")
    return CompactionPlan(snapshot.document_id, operation_key, digest, batch)
