"""Offline read-back from a separately constructed atomic table model."""

import pytest
from test_report_compaction import ARTIFACT, ROWS, AtomicTableModel, decode, plan
from test_report_source import SourceTransport

from agent_artifacts.docs_report import DocsReport, ReportConflict
from agent_artifacts.drive_storage import DriveProtocolError
from agent_artifacts.report_compaction_readback import verify_compacted_report

SUMMARY = "Итог 😀\nDecision summary"


def paragraphs(tokens, index):
    """Project model code units into Docs paragraphs and text runs."""
    elements, pieces, run, run_link = [], [], [], None
    para_start = run_start = index

    def flush_run():
        nonlocal run, run_start
        if run:
            text_run = {"content": decode(run)}
            if run_link is not None:
                text_run["textStyle"] = {"link": {"url": run_link}}
            pieces.append({"startIndex": run_start, "endIndex": index, "textRun": text_run})
            run = []
        run_start = index

    for token in tokens:
        if token["kind"] == "cell_newline":
            value, link = 10, None
        else:
            assert token["kind"] == "char"
            value, link = token["unit"], token["link"]
        if run and link != run_link:
            flush_run()
        run_link = link
        run.append(value)
        index += 1
        if value == 10:
            flush_run()
            elements.append({"startIndex": para_start, "endIndex": index,
                             "paragraph": {"elements": pieces, "paragraphStyle": {}}})
            pieces = []
            para_start = index
    assert not run and not pieces
    return elements, index


def model_document(model):
    """Project explicit model sentinels, rather than use planner index arithmetic."""
    body = model.body
    pos = next(i for i, x in enumerate(body) if x["kind"] == "table_open")
    prefix, index = paragraphs(body[:pos], 1)
    table_start = index
    assert body[pos]["kind"] == "table_open"
    pos += 1
    index += 1
    table_rows = []
    while body[pos]["kind"] == "row_open":
        row_start = index
        pos += 1
        index += 1
        cells = []
        while body[pos]["kind"] == "cell_open":
            cell_start = index
            pos += 1
            index += 1
            first = pos
            while body[pos]["kind"] != "cell_newline":
                pos += 1
            content, index = paragraphs(body[first:pos + 1], index)
            pos += 1
            cells.append({"startIndex": cell_start, "endIndex": index, "content": content})
        table_rows.append({"startIndex": row_start, "endIndex": index, "tableCells": cells})
    assert body[pos]["kind"] == "table_close"
    pos += 1
    index += 1
    table = {"startIndex": table_start, "endIndex": index,
             "table": {"rows": len(table_rows), "columns": 3, "tableRows": table_rows}}
    suffix, index = paragraphs(body[pos:], index)
    return {"revisionId": model.revision, "tabs": [{"tabProperties": {"tabId": "tab"},
            "documentTab": {"body": {"content": [
                {"endIndex": 1, "sectionBreak": {}}, *prefix, table, *suffix]}}}]}


def setup_read():
    model = AtomicTableModel()
    candidate = plan(model)
    model.apply(candidate)
    data = model_document(model)
    fake = SourceTransport(data)
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    return fake, report, data


def verify(report, **kwargs):
    args = dict(tab_id="tab", operation_key="close1", summary=SUMMARY, rows=ROWS)
    return verify_compacted_report(report, "doc", ARTIFACT, **(args | kwargs))


def table(data):
    return next(element["table"] for element in
                data["tabs"][0]["documentTab"]["body"]["content"] if "table" in element)


def test_successful_readback_is_read_only_and_same_doc():
    fake, report, _ = setup_read()
    assert verify(report) and verify(report)
    assert all(call[0] == "GET" for call in fake.calls)
    assert sum(call[1].endswith("/v1/documents/doc") for call in fake.calls) == 2
    assert all(response.closed for response in fake.responses)


@pytest.mark.parametrize("change", ["missing_link", "wrong_link", "link_span", "reason",
                                    "extra_text", "marker", "suggestion", "table_shape", "tab",
                                    "named_style", "document_style", "section_style"])
