"""Offline contract tests for the standalone report adapter."""

import json
from urllib.parse import urlsplit

import pytest
import requests

from agent_artifacts.docs_report import DocsReport, ReportConflict, ReportLimitError, ReportRound
from agent_artifacts.drive_storage import DriveProtocolError, DriveTransientError

ARTIFACT = "a" * 22
VERSION = "v" * 22
URL = f"https://app.example/a/{ARTIFACT}/versions/{VERSION}"


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
        self.closed = False

    def iter_content(self, chunk_size):
        raw = json.dumps(self.data, separators=(",", ":")).encode()
        yield from (raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size))

    def close(self):
        self.closed = True


class Fake:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.text = "\n"
        self.revision = "r1"
        self.lookup = [{"id": "doc"}]
        self.batch_status = 200
        self.fail_after_write = False
        self.bad_tag = False
        self.split_runs = False

    def request(self, method, url, **kwargs):
        assert kwargs["stream"] and not kwargs["allow_redirects"]
        self.calls.append((method, url, kwargs))
        path = urlsplit(url).path
        if path.endswith("/folder"):
            data = {"id": "folder", "driveId": "drive", "trashed": False,
                    "mimeType": "application/vnd.google-apps.folder",
                    "capabilities": {"canAddChildren": True, "canListChildren": True}}
        elif path.endswith("/doc") and "/drive/" in path:
            data = {"id": "doc", "driveId": "drive", "trashed": False,
                    "mimeType": "application/vnd.google-apps.document", "parents": ["folder"],
                    "appProperties": {
                        "agentArtifactsReport": "other" if self.bad_tag else ARTIFACT},
                    "capabilities": {"canEdit": True}}
        elif path.endswith("/files") and method == "GET":
            data = {"files": self.lookup}
        elif path.endswith("/files"):
            data = {"id": "doc"}
        elif path.endswith(":batchUpdate"):
            if self.batch_status == 200:
                inserted = kwargs["json"]["requests"][0]["insertText"]["text"]
                # Docs strips these characters from InsertTextRequest.
                inserted = "".join(c for c in inserted if not (
                    ord(c) <= 8 or 12 <= ord(c) <= 31 or 0xE000 <= ord(c) <= 0xF8FF))
                self.text = self.text[:-1] + inserted + "\n"
                self.revision = "r2"
                if self.fail_after_write:
                    self.fail_after_write = False
                    raise requests.Timeout()
            response = Response({}, self.batch_status)
            self.responses.append(response)
            return response
        else:
            assert kwargs["params"]["prettyPrint"] == "false"
            assert "textRun(content)" in kwargs["params"]["fields"]
            end = 1
            paragraphs = []
            for line in self.text.splitlines(keepends=True):
                end += len(line.encode("utf-16-le")) // 2
                # Optionally pessimistically split every character into its own run.
                parts = list(line) if self.split_runs else [line]
                paragraphs.append({"endIndex": end, "paragraph": {"elements": [
                    {"textRun": {"content": c}} for c in parts]}})
            data = {"revisionId": self.revision, "tabs": [{"tabProperties": {"tabId": "t"},
                    "documentTab": {"body": {"content": paragraphs}}}]}
        response = Response(data)
        self.responses.append(response)
        return response


@pytest.fixture
def setup():
    fake = Fake()
    return fake, DocsReport.for_folder(fake, "folder", public_url="https://app.example", timeout=3)


def round_(**changes):
    args = dict(key="round1", version_url=URL, goal="🎯 goal", decision="yes",
                reasons="because", open_items="none")
    args.update(changes)
    return ReportRound(**args)


def test_create_and_lookup_same_folder(setup):
    fake, report = setup
    with pytest.raises(ValueError):
        report.create(ARTIFACT, claim=None)
    assert report.create(ARTIFACT, claim=object()) == "doc"
    post = fake.calls[1]
    assert post[2]["params"]["supportsAllDrives"] == "true"
    assert post[2]["json"] == {"name": "Review report", "parents": ["folder"],
        "mimeType": "application/vnd.google-apps.document",
        "appProperties": {"agentArtifactsReport": ARTIFACT}}
    assert report.lookup(ARTIFACT) == "doc"
    assert "'folder' in parents" in fake.calls[3][2]["params"]["q"]
    assert all(r.closed for r in fake.responses)


