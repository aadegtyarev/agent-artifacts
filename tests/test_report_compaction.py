"""Independent sentinel model of Docs table indices; no Google transport."""

import copy
import json

import pytest

from agent_artifacts.docs_report import ReportLimitError
from agent_artifacts.report_compaction import (
    CompactionInputError,
    CompactionRow,
    VerifiedReportSnapshot,
    plan_compaction,
)

ARTIFACT = "a" * 22
URL1 = f"https://app.example/a/{ARTIFACT}/versions/{'b' * 22}"
URL2 = f"https://app.example/a/{ARTIFACT}/versions/{'c' * 22}"
ROWS = (CompactionRow(URL1, "Принято 😀", "Reason one\nwith context"),
        CompactionRow(URL2, "Rejected", "Not recorded"))


def units(text):
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def decode(values):
    return b"".join(value.to_bytes(2, "little") for value in values).decode("utf-16-le")


def chars(text, cell=None):
    return [{"kind": "char", "unit": value, "cell": cell, "link": None} for value in units(text)]


class AtomicTableModel:
    """Explicit sentinels model table, row, cell and paragraph boundaries.

    Indices are built from sentinel positions, not computed with the planner's
    row/column formula. Mutations happen on a copy and commit only if all pass.
    """

    def __init__(self, text="original review 😀\n"):
        self.document_id = "doc"
        self.revision = "revision1"
        self.body = chars(text)

    def apply(self, plan):
        if plan.document_id != self.document_id:
            raise ValueError("wrong document")
        batch = plan.batch
        if batch["writeControl"] != {"requiredRevisionId": self.revision}:
            raise ValueError("stale revision")
        body = copy.deepcopy(self.body)
        for request in batch["requests"]:
            if "deleteContentRange" in request:
                span = request["deleteContentRange"]["range"]
                start, end = span["startIndex"], span["endIndex"]
                if span["tabId"] != "tab" or start != 1 or end > len(body) or start >= end:
                    raise ValueError("cannot delete final newline or invalid range")
                del body[start - 1:end - 1]
            elif "insertText" in request:
                item = request["insertText"]
                index = item["location"]["index"]
                if item["location"]["tabId"] != "tab" or not 1 <= index <= len(body):
                    raise ValueError("invalid insertion index")
                anchor = body[index - 1]
                if anchor["kind"] not in ("char", "cell_newline"):
                    raise ValueError("not an editable paragraph anchor")
                cell = anchor.get("cell")
                body[index - 1:index - 1] = chars(item["text"], cell)
            elif "insertTable" in request:
                item = request["insertTable"]
                index = item["location"]["index"]
                if (item["location"]["tabId"] != "tab" or not 1 <= index <= len(body)
                        or body[index - 1]["kind"] != "char" or body[index - 1]["unit"] != 10):
                    raise ValueError("table insertion must be in a paragraph")
                # The API inserts a newline then table/row/cell structural boundaries.
                table = chars("\n") + [{"kind": "table_open"}]
                for row in range(item["rows"]):
                    table.append({"kind": "row_open"})
                    for col in range(item["columns"]):
                        table.append({"kind": "cell_open", "cell": (row, col)})
                        table.append({"kind": "cell_newline", "cell": (row, col)})
                table.append({"kind": "table_close"})
                body[index - 1:index - 1] = table
            elif "updateTextStyle" in request:
                style = request["updateTextStyle"]
                span = style["range"]
                start, end = span["startIndex"], span["endIndex"]
                if span["tabId"] != "tab" or not 1 <= start < end <= len(body) + 1:
                    raise ValueError("bad hyperlink range")
                selected = body[start - 1:end - 1]
                if (any(x["kind"] != "char" for x in selected)
                        or len({x["cell"] for x in selected}) != 1):
                    raise ValueError("hyperlink crosses cell boundary")
                url = style["textStyle"]["link"]["url"]
                if decode([x["unit"] for x in selected]) != url:
                    raise ValueError("hyperlink text mismatch")
                for x in selected:
                    x["link"] = url
            else:
                raise ValueError("unsupported request")
        self.body = body
        self.revision = "revision2"

    def cell(self, row, col):
        elements = [x for x in self.body if x.get("cell") == (row, col) and x["kind"] == "char"]
        return decode([x["unit"] for x in elements]), {x["link"] for x in elements}


def snapshot(model, **changes):
    data = dict(document_id="doc", artifact_id=ARTIFACT, revision_id=model.revision,
                tab_id="tab", end_index=len(model.body) + 1, version_urls=frozenset({URL1, URL2}),
                app_authored_only=True)
    return VerifiedReportSnapshot(**(data | changes))


def plan(model, **changes):
    args = dict(public_url="https://app.example", operation_key="close1",
                summary="Итог 😀\nDecision summary", rows=ROWS)
    return plan_compaction(snapshot(model), **(args | changes))


