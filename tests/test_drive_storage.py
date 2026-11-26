"""Offline Drive API simulation; no tenant authorization is implied."""

import io
import json
import time
from urllib.parse import urlparse

import pytest
import requests
import urllib3
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from requests.adapters import BaseAdapter

from agent_artifacts.drive_storage import (
    DriveAccessError,
    DriveAuthorizedSession,
    DriveCapacityError,
    DriveProtocolError,
    DriveReadControl,
    DriveRevision,
    DriveStagingError,
    DriveStorage,
    DriveTransientError,
    DriveUploadLimitError,
)


class Response:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code, self.data, self.content = status, data, content
        self.closed = False

    def iter_content(self, chunk_size):
        content = json.dumps(self.data).encode() if self.data is not None else self.content
        yield from (content[i:i + chunk_size] for i in range(0, len(content), chunk_size))

    def close(self):
        self.closed = True


class FakeDrive:
    def __init__(self):
        self.folder = {"id": "folder", "driveId": "drive", "mimeType":
                       "application/vnd.google-apps.folder", "trashed": False,
                       "capabilities": {"canAddChildren": True, "canListChildren": True}}
        self.files = {}
        self.markers = {}
        self.calls = []
        self.responses = []
        self.failure = None

    def request(self, method, url, **kwargs):
        assert url.startswith("https://www.googleapis.com/")
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert 0 < kwargs["timeout"] <= 3
        self.calls.append((method, url, kwargs))
        if self.failure:
            failure, self.failure = self.failure, None
            if isinstance(failure, Exception):
                raise failure
            response = failure if isinstance(failure, Response) else Response(failure)
        else:
            path = urlparse(url).path
            parts = path.split("/")
            params = kwargs["params"]
            if path.endswith("/generateIds"):
                assert params == {"count": 1, "space": "drive", "type": "files"}
                response = Response(data={"ids": ["generated"]})
            elif "/upload/" in path:
                assert params == {"uploadType": "multipart", "supportsAllDrives": "true",
                                  "keepRevisionForever": "true", "fields": "id,headRevisionId"}
                body = kwargs["data"].read()
                boundary = kwargs["headers"]["Content-Type"].split("boundary=")[1].encode()
                sections = body.split(b"--" + boundary)
                metadata = json.loads(sections[1].split(b"\r\n\r\n", 1)[1])
                content = sections[2].split(b"\r\n\r\n", 1)[1][:-2]
                if method == "POST":
                    assert metadata["parents"] == ["folder"]
                    assert metadata["mimeType"] == "application/octet-stream"
                    file_id = metadata.get("id", "file")
                    self.files[file_id] = []
                else:
                    file_id = parts[-1]
                self.files[file_id].append(content)
                if "appProperties" in metadata:
                    self.markers[file_id] = metadata["appProperties"]
                response = Response(data={"id": file_id,
                                          "headRevisionId": str(len(self.files[file_id]))})
            elif "revisions" in parts:
                assert params == {"alt": "media"}
                assert kwargs["stream"] is True
                response = Response(content=self.files[parts[-3]][int(parts[-1]) - 1])
            else:
                assert params["supportsAllDrives"] == "true"
                file_id = parts[-1]
                if file_id == "folder":
                    response = Response(data=self.folder.copy())
                else:
                    response = Response(data={"id": file_id, "driveId": "drive",
                        "mimeType": "application/octet-stream", "trashed": False,
                        "parents": ["folder"], "capabilities": {"canEdit": True},
                        "appProperties": self.markers.get(file_id, {}),
                        "headRevisionId": str(len(self.files[file_id])) if file_id in self.files
                        else None})
        self.responses.append(response)
        return response


@pytest.fixture
def drive():
    fake = FakeDrive()
    return fake, DriveStorage(fake, "folder", max_bytes=100, timeout=3)


def test_generated_id_and_private_marker_on_create_and_replace(drive):
    fake, storage = drive
    file_id = storage.generate_file_id()
    old = storage.create("x", io.BytesIO(b"old"), file_id=file_id, operation_id="operation1")
    assert old == DriveRevision("generated", "1")
    assert storage.inspect_operation(file_id, "operation1") == (True, "1")
    storage.replace(file_id, io.BytesIO(b"new"), operation_id="operation2")
    assert storage.inspect_operation(file_id, "operation1") == (False, "2")
    assert storage.inspect_operation(file_id, "operation2") == (True, "2")
    assert all(response.closed for response in fake.responses)