def test_lookup_missing_ambiguous_and_failure(setup):
    fake, report = setup
    fake.lookup = []
    assert report.lookup(ARTIFACT) is None
    fake.lookup = [{"id": "doc"}, {"id": "other"}]
    with pytest.raises(ReportConflict):
        report.lookup(ARTIFACT)
    fake.lookup = "invalid"
    with pytest.raises(ReportConflict):
        report.lookup(ARTIFACT)


def test_tag_mismatch_is_never_appended(setup):
    fake, report = setup
    fake.bad_tag = True
    from agent_artifacts.drive_storage import DriveAccessError

    with pytest.raises(DriveAccessError):
        report.lookup(ARTIFACT)
    with pytest.raises(DriveAccessError):
        report.append("doc", ARTIFACT, round_())
    assert not any(c[1].endswith(":batchUpdate") for c in fake.calls)


def test_append_utf16_retry_and_conflict(setup):
    fake, report = setup
    fake.text = "Existing 😀\n"
    fake.fail_after_write = True
    with pytest.raises(DriveTransientError):
        report.append("doc", ARTIFACT, round_())
    batch = next(call for call in fake.calls if call[1].endswith(":batchUpdate"))
    assert "/documents/doc:batchUpdate" in batch[1]
    body = batch[2]["json"]
    assert body["writeControl"] == {"requiredRevisionId": "r1"}
    text = body["requests"][0]["insertText"]["text"]
    link = body["requests"][1]["updateTextStyle"]
    start = len("Existing 😀".encode("utf-16-le")) // 2 + 1
    offset = len(text[:text.index(URL)].encode("utf-16-le")) // 2
    assert link["range"]["startIndex"] == start + offset
    assert link["range"]["endIndex"] - link["range"]["startIndex"] == len(URL)
    assert link["textStyle"]["link"]["url"] == URL
    assert report.append("doc", ARTIFACT, round_()) is False
    with pytest.raises(ReportConflict):
        report.append("doc", ARTIFACT, round_(decision="no"))
    assert sum(c[1].endswith(":batchUpdate") for c in fake.calls) == 1
    assert fake.text.startswith("Existing 😀\n[agent-artifacts-round:")


def test_revision_conflict_is_not_replayed(setup):
    fake, report = setup
    fake.batch_status = 400
    with pytest.raises(DriveProtocolError):
        report.append("doc", ARTIFACT, round_())
    assert sum(c[1].endswith(":batchUpdate") for c in fake.calls) == 1
    assert fake.text == "\n"


@pytest.mark.parametrize("url", [
    URL + "?x=1", URL + "/raw", URL.replace("app.example", "evil.example"),
    URL.replace(ARTIFACT, "b" * 22), URL + "#fragment",
])
def test_reject_untrusted_urls_before_network(setup, url):
    fake, report = setup
    with pytest.raises(ValueError):
        report.append("doc", ARTIFACT, round_(version_url=url))
    assert not fake.calls


@pytest.mark.parametrize("origin", [
    "http://app.example", "https://app.example?", "https://app.example#",
    "https://@app.example", "https://user:pass@app.example", "https://app.example:",
    "https://app.example:99999", "https://app.example:0", "https://-bad.example",
    "https://app..example", "https://999.999.999.999", "https://app.example\\\\evil", "https://[::1",
    "https://app.example/path", "https://app.example\n",
])
def test_reject_invalid_origins(origin):
    with pytest.raises(ValueError):
        DocsReport.for_folder(Fake(), "folder", public_url=origin)


def test_valid_https_origin_version_link():
    fake = Fake()
    report = DocsReport.for_folder(fake, "folder", public_url="https://app.example/")
    assert report.append("doc", ARTIFACT, round_())


@pytest.mark.parametrize("bad", ["foo", "a" * 21, "a" * 23, "a" * 21 + "!", None])
def test_artifact_ids_are_consistent_before_network(setup, bad):
    fake, report = setup
    for operation in (lambda: report.create(bad, claim=object()), lambda: report.lookup(bad),
                      lambda: report.append("doc", bad, round_())):
        with pytest.raises(ValueError):
            operation()
    assert not fake.calls


