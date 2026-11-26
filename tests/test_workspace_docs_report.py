"""Native report scope, exact outcome data and read-only recovery (no network)."""

from urllib.parse import urlsplit

import pytest
from test_docs_report import ARTIFACT, URL, Fake, Response, round_
from test_report_compaction import ROWS
from test_report_compaction_dispatch import AtomicDocs
from test_report_source import expected_from_render

from agent_artifacts.docs_report import DocsReport, ReportConflict
from agent_artifacts.drive_storage import DriveAccessError, DriveStorage
from agent_artifacts.report_compaction_dispatch import CompactionUnresolved, compact_report
from agent_artifacts.report_compaction_readback import verify_compacted_report
from agent_artifacts.report_source import capture_blank_presentation, read_report_source
from agent_artifacts.review_outcome import format_outcome

FOLDER = "application/vnd.google-apps.folder"
DOC = "application/vnd.google-apps.document"
SUMMARY = {
    "chosen": {"label": "Compact toast", "reason": "Keeps more of the file list visible on a phone."},
    "alternatives": [
        {"label": "Permanent list of deletions", "reason": "Displaces the file list."},
        {"label": "Inline Undo", "reason": None},
    ],
}


class ProjectScope:
    def __init__(self, kind="my-drive"):
        super().__init__()
        self.parent = "project"
        self.document_parent = "project"
        self.parent_location = "root"
        self.parent_managed = True
        self.parent_writable = True
        self.document_tag = ARTIFACT
        self.kind = kind
        self.extra_document_parent = False
        self.native_defaults = None
        self.change_cell_padding = False

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        loc = {"driveId": "team"} if self.kind == "shared-drive" else {}
        metadata = {
            "root": {"mimeType": FOLDER, "capabilities": {"canAddChildren": True, "canListChildren": True}},
            "blob": {"mimeType": "text/plain", "parents": [self.parent], "capabilities": {"canEdit": True}},
            "project": {"mimeType": FOLDER, "parents": [self.parent_location], "capabilities": {
                "canAddChildren": self.parent_writable},
                "appProperties": {"agentArtifactsFolder": "p-" + ARTIFACT} if self.parent_managed else {}},
            "doc": {"mimeType": DOC, "parents": [self.document_parent] + (["other"] if self.extra_document_parent else []),
                    "appProperties": {"agentArtifactsReport": self.document_tag}, "capabilities": {"canEdit": True}},
        }
        if path.startswith("/drive/v3/files/"):
            assert method == "GET" and kwargs["params"]["supportsAllDrives"] == "true"
            self.calls.append((method, url, kwargs))
            object_id = path.rsplit("/", 1)[1]
            return Response({"id": object_id, "trashed": False, **loc, **metadata[object_id]})
        if path == "/drive/v3/files":
            self.calls.append((method, url, kwargs))
            if method == "POST":
                assert kwargs["json"] == {"name": "Review report", "mimeType": DOC, "parents": ["project"],
                                          "appProperties": {"agentArtifactsReport": ARTIFACT}}
                return Response({"id": "doc"})
            assert method == "GET" and "'project' in parents" in kwargs["params"]["q"]
            assert kwargs["params"]["corpora"] == ("drive" if loc else "user")
            assert kwargs["params"].get("driveId") == loc.get("driveId")
            return Response({"files": [{"id": "doc"}]})
        result = super().request(method, url, **kwargs)
        if (path == "/v1/documents/doc" and method == "GET" and self.native_defaults is not None
                and kwargs["params"].get("suggestionsViewMode") == "SUGGESTIONS_INLINE"):
            doc = result.data["tabs"][0]["documentTab"]
            doc.update(self.native_defaults)
            doc["body"]["content"][0]["sectionBreak"]["sectionStyle"] = {"columnSeparatorStyle": "NONE"}
            for item in doc["body"]["content"]:
                if "table" not in item:
                    continue
                table = item["table"]
                table["tableStyle"] = {"tableColumnProperties": [{"widthType": "EVENLY_DISTRIBUTED"}] * 3}
                for row in table["tableRows"]:
                    row["tableRowStyle"] = {"minRowHeight": {"unit": "PT"}}
                    for cell in row["tableCells"]:
                        cell["tableCellStyle"] = {"backgroundColor": {}, "contentAlignment": "TOP",
                                                  "rowSpan": 1, "columnSpan": 1}
                        for side in ("Top", "Bottom", "Left", "Right"):
                            cell["tableCellStyle"]["padding" + side] = {"magnitude": 5, "unit": "PT"}
                        if self.change_cell_padding:
                            cell["tableCellStyle"]["paddingTop"]["magnitude"] = 7
                        for paragraph in cell["content"]:
                            paragraph["paragraph"]["paragraphStyle"] = {
                                "direction": "LEFT_TO_RIGHT", "namedStyleType": "NORMAL_TEXT",
                                "alignment": "START", "lineSpacing": 100, "spacingMode": "COLLAPSE_LISTS",
                                "spaceAbove": {"unit": "PT"}, "spaceBelow": {"unit": "PT"},
                                "avoidWidowAndOrphan": False,
                            }
        return result


class ProjectFake(ProjectScope, Fake):
    pass


class ProjectAtomic(ProjectScope, AtomicDocs):
    pass


def adapter(fake):
    return DocsReport(DriveStorage(fake, "root", max_bytes=10_000, project_layout=True, folder_kind=fake.kind),
                      public_url="https://app.example", artifact_file_id="blob")