def test_model_matches_official_empty_two_by_two_table_offsets():
    from types import SimpleNamespace

    model = AtomicTableModel("\n")
    candidate = SimpleNamespace(document_id="doc", batch={
        "writeControl": {"requiredRevisionId": "revision1"},
        "requests": [{"insertTable": {"rows": 2, "columns": 2,
                                      "location": {"index": 1, "tabId": "tab"}}}],
    })
    model.apply(candidate)
    indexed = {i: (x["kind"], x.get("cell")) for i, x in enumerate(model.body, 1)}
    start = 2
    assert indexed[start][0] == "table_open"
    assert indexed[start + 1][0] == "row_open"
    assert indexed[start + 3] == ("cell_newline", (0, 0))
    assert indexed[start + 5] == ("cell_newline", (0, 1))
    assert indexed[start + 6][0] == "row_open"
    assert indexed[start + 8] == ("cell_newline", (1, 0))
    assert indexed[start + 10] == ("cell_newline", (1, 1))
    assert indexed[start + 11][0] == "table_close"


def test_candidate_atomic_table_unicode_links_and_stable_doc():
    model = AtomicTableModel()
    candidate = plan(model)
    assert model.revision == "revision1"  # Planning has no side effects.
    assert candidate.document_id == "doc"
    assert candidate == plan(model)
    assert candidate.digest != plan(model, summary="Different").digest
    requests = candidate.batch["requests"]
    heading = requests[1]["insertText"]["text"]
    assert f"[agent-artifacts-compact:close1:{candidate.digest}]" in heading
    delete = requests[0]["deleteContentRange"]["range"]
    assert delete["endIndex"] == len(model.body)  # Excludes the last body newline.
    assert candidate.batch["writeControl"] == {"requiredRevisionId": "revision1"}
    model.apply(candidate)
    assert model.document_id == "doc" and model.revision == "revision2"
    assert [model.cell(0, c)[0] for c in range(3)] == ["Version", "Decision", "Reason"]
    for r, expected in enumerate(ROWS, 1):
        assert model.cell(r, 0) == (expected.version_url, {expected.version_url})
        assert model.cell(r, 1) == (expected.decision, {None})
        assert model.cell(r, 2) == (expected.reason, {None})
    assert model.body[-1]["unit"] == 10


def test_invalid_anchor_and_stale_revision_are_atomic():
    model = AtomicTableModel()
    candidate = plan(model)
    original = copy.deepcopy(model.body)
    broken = copy.deepcopy(candidate)
    inserts = [r["insertText"] for r in broken.batch["requests"] if "insertText" in r]
    table_location = next(r["insertTable"]["location"]["index"]
                          for r in broken.batch["requests"] if "insertTable" in r)
    inserts[1]["location"]["index"] = table_location + 1  # Table start structural boundary.
    with pytest.raises(ValueError):
        model.apply(broken)
    assert model.body == original and model.revision == "revision1"
    model.revision = "newer"
    with pytest.raises(ValueError, match="stale"):
        model.apply(candidate)
    assert model.body == original and model.document_id == "doc"


@pytest.mark.parametrize("rows", [
    (ROWS[0],),
    (ROWS[0], CompactionRow(URL2.replace("app.example", "evil.example"), "x", "x")),
    (ROWS[0], CompactionRow(URL2.replace(ARTIFACT, "z" * 22), "x", "x")),
    (*ROWS, CompactionRow(URL2.replace("c" * 22, "d" * 22), "x", "x")),
    (ROWS[0], CompactionRow(URL2, "x", "")),
])
def test_missing_foreign_or_invalid_rows_are_rejected(rows):
    model = AtomicTableModel()
    before = copy.deepcopy(model.body)
    with pytest.raises(CompactionInputError):
        plan(model, rows=rows)
    assert model.body == before


def test_unverified_snapshot_and_unsafe_text_rejected():
    model = AtomicTableModel()
    with pytest.raises(CompactionInputError):
        plan_compaction(snapshot(model, app_authored_only=False), public_url="https://app.example",
                        operation_key="x", summary="summary", rows=ROWS)
    with pytest.raises(CompactionInputError):
        plan(model, summary="unsafe\x00summary")
    with pytest.raises(CompactionInputError):
        plan(model, summary="\ud800")


def test_size_is_bounded_and_explicit(monkeypatch):
    import agent_artifacts.report_compaction as module

    assert len(json.dumps(plan(AtomicTableModel(), summary="ok").batch)) < 512 * 1024
    monkeypatch.setattr(module, "_DOC_JSON_LIMIT", 20000)
    with pytest.raises(ReportLimitError):
        plan(AtomicTableModel(), summary="😀" * 100)


def test_many_long_urls_charge_repeated_link_styles():
    host = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 60])
    origin = "https://" + host
    urls = [f"{origin}/a/{ARTIFACT}/versions/{i:022d}" for i in range(128)]
    model = AtomicTableModel()
    data = snapshot(model, version_urls=frozenset(urls))
    rows = tuple(CompactionRow(url, "yes", "because") for url in urls)
    with pytest.raises(ReportLimitError, match="reconciliation"):
        plan_compaction(data, public_url=origin, operation_key="long-links", summary="x", rows=rows)