@pytest.mark.parametrize("character", [
    "\x00", "\x08", "\x0c", "\x1f", "\ue000", "\uf8ff", "\ud800",
])
@pytest.mark.parametrize("field", ["goal", "open_items"])
def test_docs_stripped_and_surrogate_characters_rejected_before_write(setup, character, field):
    fake, report = setup
    with pytest.raises(ValueError):
        report.append("doc", ARTIFACT, round_(**{field: "before" + character + "after"}))
    assert not fake.calls


def test_fake_strips_docs_disallowed_characters_around_link():
    fake = Fake()
    inserted = f"before\x01{URL}after\ue000\n"
    fake.request("POST", "https://docs.googleapis.com/v1/documents/doc:batchUpdate",
                 stream=True, allow_redirects=False,
                 json={"requests": [{"insertText": {"text": inserted}}]})
    assert fake.text == f"before{URL}after\n\n"


def test_repeated_round_budget_and_lost_response(setup, monkeypatch):
    import agent_artifacts.docs_report as docs_report

    monkeypatch.setattr(docs_report, "_DOC_JSON_LIMIT", 65536)
    fake, report = setup
    fake.split_runs = True
    completed = []
    for number in range(30):
        current = round_(key=f"r{number}", goal="😀", decision="x", reasons="x", open_items="x")
        fake.fail_after_write = True
        before = sum(c[1].endswith(":batchUpdate") for c in fake.calls)
        try:
            with pytest.raises(DriveTransientError):
                report.append("doc", ARTIFACT, current)
        except ReportLimitError:
            assert sum(c[1].endswith(":batchUpdate") for c in fake.calls) == before
            break
        assert report.append("doc", ARTIFACT, current) is False
        completed.append(current)
    assert len(completed) >= 2
    for current in completed:
        assert report.append("doc", ARTIFACT, current) is False


def test_large_candidate_rejected_before_post(setup, monkeypatch):
    import agent_artifacts.docs_report as docs_report

    monkeypatch.setattr(docs_report, "_DOC_JSON_LIMIT", 65536)
    fake, report = setup
    with pytest.raises(ReportLimitError):
        report.append("doc", ARTIFACT, round_(goal="😀" * 2000))
    assert not any(c[1].endswith(":batchUpdate") for c in fake.calls)


def test_realistic_multilingual_rounds_and_lost_response(setup):
    fake, report = setup
    for n in range(6):
        current = round_(key=f"real{n}", goal="Проверка 🎯",
                         reasons="Причины решения — 日本語 😀 " * 140)
        fake.fail_after_write = True
        with pytest.raises(DriveTransientError):
            report.append("doc", ARTIFACT, current)
        assert report.append("doc", ARTIFACT, current) is False
    assert len(fake.text) > 20000
    assert sum(c[1].endswith(":batchUpdate") for c in fake.calls) == 6


def test_bounded_json(setup, monkeypatch):
    import agent_artifacts.docs_report as docs_report

    monkeypatch.setattr(docs_report, "_DOC_JSON_LIMIT", 65536)
    fake, report = setup
    fake.text = "x" * 70000
    with pytest.raises(DriveProtocolError, match="exceeds limit"):
        report.append("doc", ARTIFACT, round_())
    assert all(r.closed for r in fake.responses)


def test_create_callback_occurs_after_folder_read_and_can_block_post(setup):
    fake, report = setup

    def stop():
        assert len(fake.calls) == 1 and fake.calls[0][1].endswith("/folder")
        raise RuntimeError("claim denied")

    with pytest.raises(RuntimeError, match="claim denied"):
        report.create(ARTIFACT, claim=object(), before_create=stop)
    assert len(fake.calls) == 1


def test_append_callback_after_reads_and_budget_can_block_post(setup, monkeypatch):
    import agent_artifacts.docs_report as docs_report

    fake, report = setup
    def stop():
        assert len(fake.calls) == 3
        assert fake.calls[-1][1].endswith("/documents/doc")
        raise RuntimeError("claim denied")

    with pytest.raises(RuntimeError, match="claim denied"):
        report.append("doc", ARTIFACT, round_(), before_write=stop)
    assert not any(call[1].endswith(":batchUpdate") for call in fake.calls)
    monkeypatch.setattr(docs_report, "_DOC_JSON_LIMIT", 65536)
    called = []
    with pytest.raises(ReportLimitError):
        report.append("doc", ARTIFACT, round_(goal="x" * 2000),
                      before_write=lambda: called.append(True))
    assert called == []
