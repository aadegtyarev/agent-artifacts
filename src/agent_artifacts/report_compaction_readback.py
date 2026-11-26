"""Read-only, fail-closed inspection of a proposed compacted report.

This module never dispatches a batch. Callers must independently preserve the
exact operation key, summary and ordered rows across an uncertain outcome. A
successful inspection proves only that the currently read Doc matches those
inputs; it is not a compaction claim, authorization, or provenance proof.
The accepted table structure and cell presentation are deliberately narrow.
Google's observed native defaults require a trusted blank baseline.
"""

from __future__ import annotations

from .docs_report import DocsReport, ReportConflict, _artifact_id
from .drive_storage import _reference
from .report_compaction import (
    _MARKER,
    CompactionRow,
    VerifiedReportSnapshot,
    _utf16,
    plan_compaction,
)
from .report_source import (
    ReportPresentation,
    _normal_paragraph,
    _report_document,
    _single_tab,
    _supported_style,
    _validate_document_presentation,
)


_TABLE_DEFAULT = {"tableColumnProperties": [{"widthType": "EVENLY_DISTRIBUTED"}] * 3}
_ROW_DEFAULT = {"minRowHeight": {"unit": "PT"}}
_CELL_DEFAULT = {
    "backgroundColor": {}, "columnSpan": 1, "rowSpan": 1, "contentAlignment": "TOP",
    **{f"padding{side}": {"magnitude": 5, "unit": "PT"} for side in ("Top", "Bottom", "Left", "Right")},
}
_CELL_PARAGRAPH_OVERRIDES = {
    "avoidWidowAndOrphan": False, "lineSpacing": 100, "spaceAbove": {"unit": "PT"},
    "spaceBelow": {"unit": "PT"}, "spacingMode": "COLLAPSE_LISTS",
}


def _matches_style(value: dict, key: str, default: dict | None) -> bool:
    style = value.get(key, {})
    return isinstance(style, dict) and (not style or style == default)


def _native_cell_paragraph_style(doc: dict) -> dict | None:
    # Google expands cell styles from the document's frozen NORMAL_TEXT style
    # and applies InsertTable's known spacing defaults. Do not trust styles
    # from the cells themselves as evidence of what the writer requested.
    styles = doc.get("namedStyles", {}).get("styles", [])
    if not isinstance(styles, list):
        return None
    normal = [row.get("paragraphStyle") for row in styles
              if isinstance(row, dict) and row.get("namedStyleType") == "NORMAL_TEXT"]
    if (len(normal) != 1 or not isinstance(normal[0], dict)
            or normal[0].get("namedStyleType") != "NORMAL_TEXT" or normal[0].get("direction") != "LEFT_TO_RIGHT"):
        return None
    return normal[0] | _CELL_PARAGRAPH_OVERRIDES


def _paragraphs(elements, start, *, native_cell_style: dict | None = None):
    """Read contiguous ordinary paragraphs and exact absolute link ranges."""
    text, links, index = [], [], start
    for element in elements:
        if set(element) != {"startIndex", "endIndex", "paragraph"}:
            raise ValueError
        para = element["paragraph"]
        if (not set(para) <= {"elements", "paragraphStyle", "bullet", "positionedObjectIds",
                              "suggestedParagraphStyleChanges"}
                or para.get("bullet") or para.get("positionedObjectIds")):
            raise ValueError
        ordinary = _normal_paragraph(para)
        native = native_cell_style is not None and para.get("paragraphStyle") == native_cell_style
        if (not ordinary and not native) or element["startIndex"] != index:
            raise ValueError
        pieces = []
        for part in para["elements"]:
            if set(part) != {"startIndex", "endIndex", "textRun"}:
                raise ValueError
            run = part["textRun"]
            if not set(run) <= {"content", "textStyle", "suggestedTextStyleChanges"}:
                raise ValueError
            content = run["content"]
            if not isinstance(content, str) or not content or part["startIndex"] != index:
                raise ValueError
            index += _utf16(content)
            if part["endIndex"] != index:
                raise ValueError
            style = run.get("textStyle", {})
            if not isinstance(style, dict):
                raise ValueError
            link = style.get("link")
            if not _supported_style(style, linked=link is not None):
                raise ValueError
            if link is not None:
                if (not isinstance(link, dict) or set(link) != {"url"}
                        or not isinstance(link["url"], str)):
                    raise ValueError
                span = (part["startIndex"], part["endIndex"], link["url"])
                if links and links[-1][1] == span[0] and links[-1][2] == span[2]:
                    links[-1] = (links[-1][0], span[1], span[2])
                else:
                    links.append(span)
            pieces.append(content)
        paragraph = "".join(pieces)
        if (not paragraph.endswith("\n") or paragraph.count("\n") != 1
                or element["endIndex"] != index):
            raise ValueError
        text.append(paragraph)
    return "".join(text), links, index


