"""Read-only verification of app-authored Docs report rounds for compaction.

Trusted expected round keys, content digests and exact version URLs must come from
independent durable application metadata, not from the Doc being inspected. This
reader cannot establish closure, permission to compact, reason fidelity, or lock out
concurrent writers. Only an intentionally narrow tested presentation baseline is
supported; older, manually styled or richer Docs can be rejected. Native
Docs defaults are accepted only if they match a baseline captured before the
first append and independently persisted with the application's Doc mapping.
No write or compaction dispatch occurs here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass

from .docs_report import _DOC_JSON_LIMIT, DocsReport, ReportConflict, _artifact_id
from .drive_storage import _reference, _segment
from .report_compaction import VerifiedReportSnapshot
from .store import ID_RE

_FIELDS = "revisionId,tabs(tabProperties(tabId),childTabs,documentTab)"
# Even the shortest rendered round costs far more than 64 bytes in a bounded GET.
_MAX_ROUNDS = _DOC_JSON_LIMIT // 64


@dataclass(frozen=True)
class ExpectedReportRound:
    """Trusted writer metadata, persisted in confirmed append order independently of Docs.

    Length and link offsets are UTF-16 units relative to the start of the frame;
    digest is SHA-256 of the UTF-8 body between the framing marker lines.
    """

    key: str
    digest: str
    version_url: str
    framed_utf16_length: int
    link_start: int
    link_end: int


@dataclass(frozen=True)
class ReportPresentation:
    """Initial presentation of the app-owned blank Doc, tied to its mapped tab."""

    document_id: str
    artifact_id: str
    tab_id: str
    digest: str


def _no_suggestions(value: object) -> None:
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, item in node.items():
                if key.startswith("suggested") and item:
                    raise ReportConflict("report has unsupported suggestions")
                stack.append(item)
        elif isinstance(node, list):
            stack.extend(node)


def _presentation_parts(doc: dict, section: dict) -> tuple[dict, dict, dict]:
    if (not {"endIndex", "sectionBreak"} <= set(section)
            or not set(section) <= {"startIndex", "endIndex", "sectionBreak"}
            or section.get("startIndex", 0) != 0 or section["endIndex"] != 1):
        raise ValueError
    value = section.get("sectionBreak")
    if not isinstance(value, dict) or not set(value) <= {"sectionStyle"}:
        raise ValueError
    parts = doc.get("documentStyle", {}), doc.get("namedStyles", {}), value.get("sectionStyle", {})
    if any(not isinstance(part, dict) for part in parts):
        raise ValueError
    return parts


def _presentation_digest(parts: tuple[dict, dict, dict]) -> str:
    value = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _validate_document_presentation(doc: dict, section: dict, presentation: ReportPresentation | None, *,
                                    document_id: str, artifact_id: str, tab_id: str) -> None:
    parts = _presentation_parts(doc, section)
    if presentation is None:
        if any(parts):
            raise ValueError
    elif (not isinstance(presentation, ReportPresentation)
          or (presentation.document_id, presentation.artifact_id, presentation.tab_id)
          != (document_id, artifact_id, tab_id) or presentation.digest != _presentation_digest(parts)):
        raise ValueError


def _report_document(report: DocsReport, document_id: str, artifact_id: str) -> dict:
    drive_id = report._drive.validate_folder(write=False)
    report._validate(document_id, artifact_id, drive_id)
    return report._json_request("GET", f"https://docs.googleapis.com/v1/documents/{_segment(document_id)}",
                                params={"includeTabsContent": "true", "fields": _FIELDS,
                                        "prettyPrint": "false", "suggestionsViewMode": "SUGGESTIONS_INLINE"},
                                headers={"Accept-Encoding": "identity"}, document=True)


def _single_tab(data: dict) -> tuple[str, str, dict]:
    _no_suggestions(data)
    tabs = data["tabs"]
    if (not isinstance(tabs, list) or len(tabs) != 1 or tabs[0].get("childTabs")
            or not isinstance(tabs[0]["documentTab"], dict)):
        raise ValueError
    tab = tabs[0]
    tab_id, revision = tab["tabProperties"]["tabId"], data["revisionId"]
    if any(not isinstance(v, str) or not 0 < len(v) <= 1024
           or any(not 32 <= ord(c) <= 126 for c in v) for v in (tab_id, revision)):
        raise ValueError
    doc = tab["documentTab"]
    if any(doc.get(k) for k in ("headers", "footers", "footnotes", "inlineObjects",
                                 "positionedObjects", "namedRanges", "lists")):
        raise ValueError
    return tab_id, revision, doc


def capture_blank_presentation(report: DocsReport, document_id: str, artifact_id: str) -> ReportPresentation:
    """Capture immediately after verified create, before any append."""
    return read_blank_report(report, document_id, artifact_id)[0]


def read_blank_report(report: DocsReport, document_id: str, artifact_id: str) -> tuple[ReportPresentation, str]:
    """Read the verified blank presentation and revision for a guarded append."""
    _reference(document_id)
    _artifact_id(artifact_id)
    try:
        tab, revision, doc = _single_tab(_report_document(report, document_id, artifact_id))
        section, blank = doc["body"]["content"]
        parts = _presentation_parts(doc, section)
        if (set(blank) != {"startIndex", "endIndex", "paragraph"}
                or blank["startIndex"] != 1 or blank["endIndex"] != 2):
            raise ValueError
        paragraph = blank["paragraph"]
        if not set(paragraph) <= {"elements", "paragraphStyle"} or not _normal_paragraph(paragraph):
            raise ValueError
        element, = paragraph["elements"]
        if (set(element) != {"startIndex", "endIndex", "textRun"}
                or element["startIndex"] != 1 or element["endIndex"] != 2):
            raise ValueError
        run = element["textRun"]
        if (not set(run) <= {"content", "textStyle"} or run["content"] != "\n"
                or not isinstance(run.get("textStyle", {}), dict)
                or not _supported_style(run.get("textStyle", {}), linked=False)):
            raise ValueError
        return ReportPresentation(document_id, artifact_id, tab, _presentation_digest(parts)), revision
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, IndexError):
        raise ReportConflict("new report is not the expected blank document") from None


def _normal_paragraph(paragraph: dict) -> bool:
    style = paragraph.get("paragraphStyle", {})
    return (isinstance(style, dict) and set(style) <= {"namedStyleType", "direction"}
            and style.get("namedStyleType", "NORMAL_TEXT") == "NORMAL_TEXT"
            and style.get("direction", "LEFT_TO_RIGHT") == "LEFT_TO_RIGHT")


def _supported_style(style: dict, *, linked: bool) -> bool:
    """Strict offline baseline; Google's real default styles need separate validation."""
    if not set(style) <= {"link", "underline", "foregroundColor", "bold", "italic",
                          "strikethrough", "smallCaps"}:
        return False
    if any(style.get(key, False) is not False
           for key in ("bold", "italic", "strikethrough", "smallCaps")):
        return False
    if "underline" in style and (not linked or style["underline"] is not True):
        return False
    if "foregroundColor" in style:
        if not linked:
            return False
        color = style["foregroundColor"]
        if (not isinstance(color, dict) or set(color) != {"color"}
                or not isinstance(color["color"], dict) or set(color["color"]) != {"rgbColor"}):
            return False
        rgb = color["color"]["rgbColor"]
        if (not isinstance(rgb, dict) or set(rgb) != {"red", "green", "blue"}
                or any(type(rgb[k]) not in (int, float) or not 0 <= rgb[k] <= 1
                       or (isinstance(rgb[k], float) and not math.isfinite(rgb[k])) for k in rgb)):
            return False
        if any(abs(rgb[k] - target) > 0.002 for k, target in
               (("red", 1 / 15), ("green", 1 / 3), ("blue", 0.8))):
            return False
    return True