def test_crafted_marker_with_edited_result_is_unresolved(change):
    fake, report, data = setup_read()
    contents = data["tabs"][0]["documentTab"]["body"]["content"]
    if change in ("missing_link", "wrong_link", "link_span"):
        cell = table(data)["tableRows"][1]["tableCells"][0]
        paragraph = cell["content"][0]["paragraph"]
        part = paragraph["elements"][0]
        run = part["textRun"]
        if change == "missing_link":
            del run["textStyle"]
        elif change == "wrong_link":
            run["textStyle"]["link"]["url"] = "https://evil.example"
        else:
            first = {"startIndex": part["startIndex"], "endIndex": part["startIndex"] + 1,
                     "textRun": {"content": run["content"][0]}}
            part["startIndex"] += 1
            run["content"] = run["content"][1:]
            paragraph["elements"].insert(0, first)
    elif change == "reason":
        cell = table(data)["tableRows"][1]["tableCells"][2]
        run = cell["content"][0]["paragraph"]["elements"][0]["textRun"]
        run["content"] = run["content"].replace("Reason", "Change")
    elif change == "extra_text":
        part = contents[-1]["paragraph"]["elements"][0]
        part["textRun"]["content"] = "x\n"
        part["endIndex"] += 1
        contents[-1]["endIndex"] += 1
    elif change == "marker":
        run = contents[1]["paragraph"]["elements"][0]["textRun"]
        content = run["content"]
        at = content.index(":", content.index(":") + 1) + 1
        replacement = "0" if content[at] != "0" else "1"
        run["content"] = content[:at] + replacement + content[at + 1:]
    elif change == "suggestion":
        contents[1]["paragraph"]["elements"][0]["suggestedDeletionIds"] = ["x"]
    elif change == "table_shape":
        table(data)["tableRows"][1]["tableCells"].pop()
    elif change == "tab":
        data["tabs"][0]["tabProperties"]["tabId"] = "other"
    elif change == "named_style":
        data["tabs"][0]["documentTab"]["namedStyles"] = {"styles": [{"textStyle": {"bold": True}}]}
    elif change == "document_style":
        data["tabs"][0]["documentTab"]["documentStyle"] = {"background": {"color": "black"}}
    elif change == "section_style":
        contents[0]["sectionBreak"]["sectionStyle"] = {"columnProperties": [1]}
    with pytest.raises(ReportConflict):
        verify(report)
    assert all(call[0] == "GET" for call in fake.calls)


def test_readback_rejects_changed_inputs_and_is_bounded(monkeypatch):
    import agent_artifacts.docs_report as docs

    fake, report, _ = setup_read()
    with pytest.raises(ReportConflict):
        verify(report, summary="other summary")
    monkeypatch.setattr(docs, "_DOC_JSON_LIMIT", 1024)
    with pytest.raises(DriveProtocolError, match="exceeds limit"):
        verify(report)
    assert all(call[0] == "GET" for call in fake.calls)


def test_readback_after_simulated_lost_committed_response_never_posts():
    from agent_artifacts.drive_storage import DriveTransientError

    model = AtomicTableModel()
    candidate = plan(model)

    def commit_then_lose_response():
        model.apply(candidate)
        raise DriveTransientError("simulated response loss")

    with pytest.raises(DriveTransientError):
        commit_then_lose_response()
    fake = SourceTransport(model_document(model))
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    assert verify(report)
    assert all(call[0] == "GET" for call in fake.calls)


def test_wrong_tag_fails_before_docs_get():
    from agent_artifacts.drive_storage import DriveAccessError

    fake, report, _ = setup_read()
    fake.bad_tag = True
    with pytest.raises(DriveAccessError):
        verify(report)
    assert len(fake.calls) == 2 and all("/v1/documents/" not in call[1] for call in fake.calls)


def test_unavailable_read_never_posts():
    from agent_artifacts.drive_storage import DriveTransientError

    fake, report, _ = setup_read()
    original = fake.request

    def fail(method, url, **kwargs):
        if "/v1/documents/" in url:
            import requests
            raise requests.Timeout("private")
        return original(method, url, **kwargs)

    fake.request = fail
    with pytest.raises(DriveTransientError):
        verify(report)
    assert all(call[0] == "GET" for call in fake.calls)


@pytest.mark.parametrize("target,field", [("table", "tableStyle"), ("row", "tableRowStyle"),
                                           ("cell", "tableCellStyle")])
@pytest.mark.parametrize("value", [None, False, [], ""])
def test_malformed_falsy_table_styles_are_rejected(target, field, value):
    _, report, data = setup_read()
    target_data = table(data)
    if target in ("row", "cell"):
        target_data = target_data["tableRows"][0]
    if target == "cell":
        target_data = target_data["tableCells"][0]
    target_data[field] = value
    with pytest.raises(ReportConflict):
        verify(report)