def test_create_replace_and_read_committed_revision(drive):
    fake, storage = drive
    old = storage.create("report.html", io.BytesIO(b"<h1>old</h1>"))
    new = storage.replace(old.file_id, io.BytesIO(b"new"))
    assert (old, new) == (DriveRevision("file", "1"), DriveRevision("file", "2"))
    with storage.open_revision(old) as chunks:
        assert b"".join(chunks) == b"<h1>old</h1>"
    with storage.open_revision(new) as chunks:
        assert b"".join(chunks) == b"new"
    assert all(response.closed for response in fake.responses)
    assert [c[0] for c in fake.calls].count("PATCH") == 1


@pytest.mark.parametrize("change", [
    {"driveId": None}, {"id": "other"}, {"mimeType": "text/plain"}, {"trashed": True},
    {"capabilities": {"canAddChildren": False, "canListChildren": True}},
    {"capabilities": {"canAddChildren": True, "canListChildren": False}},
])
def test_reject_invalid_folder_metadata(drive, change):
    fake, storage = drive
    fake.folder.update(change)
    with pytest.raises(DriveAccessError):
        storage.create("x", io.BytesIO(b"x"))
    assert len(fake.calls) == 1


def test_changed_shared_drive_and_lost_access(drive):
    fake, storage = drive
    storage.validate_folder()
    fake.folder["driveId"] = "different"
    with pytest.raises(DriveAccessError):
        storage.validate_folder()
    fake.failure = 404
    with pytest.raises(DriveAccessError):
        storage.validate_folder()


@pytest.mark.parametrize(("status", "error"), [(403, DriveAccessError), (404, DriveAccessError),
    (429, DriveCapacityError), (507, DriveCapacityError), (500, DriveTransientError),
    (302, DriveProtocolError)])
def test_errors_are_safe_and_not_retried(drive, status, error):
    fake, storage = drive
    fake.failure = status
    with pytest.raises(error):
        storage.validate_folder()
    assert len(fake.calls) == 1
    assert fake.responses[0].closed


def test_ambiguous_write_timeout_is_not_retried(drive):
    fake, storage = drive
    original = fake.request

    def request(method, url, **kwargs):
        if method == "POST":
            fake.failure = requests.Timeout()
        return original(method, url, **kwargs)

    fake.request = request
    with pytest.raises(DriveTransientError):
        storage.create("x", io.BytesIO(b"content"))
    assert [call[0] for call in fake.calls] == ["GET", "POST"]


def test_source_oserror_is_sanitized_and_does_not_dispatch(drive):
    fake, storage = drive

    class BrokenSource(io.BytesIO):
        def read(self, size=-1):
            raise OSError("private path")

    with pytest.raises(DriveStagingError) as exc:
        storage.create("x", BrokenSource(b"x"))
    assert "private path" not in str(exc.value)
    assert [call[0] for call in fake.calls] == ["GET"]


def test_unexpected_source_error_is_not_reclassified(drive):
    fake, storage = drive

    class BrokenSource(io.BytesIO):
        def read(self, size=-1):
            raise ValueError("programming error")

    with pytest.raises(ValueError, match="programming error"):
        storage.create("x", BrokenSource(b"x"))
    assert [call[0] for call in fake.calls] == ["GET"]


def test_bounded_upload_and_download(drive):
    fake, storage = drive
    with pytest.raises(DriveUploadLimitError):
        storage.create("x", io.BytesIO(b"x" * 101))
    assert len(fake.calls) == 1
    rev = storage.create("x", io.BytesIO(b"x" * 100))
    fake.files["file"][0] += b"x"
    with pytest.raises(DriveCapacityError), storage.open_revision(rev) as chunks:
        b"".join(chunks)
    assert fake.responses[-1].closed


@pytest.mark.parametrize("change", [
    {"id": "other"}, {"driveId": "other"}, {"parents": ["other"]},
    {"trashed": True}, {"mimeType": "application/vnd.google-apps.document"},
    {"capabilities": {"canEdit": False}},
])
def test_reject_wrong_file_metadata_before_update(drive, change):
    fake, storage = drive
    original = fake.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if method == "GET" and url.endswith("/file"):
            response.data.update(change)
        return response

    fake.request = request
    with pytest.raises(DriveAccessError):
        storage.replace("file", io.BytesIO(b"new"))
    assert [call[0] for call in fake.calls] == ["GET", "GET"]
    assert all(response.closed for response in fake.responses)


