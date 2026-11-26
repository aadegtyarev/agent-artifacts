"""Read-only Docs source verification with independently built projected fixtures."""

import copy
import hashlib
from urllib.parse import urlsplit

import pytest
from test_docs_report import ARTIFACT, Fake, Response

from agent_artifacts.docs_report import DocsReport, ReportConflict
from agent_artifacts.drive_storage import DriveAccessError, DriveProtocolError
from agent_artifacts.report_source import ExpectedReportRound, read_report_source

URL1 = f"https://app.example/a/{ARTIFACT}/versions/{'b' * 22}"
URL2 = f"https://app.example/a/{ARTIFACT}/versions/{'c' * 22}"


def length(text):
    return len(text.encode("utf-16-le")) // 2


def source(pairs=None):
    lines = []
    expected = []
    if pairs is None:
        pairs = [("round1", URL1, "Цель 😀", "Причина"),
                 ("round2", URL2, "Goal", "Not recorded")]
    for key, url, goal, reason in pairs:
        content = (f"Goal: {goal}\nVersion: {url}\nDecision: yes\n"
                   f"Reasons: {reason}\nOpen items: none\n")
        digest = hashlib.sha256(content.encode()).hexdigest()
        head = f"[agent-artifacts-round:{key}:{digest}]\n"
        frame = head + content + f"[agent-artifacts-end:{key}]\n"
        offset = length(head + f"Goal: {goal}\nVersion: ")
        expected.append(ExpectedReportRound(key, digest, url, length(frame), offset,
                                            offset + length(url)))
        lines.extend([(head, None),
                      (f"Goal: {goal}\n", None),
                      ("Version: ", url),
                      ("Decision: yes\n", None), (f"Reasons: {reason}\n", None),
                      ("Open items: none\n", None),
                      (f"[agent-artifacts-end:{key}]\n", None)])
    lines.append(("\n", None))
    elements = [{"endIndex": 1, "sectionBreak": {"sectionStyle": {}}}]
    index = 1
    for prefix, url in lines:
        start = index
        runs = []
        parts = [(prefix, None)] if url is None else [(prefix, None),
                    (url[:20], url), (url[20:], url), ("\n", None)]
        for text, link in parts:
            if not text:
                continue
            run = {"content": text}
            if link:
                run["textStyle"] = {"link": {"url": link}}
            runs.append({"startIndex": index, "endIndex": index + length(text), "textRun": run})
            index += length(text)
        elements.append({"startIndex": start, "endIndex": index,
                         "paragraph": {"elements": runs, "paragraphStyle": {}}})
    return {"revisionId": "revision", "tabs": [{"tabProperties": {"tabId": "tab"},
            "documentTab": {"body": {"content": elements}}}]}, tuple(expected), index


class SourceTransport(Fake):
    def __init__(self, data):
        super().__init__()
        self.source_data = data

    def request(self, method, url, **kwargs):
        if urlsplit(url).path == "/v1/documents/doc":
            assert method == "GET" and kwargs["stream"] and not kwargs["allow_redirects"]
            assert kwargs["params"]["fields"] == (
                "revisionId,tabs(tabProperties(tabId),childTabs,documentTab)"
            )
            assert kwargs["params"]["suggestionsViewMode"] == "SUGGESTIONS_INLINE"
            self.calls.append((method, url, kwargs))
            response = Response(self.source_data)
            self.responses.append(response)
            return response
        return super().request(method, url, **kwargs)


def read(data, expected):
    fake = SourceTransport(data)
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    result = read_report_source(report, "doc", ARTIFACT, expected_rounds=expected)
    assert all(call[0] == "GET" for call in fake.calls)
    assert all(response.closed for response in fake.responses)
    return result


def test_verified_multiround_unicode_source_and_exact_positions():
    data, expected, end = source()
    result = read(data, expected)
    assert result.document_id == "doc" and result.artifact_id == ARTIFACT
    assert (result.revision_id, result.tab_id, result.end_index) == ("revision", "tab", end)
    assert result.version_urls == frozenset({URL1, URL2}) and result.app_authored_only


def test_untrusted_expected_data_fails_before_network():
    data, expected, _ = source()
    fake = SourceTransport(data)
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    for items in ((), (expected[0], expected[0]),
                  (ExpectedReportRound("r", "0" * 64, URL1.replace("app.example", "evil"),
                                       300, 10, 10 + length(URL1.replace("app.example", "evil"))),),
                  (ExpectedReportRound("r", "0" * 64, URL1.replace(ARTIFACT, "d" * 22),
                                       300, 10, 10 + length(URL1)),)):
        with pytest.raises(ValueError):
            read_report_source(report, "doc", ARTIFACT, expected_rounds=items)
    assert not fake.calls