def verify_compacted_report(report: DocsReport, document_id: str, artifact_id: str, *,
                            tab_id: str, operation_key: str, summary: str,
                            rows: tuple[CompactionRow, ...], presentation: ReportPresentation | None = None) -> bool:
    """Bounded GET and compare every expected heading, table cell and link.

    True means a matching current result was observed. Mismatch or unavailable
    data raises; neither is proof that a previously attempted batch failed. This
    function never writes or retries. Inputs must be independently trusted.
    """
    _reference(document_id)
    _artifact_id(artifact_id)
    # Reuse the planner's URL/text/budget validation and canonical operation digest.
    try:
        urls = frozenset(row.version_url for row in rows)
    except (AttributeError, TypeError):
        raise ValueError("invalid expected compaction rows") from None
    expected = plan_compaction(
        VerifiedReportSnapshot(document_id, artifact_id, "inspection", tab_id, 2, urls, True),
        public_url=report._origin, operation_key=operation_key, summary=summary, rows=rows,
    )
    heading = f"{_MARKER}{operation_key}:{expected.digest}]\n{summary}\n\n"
    cells = [("Version", "Decision", "Reason"), *(
        (row.version_url, row.decision, row.reason) for row in rows)]
    data = _report_document(report, document_id, artifact_id)
    try:
        actual_tab, _, doc = _single_tab(data)
        elements = doc["body"]["content"]
        if actual_tab != tab_id or not isinstance(elements, list) or len(elements) < 4:
            raise ValueError
        _validate_document_presentation(doc, elements[0], presentation, document_id=document_id,
                                        artifact_id=artifact_id, tab_id=tab_id)
        native_style = _native_cell_paragraph_style(doc) if presentation is not None else None
        table_positions = [i for i, element in enumerate(elements) if "table" in element]
        if len(table_positions) != 1:
            raise ValueError
        at = table_positions[0]
        if at < 2 or len(elements) != at + 2:
            raise ValueError
        before, links, index = _paragraphs(elements[1:at], 1)
        if before != heading or links:
            raise ValueError
        element = elements[at]
        if (set(element) != {"startIndex", "endIndex", "table"}
                or element["startIndex"] != index):
            raise ValueError
        table = element["table"]
        if (not set(table) <= {"rows", "columns", "tableRows", "tableStyle"}
                or not _matches_style(table, "tableStyle", _TABLE_DEFAULT if native_style else None)
                or table["rows"] != len(cells)
                or table["columns"] != 3 or len(table["tableRows"]) != len(cells)):
            raise ValueError
        index += 1  # table start boundary
        for r, row in enumerate(table["tableRows"]):
            if (not set(row) <= {"startIndex", "endIndex", "tableCells", "tableRowStyle"}
                    or not _matches_style(row, "tableRowStyle", _ROW_DEFAULT if native_style else None)
                    or row["startIndex"] != index
                    or len(row["tableCells"]) != 3):
                raise ValueError
            index += 1  # row start boundary
            for c, cell in enumerate(row["tableCells"]):
                if (not set(cell) <= {"startIndex", "endIndex", "content", "tableCellStyle"}
                        or not _matches_style(cell, "tableCellStyle", _CELL_DEFAULT if native_style else None)
                        or cell["startIndex"] != index):
                    raise ValueError
                index += 1  # cell start boundary
                cell_start = index
                cell_text, cell_links, index = _paragraphs(cell["content"], index, native_cell_style=native_style)
                if cell_text != cells[r][c] + "\n" or cell["endIndex"] != index:
                    raise ValueError
                required = ([(cell_start, cell_start + _utf16(cells[r][c]), cells[r][c])]
                            if r and c == 0 else [])
                if cell_links != required:
                    raise ValueError
            if row["endIndex"] != index:
                raise ValueError
        index += 1  # table end boundary
        if element["endIndex"] != index:
            raise ValueError
        after, links, _ = _paragraphs(elements[at + 1:], index)
        if after != "\n" or links:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, IndexError):
        raise ReportConflict("compacted report is unsupported or does not match") from None
    return True