def test_controlled_request_closes_response_if_stopped_during_request(drive):
    fake, storage = drive
    original = fake.request
    control = DriveReadControl(time.monotonic() + 5)

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        control.stop()
        return response

    fake.request = request
    with pytest.raises(DriveTransientError), storage.open_revision(
        DriveRevision("file", "rev"), control=control,
    ):
        pass
    assert len(fake.calls) == 1
    assert fake.responses[0].closed


def test_file_like_read1_uses_compatible_fallback_and_checks_before_fetch():
    control = DriveReadControl(time.monotonic() + 5)
    calls = []

    class FileLike:
        def read1(self, size):
            raise AssertionError("standard file read1 must not receive urllib3 arguments")

    class FileResponse:
        raw = FileLike()
        headers = {}

        def iter_content(self, chunk_size):
            calls.append(chunk_size)

            def chunks():
                yield b"x"
                calls.append("second read")
                yield b"y"

            return chunks()

    chunks = DriveStorage._chunks(FileResponse(), control)
    assert next(chunks) == b"x"
    control.stop()
    with pytest.raises(DriveTransientError):
        next(chunks)
    assert calls == [1]
    pre_cancelled = DriveReadControl(time.monotonic() + 5)
    pre_cancelled.stop()
    with pytest.raises(DriveTransientError):
        next(DriveStorage._chunks(FileResponse(), pre_cancelled))
    assert calls == [1]


def test_controlled_media_uses_read1_and_deadline_between_trickled_chunks(drive):
    fake, storage = drive
    revision = storage.create("x", io.BytesIO(b"x"))
    original = fake.request
    reads = []

    class Raw(urllib3.response.HTTPResponse):
        def read1(self, size, *, decode_content):
            assert size == 64 * 1024 and decode_content is False
            time.sleep(0.02)
            reads.append(1)
            return b"x"

    def request(method, url, **kwargs):
        assert kwargs["timeout"] <= 0.2
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        response = original(method, url, **kwargs)
        if "/revisions/" in url:
            response.raw = Raw()
        return response

    fake.request = request
    control = DriveReadControl(time.monotonic() + 0.12)
    with pytest.raises(DriveTransientError), storage.open_revision(revision, control=control) as c:
        b"".join(c)
    assert len(reads) >= 2
    assert fake.responses[-1].closed


def test_stream_failure_closes_response(drive):
    fake, storage = drive
    revision = storage.create("x", io.BytesIO(b"x"))
    original = fake.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if "/revisions/" in url:
            def broken_stream(chunk_size):
                raise requests.ConnectionError("connection lost")
                yield b""  # pragma: no cover

            response.iter_content = broken_stream
        return response

    fake.request = request
    with pytest.raises(DriveTransientError), storage.open_revision(revision) as chunks:
        b"".join(chunks)
    assert fake.responses[-1].closed


@pytest.mark.parametrize(("reason", "error"), [
    ("userRateLimitExceeded", DriveCapacityError),
    ("rateLimitExceeded", DriveCapacityError),
    ("storageQuotaExceeded", DriveCapacityError),
    ("teamDriveFileLimitExceeded", DriveCapacityError),
    ("insufficientFilePermissions", DriveAccessError),
    ("unknownReason", DriveAccessError),
])
def test_403_reasons_are_classified_without_exposing_body(drive, reason, error):
    fake, storage = drive
    fake.failure = Response(403, data={"error": {"message": "private body", "errors": [
        {"reason": reason},
    ]}})
    with pytest.raises(error) as exc:
        storage.validate_folder()
    assert "private body" not in str(exc.value)
    assert len(fake.calls) == 1
    assert fake.responses[-1].closed


def test_deeply_nested_json_is_protocol_error_and_closed(drive):
    fake, storage = drive
    original = fake.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        response.data = None
        response.content = b"[" * 2000 + b"]" * 2000
        return response

    fake.request = request
    with pytest.raises(DriveProtocolError):
        storage.validate_folder()
    assert fake.responses[-1].closed


def test_403_capacity_write_not_retried(drive):
    fake, storage = drive
    original = fake.request

    def request(method, url, **kwargs):
        if method == "POST":
            fake.failure = Response(403, data={"error": {"errors": [
                {"reason": "userRateLimitExceeded"},
            ]}})
        return original(method, url, **kwargs)

    fake.request = request
    with pytest.raises(DriveCapacityError):
        storage.create("x", io.BytesIO(b"content"))
    assert [call[0] for call in fake.calls] == ["GET", "POST"]
    assert fake.responses[-1].closed