@pytest.mark.parametrize("change", ["no_link", "swapped_link", "suggestion", "manual_text",
                                    "bad_index", "table", "children", "header", "duplicate",
                                    "missing_final_newline", "missing_round", "multiline"])
def test_untrusted_or_unsupported_source_rejected(change):
    data, expected, _ = source()
    doc = data["tabs"][0]["documentTab"]
    elements = doc["body"]["content"]
    if change == "no_link":
        del elements[3]["paragraph"]["elements"][1]["textRun"]["textStyle"]
    elif change == "swapped_link":
        elements[3]["paragraph"]["elements"][1]["textRun"]["textStyle"]["link"]["url"] = URL2
    elif change == "suggestion":
        elements[2]["paragraph"]["elements"][0]["suggestedInsertionIds"] = ["s"]
    elif change == "manual_text":
        elements[2]["paragraph"]["elements"][0]["textRun"]["content"] = "Goal: edited\n"
    elif change == "bad_index":
        elements[3]["startIndex"] += 1
    elif change == "table":
        elements[2]["table"] = {}
    elif change == "children":
        data["tabs"][0]["childTabs"] = [{"tabProperties": {"tabId": "extra"}}]
    elif change == "header":
        doc["headers"] = {"h": {}}
    elif change == "duplicate":
        from dataclasses import replace

        expected = (expected[0], replace(expected[1], key="other"))
    elif change == "missing_final_newline":
        elements.pop()
    elif change == "missing_round":
        expected = (expected[0],)
    elif change == "multiline":
        elements[2]["paragraph"]["elements"][0]["textRun"]["content"] = "Goal: one\ntwo\n"
    with pytest.raises(ReportConflict, match="unsupported|suggestions"):
        read(data, expected)


def test_duplicate_round_in_doc_is_rejected_even_when_digest_matches():
    pairs = [("round1", URL1, "Цель 😀", "Причина"),
             ("round2", URL2, "Goal", "Not recorded"),
             ("round1", URL1, "Цель 😀", "Причина")]
    data, expected, _ = source(pairs)
    with pytest.raises(ReportConflict):
        read(data, expected[:2])


def test_marker_recomputed_after_edit_still_requires_trusted_digest():
    data, expected, _ = source()
    # Replace a whole well-formed report with a separately self-consistent report.
    modified = copy.deepcopy(data)
    elements = modified["tabs"][0]["documentTab"]["body"]["content"]
    goal_run = elements[2]["paragraph"]["elements"][0]["textRun"]
    old_goal = goal_run["content"]
    new_goal = old_goal.replace("Цель", "Идея")  # same UTF-16 length
    goal_run["content"] = new_goal
    old_content = (f"Goal: Цель 😀\nVersion: {URL1}\nDecision: yes\n"
                   "Reasons: Причина\nOpen items: none\n")
    digest = hashlib.sha256(old_content.replace("Цель", "Идея").encode()).hexdigest()
    header = elements[1]["paragraph"]["elements"][0]["textRun"]
    header["content"] = header["content"].replace(expected[0].digest, digest)
    with pytest.raises(ReportConflict):
        read(modified, expected)


def test_bounded_read_and_tag_verification(monkeypatch):
    import agent_artifacts.docs_report as docs_module

    data, expected, _ = source()
    fake = SourceTransport(data)
    fake.bad_tag = True
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    with pytest.raises(DriveAccessError, match="tagged document"):
        read_report_source(report, "doc", ARTIFACT, expected_rounds=expected)
    assert len(fake.calls) == 2
    fake.bad_tag = False
    monkeypatch.setattr(docs_module, "_DOC_JSON_LIMIT", 1024)
    with pytest.raises(DriveProtocolError, match="exceeds limit"):
        read_report_source(report, "doc", ARTIFACT, expected_rounds=expected)
    assert all(r.closed for r in fake.responses)
    assert all(call[0] == "GET" for call in fake.calls)