@pytest.mark.parametrize("kind", ["my-drive", "shared-drive"])
def test_create_and_find_private_report_in_the_artifacts_managed_folder(kind):
    fake = ProjectFake(kind)
    report = adapter(fake)
    assert report.create(ARTIFACT, claim=object()) == "doc"
    assert report.lookup(ARTIFACT) == "doc"
    assert report.append("doc", ARTIFACT, round_())
    assert report.version_url(ARTIFACT, "v" * 22) == URL
    assert fake.text.count(URL) == 1
    assert not any("/permissions" in url for _, url, _ in fake.calls)


@pytest.mark.parametrize(("attribute", "value"), [
    ("parent", "root"), ("parent_location", "outside"), ("parent_managed", False), ("parent_writable", False),
])
def test_managed_parent_is_required_before_creating_a_doc(attribute, value):
    fake = ProjectFake()
    setattr(fake, attribute, value)
    with pytest.raises(DriveAccessError):
        adapter(fake).create(ARTIFACT, claim=object())
    assert not any(method != "GET" for method, _, _ in fake.calls)


@pytest.mark.parametrize(("attribute", "value"), [
    ("document_parent", "root"), ("document_parent", "another-project"),
    ("document_tag", "another-artifact"), ("extra_document_parent", True),
])
def test_an_unrelated_doc_cannot_be_used_even_inside_the_same_drive(attribute, value):
    fake = ProjectFake()
    setattr(fake, attribute, value)
    report = adapter(fake)
    with pytest.raises(DriveAccessError):
        report.lookup(ARTIFACT)
    with pytest.raises(DriveAccessError):
        report.append("doc", ARTIFACT, round_())
    assert not any(method != "GET" for method, _, _ in fake.calls)


@pytest.mark.parametrize("kind", ["my-drive", "shared-drive"])
def test_final_reasons_and_exact_versions_use_same_doc_with_read_only_recovery(kind):
    fake = ProjectAtomic(kind)
    fake.native_defaults = {"documentStyle": {"marginTop": {"magnitude": 72, "unit": "PT"}},
                            "namedStyles": {"styles": [{"namedStyleType": "NORMAL_TEXT", "paragraphStyle": {
                                "alignment": "START", "lineSpacing": 115, "direction": "LEFT_TO_RIGHT",
                                "namedStyleType": "NORMAL_TEXT", "avoidWidowAndOrphan": True,
                            }}]}}
    report = adapter(fake)
    presentation = capture_blank_presentation(report, "doc", ARTIFACT)
    assert len(presentation.digest) == 64 and all(call[0] == "GET" for call in fake.calls)
    expected = []
    for i, row in enumerate(ROWS):
        draft = round_(key=f"round{i}", version_url=row.version_url, decision=row.decision, reasons=row.reason)
        assert report.append("doc", ARTIFACT, draft)
        expected.append(expected_from_render(report.render_round(ARTIFACT, draft)))
    summary = format_outcome(SUMMARY)
    assert "Chosen: Compact toast\nWhy: Keeps more" in summary
    assert "Why not: Displaces the file list." in summary
    assert "Inline Undo\n  Why not: Not recorded" in summary
    with pytest.raises(ReportConflict):
        capture_blank_presentation(report, "doc", ARTIFACT)
    with pytest.raises(ReportConflict):
        read_report_source(report, "doc", ARTIFACT, expected_rounds=tuple(expected))
    assert read_report_source(report, "doc", ARTIFACT, expected_rounds=tuple(expected),
                              presentation=presentation).version_urls == {row.version_url for row in ROWS}
    fake.lose_response = True
    tabs = []
    with pytest.raises(CompactionUnresolved):
        compact_report(report, "doc", ARTIFACT, expected_rounds=tuple(expected), operation_key="close1",
                       summary=summary, rows=ROWS, before_write=tabs.append, presentation=presentation)
    assert fake.compact_posts == 1 and len(tabs) == 1
    fake.calls.clear()
    assert verify_compacted_report(report, "doc", ARTIFACT, tab_id=tabs[0], operation_key="close1",
                                   summary=summary, rows=ROWS, presentation=presentation)
    fake.change_cell_padding = True
    with pytest.raises(ReportConflict):
        verify_compacted_report(report, "doc", ARTIFACT, tab_id=tabs[0], operation_key="close1",
                                summary=summary, rows=ROWS, presentation=presentation)
    fake.change_cell_padding = False
    fake.native_defaults["documentStyle"] = {"marginTop": {"magnitude": 8, "unit": "PT"}}
    with pytest.raises(ReportConflict):
        verify_compacted_report(report, "doc", ARTIFACT, tab_id=tabs[0], operation_key="close1",
                                summary=summary, rows=ROWS, presentation=presentation)
    assert all(method == "GET" for method, _, _ in fake.calls) and fake.compact_posts == 1


def test_unrecorded_choice_reason_is_not_invented():
    outcome = {"chosen": {"label": "Selected layout", "reason": None}, "alternatives": []}
    assert format_outcome(outcome) == "Chosen: Selected layout\nWhy: Not recorded"
    for alternative in ({"label": "selected LAYOUT", "reason": "duplicate"},
                        {"label": "next", "reason": "lines\nare not evidence"},
                        {"label": "unknown", "reason": None, "approval": True}):
        with pytest.raises(ValueError):
            format_outcome(outcome | {"alternatives": [alternative]})