@pytest.mark.parametrize("content", [b"not json", b"x" * (64 * 1024 + 1)])
def test_403_invalid_or_oversized_envelope_defaults_to_access(drive, content):
    fake, storage = drive
    fake.failure = Response(403, content=content)
    with pytest.raises(DriveAccessError):
        storage.validate_folder()
    assert fake.responses[-1].closed


@pytest.mark.parametrize("upload", [False, True])
def test_json_responses_are_bounded_and_closed(drive, upload):
    fake, storage = drive
    original = fake.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if (method == "POST") == upload:
            response.data = None
            response.content = b"x" * (64 * 1024 + 1)
        return response

    fake.request = request
    with pytest.raises(DriveProtocolError):
        storage.create("x", io.BytesIO(b"content"))
    assert fake.responses[-1].closed
    assert [call[0] for call in fake.calls].count("POST") == int(upload)


def test_replace_spools_before_final_folder_and_file_checks(drive):
    fake, storage = drive

    class Source(io.BytesIO):
        def read(self, size=-1):
            assert not fake.calls
            return super().read(size)

    fake.files["file"] = [b"old"]
    storage.replace("file", Source(b"new"))
    assert [call[0] for call in fake.calls] == ["GET", "GET", "PATCH"]


@pytest.mark.parametrize("replace", [False, True])
def test_before_write_runs_after_spooling_and_aborts_mutation(drive, replace):
    fake, storage = drive
    source = io.BytesIO(b"new")

    def reject():
        assert source.tell() == 3
        assert all(call[0] == "GET" for call in fake.calls)
        raise RuntimeError("preflight rejected")

    with pytest.raises(RuntimeError, match="preflight rejected"):
        if replace:
            storage.replace("file", source, before_write=reject)
        else:
            storage.create("x", source, before_write=reject)
    assert all(call[0] == "GET" for call in fake.calls)
    assert fake.files == {}


def test_replace_rechecks_changed_access_after_spooling(drive):
    fake, storage = drive

    class Source(io.BytesIO):
        def read(self, size=-1):
            fake.folder["capabilities"]["canAddChildren"] = False
            return super().read(size)

    with pytest.raises(DriveAccessError):
        storage.replace("file", Source(b"new"))
    assert [call[0] for call in fake.calls] == ["GET"]


class RecordingBody(io.BytesIO):
    def __init__(self):
        super().__init__(b"redirect body must not be read")
        self.read_count = 0

    def read(self, *args):
        self.read_count += 1
        return super().read(*args)


class RecordingAdapter(BaseAdapter):
    def __init__(self, status):
        self.status = status
        self.calls = []
        self.body = RecordingBody()

    def send(self, request, **kwargs):
        self.calls.append(request)
        response = requests.Response()
        response.status_code = self.status
        response.headers["Location"] = "https://attacker.invalid/secret"
        response.url = request.url
        response.request = request
        response.raw = self.body
        return response

    def close(self):
        pass


@pytest.mark.parametrize("status", [302, 401])
def test_authorized_requests_session_neither_drains_redirect_nor_replays(status):
    session = DriveAuthorizedSession(Credentials(token="secret"))  # noqa: S106
    adapter = RecordingAdapter(status)
    session.mount("https://", adapter)
    storage = DriveStorage(session, "folder", max_bytes=100)
    error = DriveProtocolError if status == 302 else DriveAccessError
    with pytest.raises(error):
        storage.validate_folder()
    assert len(adapter.calls) == 1
    assert adapter.calls[0].url.startswith("https://www.googleapis.com/")
    assert adapter.calls[0].headers["Authorization"] == "Bearer secret"
    assert adapter.body.read_count == 0
    assert adapter.body.closed
    session.close()


def test_credential_refresh_error_is_sanitized():
    class BadCredentials(Credentials):
        def before_request(self, request, method, url, headers):
            raise RefreshError("private credential detail")

    session = DriveAuthorizedSession(BadCredentials(token="fake"))  # noqa: S106
    try:
        with pytest.raises(DriveTransientError) as exc:
            DriveStorage(session, "folder", max_bytes=100).validate_folder()
        assert "private credential detail" not in str(exc.value)
    finally:
        session.close()


@pytest.mark.parametrize("value", ["", "../file", "a/b", "a?b", "a b"])
def test_reject_unsafe_references(value):
    with pytest.raises(ValueError):
        DriveRevision(value, "rev")