class AppendingTransport(SourceTransport):
    """Apply the actual insertText and updateTextStyle requests to an offline Doc."""

    def __init__(self):
        super().__init__({})
        self.text = "\n"
        self.link_spans = []
        self.revision = 1

    def document(self):
        elements = [{"endIndex": 1, "sectionBreak": {"sectionStyle": {}}}]
        index = 1
        for paragraph in self.text.split("\n")[:-1]:
            start = index
            runs = []
            part, current = "", None
            for char in paragraph + "\n":
                link = next((url for a, b, url in self.link_spans if a <= index < b), None)
                if part and link != current:
                    run = {"content": part}
                    if current:
                        run["textStyle"] = {"link": {"url": current}}
                    runs.append({"startIndex": index - length(part), "endIndex": index,
                                 "textRun": run})
                    part = ""
                part += char
                current = link
                index += length(char)
            if part:
                run = {"content": part}
                if current:
                    run["textStyle"] = {"link": {"url": current}}
                runs.append({"startIndex": index - length(part), "endIndex": index,
                             "textRun": run})
            elements.append({"startIndex": start, "endIndex": index,
                             "paragraph": {"elements": runs, "paragraphStyle": {}}})
        return {"revisionId": f"r{self.revision}", "tabs": [{"tabProperties": {"tabId": "tab"},
                "documentTab": {"body": {"content": elements}}}]}

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        if path == "/v1/documents/doc" and method == "GET":
            append_fields = (
                "revisionId,tabs(tabProperties(tabId),childTabs(tabProperties(tabId)),"
                "documentTab(body(content(endIndex,sectionBreak,paragraph(elements(textRun(content)))))))"
            )
            source_fields = "revisionId,tabs(tabProperties(tabId),childTabs,documentTab)"
            assert kwargs["stream"] and not kwargs["allow_redirects"]
            assert kwargs["params"]["includeTabsContent"] == "true"
            assert kwargs["params"]["prettyPrint"] == "false"
            fields = kwargs["params"]["fields"]
            assert fields in (append_fields, source_fields)
            data = self.document()
            if fields == append_fields:
                elements = data["tabs"][0]["documentTab"]["body"]["content"]
                for element in elements:
                    element.pop("startIndex", None)
                    if "paragraph" in element:
                        paragraph = element["paragraph"]
                        paragraph.pop("paragraphStyle", None)
                        for part in paragraph["elements"]:
                            part.pop("startIndex", None)
                            part.pop("endIndex", None)
                            part["textRun"].pop("textStyle", None)
            else:
                assert kwargs["params"]["suggestionsViewMode"] == "SUGGESTIONS_INLINE"
            self.calls.append((method, url, kwargs))
            response = Response(data)
            self.responses.append(response)
            return response
        if path == "/v1/documents/doc:batchUpdate":
            assert method == "POST" and kwargs["json"]["writeControl"] == {
                "requiredRevisionId": f"r{self.revision}"}
            request = kwargs["json"]["requests"]
            insert = request[0]["insertText"]
            assert insert["location"] == {"index": length(self.text), "tabId": "tab"}
            self.text = self.text[:-1] + insert["text"] + "\n"
            style = request[1]["updateTextStyle"]
            assert style["fields"] == "link" and style["range"]["tabId"] == "tab"
            start, end = style["range"]["startIndex"], style["range"]["endIndex"]
            self.link_spans.append((start, end, style["textStyle"]["link"]["url"]))
            self.calls.append((method, url, kwargs))
            self.revision += 1
            response = Response({})
            self.responses.append(response)
            return response
        return super().request(method, url, **kwargs)


def expected_from_render(render):
    return ExpectedReportRound(render.key, render.digest, render.version_url,
                               render.framed_utf16_length, render.link_start, render.link_end)


def test_actual_append_multiline_unicode_and_literal_markers_verify():
    from agent_artifacts.docs_report import ReportRound

    fake = AppendingTransport()
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    rounds = [ReportRound("round1", URL1, "Первый 😀\nSecond [agent-artifacts-end:literal]",
                          "First\nDecision: fake", "Reason\n[agent-artifacts-any]",
                          "Open\n[agent-artifacts-end:round1]"),
              ReportRound("round2", URL2, "Other\nGoal", "no\nchanged", "why\nwhy", "x\ny")]
    expected = []
    for item in rounds:
        rendered = report.render_round(ARTIFACT, item)
        expected.append(expected_from_render(rendered))
        assert report.append("doc", ARTIFACT, item)
    result = read_report_source(report, "doc", ARTIFACT, expected_rounds=tuple(expected))
    assert result.version_urls == frozenset({URL1, URL2})
    assert result.end_index == length(fake.text) + 1
    post_count = sum(call[0] == "POST" for call in fake.calls)
    assert post_count == 2
    read_report_source(report, "doc", ARTIFACT, expected_rounds=tuple(expected))
    assert sum(call[0] == "POST" for call in fake.calls) == post_count