def read_report_source(report: DocsReport, document_id: str, artifact_id: str, *,
                       expected_rounds: tuple[ExpectedReportRound, ...],
                       presentation: ReportPresentation | None = None) -> VerifiedReportSnapshot:
    """Read and strictly verify one tagged Doc, returning a planner snapshot.

    The caller must supply the complete authoritative round list, keep it stable
    across this read and any future write, and validate the artifact's publication
    and authorization separately. A marker found in the Doc is never authority.
    """
    _reference(document_id)
    _artifact_id(artifact_id)
    if not isinstance(expected_rounds, tuple) or not 1 <= len(expected_rounds) <= _MAX_ROUNDS:
        raise ValueError("trusted expected rounds required")
    expected_keys = set()
    url_pattern = re.compile(re.escape(report._origin) + rf"/a/{artifact_id}/versions/{ID_RE}")
    for item in expected_rounds:
        if not isinstance(item, ExpectedReportRound):
            raise ValueError("invalid expected round")
        _reference(item.key)
        if (not isinstance(item.digest, str) or not re.fullmatch(r"[a-f0-9]{64}", item.digest)
                or not isinstance(item.version_url, str) or len(item.version_url) > 512
                or not url_pattern.fullmatch(item.version_url) or item.key in expected_keys
                or any(type(value) is not int for value in
                       (item.framed_utf16_length, item.link_start, item.link_end))
                or not 0 < item.link_start < item.link_end < item.framed_utf16_length
                or item.framed_utf16_length > _DOC_JSON_LIMIT
                or item.link_end - item.link_start
                != len(item.version_url.encode("utf-16-le")) // 2):
            raise ValueError("invalid or duplicate expected round")
        expected_keys.add(item.key)

    data = _report_document(report, document_id, artifact_id)
    try:
        tab_id, revision, doc = _single_tab(data)
        elements = doc["body"]["content"]
        if not isinstance(elements, list) or len(elements) < 2:
            raise ValueError
        _validate_document_presentation(doc, elements[0], presentation, document_id=document_id,
                                        artifact_id=artifact_id, tab_id=tab_id)
        text_parts = []
        links = []
        utf16_index = 1
        for element in elements[1:]:
            if set(element) != {"startIndex", "endIndex", "paragraph"}:
                raise ValueError
            para = element["paragraph"]
            if (not set(para) <= {"elements", "paragraphStyle", "bullet", "positionedObjectIds",
                                  "suggestedParagraphStyleChanges"}
                    or para.get("bullet") or para.get("positionedObjectIds")):
                raise ValueError
            if not _normal_paragraph(para) or element["startIndex"] != utf16_index:
                raise ValueError
            paragraph_parts = []
            for part in para["elements"]:
                if set(part) != {"startIndex", "endIndex", "textRun"}:
                    raise ValueError
                run = part["textRun"]
                if not set(run) <= {"content", "textStyle", "suggestedTextStyleChanges"}:
                    raise ValueError
                content = run["content"]
                if not isinstance(content, str) or not content or part["startIndex"] != utf16_index:
                    raise ValueError
                utf16_index += len(content.encode("utf-16-le")) // 2
                if part["endIndex"] != utf16_index:
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
                    start, end, url = part["startIndex"] - 1, part["endIndex"] - 1, link["url"]
                    if links and links[-1][1] == start and links[-1][2] == url:
                        links[-1] = (links[-1][0], end, url)
                    else:
                        links.append((start, end, url))
                paragraph_parts.append(content)
            paragraph = "".join(paragraph_parts)
            if (not paragraph.endswith("\n") or paragraph.count("\n") != 1
                    or element["endIndex"] != utf16_index):
                raise ValueError
            text_parts.append(paragraph)
        text = "".join(text_parts)
        if not text.endswith("\n"):
            raise ValueError
        encoded = text.encode("utf-16-le")
        pos, required_links = 0, []
        for trusted in expected_rounds:
            end = pos + trusted.framed_utf16_length
            if end > len(encoded) // 2:
                raise ValueError
            frame = encoded[2 * pos:2 * end].decode("utf-16-le")
            head = f"[agent-artifacts-round:{trusted.key}:{trusted.digest}]\n"
            tail = f"[agent-artifacts-end:{trusted.key}]\n"
            if not frame.startswith(head) or not frame.endswith(tail):
                raise ValueError
            content = frame[len(head):-len(tail)]
            link_start, link_end = pos + trusted.link_start, pos + trusted.link_end
            url = encoded[2 * link_start:2 * link_end].decode("utf-16-le")
            if (url != trusted.version_url or not content.startswith("Goal: ")
                    or any(ord(c) <= 8 or 12 <= ord(c) <= 31
                           or 0xD800 <= ord(c) <= 0xF8FF for c in content)
                    or hashlib.sha256(content.encode("utf-8")).hexdigest() != trusted.digest):
                raise ValueError
            required_links.append((link_start, link_end, url))
            pos = end
        if encoded[2 * pos:] != b"\n\x00" or links != required_links:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeEncodeError):
        raise ReportConflict("unsupported, modified or unverified report source") from None
    return VerifiedReportSnapshot(document_id, artifact_id, revision, tab_id, utf16_index,
                                  frozenset(item.version_url for item in expected_rounds), True)
