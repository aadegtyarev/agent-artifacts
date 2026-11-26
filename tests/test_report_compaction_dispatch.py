"""Offline standalone compaction with a sentinel-backed atomic Docs simulation."""

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from test_docs_report import ARTIFACT, Response
from test_report_compaction import ROWS, AtomicTableModel, chars
from test_report_compaction_readback import SUMMARY, model_document, table
from test_report_source import AppendingTransport, expected_from_render

from agent_artifacts.docs_report import DocsReport, ReportConflict, ReportRound
from agent_artifacts.drive_storage import DriveAccessError
from agent_artifacts.report_compaction_dispatch import CompactionUnresolved, compact_report
from agent_artifacts.report_compaction_readback import verify_compacted_report


class AtomicDocs(AppendingTransport):
    def __init__(self):
        super().__init__()
        self.compacted = None
        self.lose_response = False
        self.tamper = False
        self.unavailable_after_write = False
        self.compact_posts = 0

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        if path == "/v1/documents/doc" and method == "GET" and self.compacted is not None:
            if self.unavailable_after_write:
                raise requests.Timeout("private read error")
            assert kwargs["params"]["fields"] == (
                "revisionId,tabs(tabProperties(tabId),childTabs,documentTab)"
            )
            data = model_document(self.compacted)
            if self.tamper:
                cell = table(data)["tableRows"][1]["tableCells"][2]
                run = cell["content"][0]["paragraph"]["elements"][0]["textRun"]
                run["content"] = run["content"].replace("Reason", "Change")
            response = Response(data)
            self.calls.append((method, url, kwargs))
            self.responses.append(response)
            return response
        if (path == "/v1/documents/doc:batchUpdate" and method == "POST"
                and any("insertTable" in r for r in kwargs["json"]["requests"])):
            assert kwargs["stream"] and not kwargs["allow_redirects"]
            self.calls.append((method, url, kwargs))
            self.compact_posts += 1
            model = AtomicTableModel()
            model.body = chars(self.text)
            model.revision = f"r{self.revision}"
            try:
                model.apply(SimpleNamespace(document_id="doc", batch=kwargs["json"]))
            except ValueError:
                response = Response({}, status=400)
                self.responses.append(response)
                return response
            self.compacted = model
            if self.lose_response:
                raise requests.Timeout("private write error")
            response = Response({})
            self.responses.append(response)
            return response
        return super().request(method, url, **kwargs)


def setup_doc():
    fake = AtomicDocs()
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    expected = []
    for i, row in enumerate(ROWS):
        item = ReportRound(f"r{i}", row.version_url, "Цель 😀\nmore", row.decision,
                           row.reason, "open\nitem")
        expected.append(expected_from_render(report.render_round(ARTIFACT, item)))
        assert report.append("doc", ARTIFACT, item)
    fake.calls.clear()
    return fake, report, tuple(expected)


def run(report, expected, before_write):
    return compact_report(report, "doc", ARTIFACT, expected_rounds=expected,
                          operation_key="close1", summary=SUMMARY, rows=ROWS,
                          before_write=before_write)


def verify(report, tab_id):
    return verify_compacted_report(report, "doc", ARTIFACT, tab_id=tab_id, operation_key="close1",
                                   summary=SUMMARY, rows=ROWS)


def test_explicit_dispatch_once_and_verify_exact_result():
    fake, report, expected = setup_doc()
    claims = []
    result = run(report, expected, lambda tab: claims.append(tab))
    assert claims == ["tab"] and result.document_id == "doc"
    assert fake.compact_posts == 1 and fake.compacted is not None
    post = next(call for call in fake.calls if call[0] == "POST")
    assert post[1].endswith("/documents/doc:batchUpdate")
    assert post[2]["json"]["writeControl"] == {"requiredRevisionId": "r3"}
    assert verify(report, claims[0]) and fake.compact_posts == 1


def test_stale_revision_is_unresolved_and_original_remains_intact():
    fake, report, expected = setup_doc()
    original = fake.text
    def competing_change(tab):
        fake.revision += 1
    with pytest.raises(CompactionUnresolved):
        run(report, expected, competing_change)
    assert fake.compact_posts == 1 and fake.compacted is None and fake.text == original


def test_claim_denied_before_post_and_no_claim_is_rejected():
    fake, report, expected = setup_doc()
    def deny(tab):
        raise RuntimeError("claim denied")
    with pytest.raises(RuntimeError, match="claim denied"):
        run(report, expected, deny)
    with pytest.raises(ValueError, match="claim required"):
        run(report, expected, None)
    assert fake.compact_posts == 0


def test_lost_response_is_unresolved_and_separate_readback_does_not_replay():
    fake, report, expected = setup_doc()
    fake.lose_response = True
    captured = []
    with pytest.raises(CompactionUnresolved, match="not confirmed"):
        run(report, expected, captured.append)
    assert fake.compact_posts == 1 and fake.compacted is not None
    assert verify(report, captured[0]) and verify(report, captured[0])
    assert fake.compact_posts == 1


@pytest.mark.parametrize("failure", ["tamper", "unavailable"])
def test_unconfirmed_readback_is_unresolved_without_replay(failure):
    fake, report, expected = setup_doc()
    fake.tamper = failure == "tamper"
    fake.unavailable_after_write = failure == "unavailable"
    captured = []
    with pytest.raises(CompactionUnresolved):
        run(report, expected, captured.append)
    assert fake.compact_posts == 1 and fake.compacted is not None
    if failure == "unavailable":
        fake.unavailable_after_write = False
        assert verify(report, captured[0])
    else:
        with pytest.raises(ReportConflict):
            verify(report, captured[0])
    assert fake.compact_posts == 1


def test_bad_tag_and_untrusted_source_fail_before_claim_or_post():
    from dataclasses import replace

    fake, report, expected = setup_doc()
    fake.bad_tag = True
    claims = []
    with pytest.raises(DriveAccessError):
        run(report, expected, lambda tab: claims.append(tab))
    assert not any("/v1/documents/" in call[1] for call in fake.calls)
    fake.bad_tag = False
    with pytest.raises(ReportConflict):
        run(report, (replace(expected[0], digest="0" * 64), expected[1]),
            lambda tab: claims.append(tab))
    assert claims == [] and fake.compact_posts == 0


def test_source_get_unavailable_fails_before_claim_or_compact_post():
    from agent_artifacts.drive_storage import DriveTransientError

    fake, report, expected = setup_doc()
    original = fake.request

    def unavailable(method, url, **kwargs):
        if urlsplit(url).path == "/v1/documents/doc":
            raise requests.Timeout("private")
        return original(method, url, **kwargs)

    fake.request = unavailable
    claims = []
    with pytest.raises(DriveTransientError):
        run(report, expected, lambda tab: claims.append(tab))
    assert claims == [] and fake.compact_posts == 0