def test_reordered_intact_rounds_are_rejected():
    data, expected, _ = source()
    pairs = [("round2", URL2, "Goal", "Not recorded"), ("round1", URL1, "Цель 😀", "Причина")]
    reordered, _, _ = source(pairs)
    with pytest.raises(ReportConflict):
        read(reordered, expected)
    assert data != reordered


@pytest.mark.parametrize("style", [
    {"strikethrough": True}, {"foregroundColor": {"color": {"rgbColor": {
        "red": 1, "green": 1, "blue": 1}}}}, {"fontSize": {"magnitude": 0}},
    {"backgroundColor": {"color": {"rgbColor": {"red": 0, "green": 0, "blue": 0}}}},
])
def test_unsupported_semantic_text_styles_rejected(style):
    data, expected, _ = source()
    data["tabs"][0]["documentTab"]["body"]["content"][4]["paragraph"]["elements"][0][
        "textRun"]["textStyle"] = style
    with pytest.raises(ReportConflict):
        read(data, expected)


def test_supported_default_blue_link_style_and_hidden_style_rejection():
    data, expected, _ = source()
    runs = data["tabs"][0]["documentTab"]["body"]["content"][3]["paragraph"]["elements"]
    for run in runs[1:3]:
        style = run["textRun"]["textStyle"]
        style["underline"] = True
        style["foregroundColor"] = {"color": {"rgbColor": {
            "red": 1 / 15, "green": 1 / 3, "blue": 0.8}}}
    assert read(data, expected).app_authored_only
    runs[1]["textRun"]["textStyle"]["foregroundColor"]["color"]["rgbColor"]["red"] = float("nan")
    with pytest.raises(ReportConflict):
        read(data, expected)


def test_expected_span_cannot_split_surrogate():
    from dataclasses import replace

    data, expected, _ = source()
    # The goal contains an astral symbol before the normal version span.
    text = "[agent-artifacts-round:round1:" + expected[0].digest + "]\nGoal: Цель "
    inside_emoji = length(text) + 1
    tampered = replace(expected[0], link_start=inside_emoji,
                       link_end=inside_emoji + length(URL1))
    with pytest.raises(ReportConflict):
        read(data, (tampered, expected[1]))


def test_more_than_128_confirmed_rounds_one_version_are_verifiable():
    from agent_artifacts.docs_report import ReportRound

    fake = AppendingTransport()
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example")
    expected = []
    for i in range(129):
        item = ReportRound(f"r{i}", URL1, "g", "d", "r", "o")
        expected.append(expected_from_render(report.render_round(ARTIFACT, item)))
        assert report.append("doc", ARTIFACT, item)
    result = read_report_source(report, "doc", ARTIFACT, expected_rounds=tuple(expected))
    assert result.version_urls == frozenset({URL1})
    from agent_artifacts.report_compaction import CompactionRow, plan_compaction

    candidate = plan_compaction(result, public_url="https://app.example", operation_key="close",
                                summary="done", rows=(CompactionRow(URL1, "d", "r"),))
    assert candidate.document_id == "doc"


def test_named_style_inherited_strikethrough_is_rejected():
    data, expected, _ = source()
    doc = data["tabs"][0]["documentTab"]
    doc["namedStyles"] = {"styles": [{"namedStyleType": "NORMAL_TEXT",
                                      "textStyle": {"strikethrough": True}}]}
    with pytest.raises(ReportConflict):
        read(data, expected)
    # The only accepted named-style baseline is absent or empty, not a real-provider claim.
    doc["namedStyles"] = {}
    assert read(data, expected).app_authored_only


@pytest.mark.parametrize("bad", [None, [], False, ""])
def test_falsy_nonobject_named_styles_rejected(bad):
    data, expected, _ = source()
    data["tabs"][0]["documentTab"]["namedStyles"] = bad
    with pytest.raises(ReportConflict):
        read(data, expected)


@pytest.mark.parametrize("location,bad", [
    ("documentStyle", {"background": {"color": "black"}}), ("documentStyle", None),
    ("sectionBreak", {"sectionStyle": {"columnProperties": [1]}}), ("sectionBreak", False),
    ("sectionBreak", {"sectionStyle": []}),
])
def test_unverified_document_and_section_style_rejected(location, bad):
    data, expected, _ = source()
    doc = data["tabs"][0]["documentTab"]
    if location == "documentStyle":
        doc[location] = bad
    else:
        doc["body"]["content"][0][location] = bad
    with pytest.raises(ReportConflict):
        read(data, expected)
