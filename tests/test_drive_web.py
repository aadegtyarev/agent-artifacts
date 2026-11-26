"""Injected offline Drive delivery under Workspace authentication."""

import asyncio
import io
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from email.utils import formatdate
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from starlette.responses import Response
from starlette.testclient import TestClient
from test_drive_storage import FakeDrive as FakeTransport
from test_drive_store import FakeDrive
from test_workspace_web import MAIN, RAW, FakeGoogle, sign_in

from agent_artifacts.config import Config, WorkspaceConfig
from agent_artifacts.drive_storage import (
    DriveAccessError,
    DriveReadControl,
    DriveRevision,
    DriveStorage,
    DriveTransientError,
)
from agent_artifacts.pages import PINS_SCRIPT, SANDBOX_CSP, VIEWER_CSP
from agent_artifacts.store import Store
from agent_artifacts.web import (
    _materialize_async,
    _materialize_revision,
    _TemporaryResponse,
    _TemporaryStorage,
    create_app,
)
from agent_artifacts.workspace_auth import SESSION_COOKIE


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    config = Config(data_dir=tmp_path, public_url=MAIN, raw_url=RAW,
                    workspace=WorkspaceConfig("client", "secret", "company.example"))
    store = Store(tmp_path, max_total_bytes=config.max_total_bytes)
    admin = store.add_agent("admin", admin=True)
    key = store.add_agent("agent")
    drive = FakeDrive()
    with TestClient(create_app(config, store, drive=drive), base_url=MAIN) as client:
        yield client, store, drive, {"Authorization": f"Bearer {key}"}, admin


def upload(client, headers, name, data, artifact_id=None, **fields):
    path = "/api/v1/artifacts" + (f"/{artifact_id}" if artifact_id else "")
    return client.request("PUT" if artifact_id else "POST", path, headers=headers,
                          files={"file": (name, data)}, data=fields)


def test_upload_replace_exact_revision_workspace_and_html(env):
    client, store, drive, headers, _ = env
    created = upload(client, headers, "a.html", b"<h1>old</h1>")
    assert created.status_code == 201
    artifact_id = created.json()["id"]
    assert client.get(f"/a/{artifact_id}", follow_redirects=False).status_code == 303
    assert client.get(f"/a/{artifact_id}/raw").status_code == 401
    assert client.get(RAW + f"/a/{artifact_id}/raw").status_code == 401
    replaced = upload(client, headers, "a.html", b"<h1>new</h1>", artifact_id)
    assert replaced.status_code == 200
    _, ref = store.get_drive(artifact_id)
    drive.files[ref.file_id].append(b"unpublished HEAD")
    sign_in(client, MAIN, f"/a/{artifact_id}")
    assert client.get(f"/a/{artifact_id}").status_code == 200
    raw = client.get(f"/a/{artifact_id}/raw")
    assert raw.content == b"<h1>new</h1>" + PINS_SCRIPT
    assert raw.headers["content-security-policy"] == SANDBOX_CSP
    assert client.get(f"/a/{artifact_id}/download").content == b"<h1>new</h1>"
    sign_in(client, RAW, f"/a/{artifact_id}/raw")
    assert client.get(RAW + f"/a/{artifact_id}/raw").content == raw.content
    assert client.get("/api/v1/me", headers=headers).status_code == 200


def test_legacy_disk_file_stays_on_disk_with_drive_enabled(env):
    client, store, drive, headers, _ = env
    legacy = store.put(io.BytesIO(b"before"), owner="agent", name="legacy.txt", kind="txt", size=6)
    path = f"/a/{legacy.id}"
    outsider = store.add_agent("outsider")
    rejected = upload(client, {"Authorization": f"Bearer {outsider}"}, "legacy.txt", b"no", legacy.id)
    assert rejected.status_code == 404
    drive.failure = DriveAccessError("Drive is unavailable")
    replaced = upload(client, headers, "legacy.txt", b"after", legacy.id)
    assert replaced.status_code == 200 and "version_url" not in replaced.json()
    assert store.get_drive(legacy.id) is None and not drive.files
    sign_in(client, MAIN, path)
    assert client.get(path + "/download").content == b"after"


def test_private_version_link_retains_exact_old_bytes_and_metadata(env):
    client, store, drive, headers, _ = env
    old_bytes = b"<h1>old</h1>"
    first = upload(client, headers, "old.html", old_bytes).json()
    path = urlsplit(first["version_url"]).path
    artifact_id = first["id"]
    pdf = b"%PDF-1.4\nnew pdf\n%%EOF\n"
    second = upload(client, headers, "new.pdf", pdf, artifact_id).json()
    path2 = urlsplit(second["version_url"]).path
    assert path != path2
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?return=" + path
    assert client.get(path + "/raw", follow_redirects=False).status_code == 401
    assert client.get(path + "/download", follow_redirects=False).status_code == 401
    assert client.get(RAW + path, follow_redirects=False).status_code == 404
    sign_in(client, RAW, f"/a/{artifact_id}/raw")
    assert client.get(path, follow_redirects=False).status_code == 303
    sign_in(client, MAIN, path)
    wrapper = client.get(path)
    assert wrapper.status_code == 200 and "old.html" in wrapper.text
    assert 'sandbox="' in wrapper.text and path + "/raw" in wrapper.text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in wrapper.text
    assert '<link rel="stylesheet" href="/static/site.css">' in wrapper.text
    assert '<div class="stage"><main><iframe' in wrapper.text
    assert wrapper.headers["content-security-policy"] == VIEWER_CSP
    assert client.get("/static/site.css").status_code == 200
    raw = client.get(path + "/raw")
    assert raw.content == old_bytes + PINS_SCRIPT
    assert raw.headers["content-security-policy"] == SANDBOX_CSP
    assert client.get(path + "/download").content == old_bytes
    assert "old.html" in client.get(path + "/download").headers["content-disposition"]
    assert client.get(path2 + "/raw").content == pdf
    assert client.get(path2 + "/download").content == pdf
    assert client.get(path + "/raw", headers={"sec-fetch-dest": "document"},
                      follow_redirects=False).headers["location"] == path
    assert client.get(path.replace(artifact_id, "a" * 22)).status_code == 404
    assert client.get(path.rsplit("/", 1)[0] + "/" + "b" * 22).status_code == 404
    drive.failure = DriveAccessError("private provider error")
    for suffix in ("", "/raw", "/download"):
        response = client.get(path + suffix)
        assert response.status_code == 503 and b"private provider error" not in response.content
    assert store.get_drive(artifact_id) is not None
    drive.failure = None
    first_expiry = store.get(artifact_id).expires_at
    store.clock = lambda: first_expiry + 1
    assert client.get(path + "/download").status_code == 404


def test_historical_head_requires_workspace_and_returns_headers_without_body(env):
    client, _, _, headers, _ = env
    content = b"<h1>old</h1>"
    first = upload(client, headers, "old.html", content).json()
    path = urlsplit(first["version_url"]).path
    assert upload(client, headers, "new.html", b"new", first["id"]).status_code == 200
    for suffix in ("", "/raw", "/download"):
        response = client.head(path + suffix, follow_redirects=False)
        assert response.status_code == 401 and response.content == b""
        assert response.headers["cache-control"] == "no-store"
        assert client.head(RAW + path + suffix, follow_redirects=False).status_code == 404
    sign_in(client, MAIN, path)
    response = client.head(path)
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-security-policy"] == VIEWER_CSP
    assert int(response.headers["content-length"]) > 0
    response = client.head(path + "/raw")
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-security-policy"] == SANDBOX_CSP
    assert int(response.headers["content-length"]) == len(content) + len(PINS_SCRIPT)
    response = client.head(path + "/download")
    assert response.status_code == 200 and response.content == b""
    assert int(response.headers["content-length"]) == len(content)
    assert "old.html" in response.headers["content-disposition"]
    for suffix in ("", "/raw", "/download"):
        assert client.head(path + suffix).headers["cache-control"] == "no-store"


def test_response_version_link_remains_this_write_if_replaced_before_response(env, monkeypatch):
    client, store, drive, headers, _ = env
    initial = upload(client, headers, "first.txt", b"first").json()
    artifact_id = initial["id"]
    original = store.put_drive

    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        original(drive, io.BytesIO(b"third"), owner="agent", name="third.txt", kind="txt",
                 size=5, artifact_id=artifact_id)
        return result

    monkeypatch.setattr(store, "put_drive", race)
    response = upload(client, headers, "second.txt", b"second", artifact_id)
    assert response.status_code == 200
    path = urlsplit(response.json()["version_url"]).path
    sign_in(client, MAIN, path)
    assert client.get(path + "/download").content == b"second"
    assert client.get(f"/a/{artifact_id}/download").content == b"third"


def test_unpublished_remote_revision_has_no_version_link(env):
    client, store, drive, headers, _ = env
    first = upload(client, headers, "first.txt", b"first").json()
    artifact_id = first["id"]
    with store._conn() as db:
        before = db.execute("SELECT version_id FROM drive_versions").fetchall()
    drive.lose_response = True
    assert upload(client, headers, "x.txt", b"new", artifact_id).status_code == 503
    with store._conn() as db:
        assert db.execute("SELECT version_id FROM drive_versions").fetchall() == before
    sign_in(client, MAIN, urlsplit(first["version_url"]).path)
    assert client.get(urlsplit(first["version_url"]).path + "/download").content == b"first"


def test_pdf_head_ranges_and_unavailable_preserves_metadata(env):
    client, store, drive, headers, _ = env
    data = b"%PDF-1.4\nprivate\n%%EOF\n"
    artifact_id = upload(client, headers, "x.pdf", data).json()["id"]
    comment = store.agent_comment(artifact_id, "review", None, owner="agent",
                                  validate_anchor=lambda anchor, pin: None)
    sign_in(client, MAIN, f"/a/{artifact_id}")
    sign_in(client, RAW, f"/a/{artifact_id}/raw")
    for path in (f"/a/{artifact_id}/raw", f"/a/{artifact_id}/download",
                 RAW + f"/a/{artifact_id}/raw"):
        response = client.head(path)
        assert response.status_code == 200 and response.content == b""
        assert response.headers["content-length"] == str(len(data))
        response = client.get(path, headers={"Range": "bytes=2-6"})
        assert response.status_code == 206 and response.content == data[2:7]
    drive.failure = DriveAccessError("sensitive provider detail")
    for path in (f"/a/{artifact_id}", f"/a/{artifact_id}/raw", f"/a/{artifact_id}/download",
                 RAW + f"/a/{artifact_id}/raw"):
        response = client.get(path)
        assert response.status_code == 503
        assert b"sensitive provider detail" not in response.content
    assert store.purge_expired() == 0
    assert store.get_drive(artifact_id) is not None
    assert store.comments(artifact_id, owner="agent")[1] == [comment]


def test_drive_range_validators_identify_committed_revision(env, monkeypatch):
    client, _, _, headers, _ = env
    first = b"%PDF-1.4\n11111\n%%EOF\n"
    second = b"%PDF-1.4\n22222\n%%EOF\n"
    artifact_id = upload(client, headers, "x.pdf", first).json()["id"]
    sign_in(client, MAIN, f"/a/{artifact_id}")
    sign_in(client, RAW, f"/a/{artifact_id}/raw")
    original = _materialize_revision
    timestamp = 1700000000

    def same_mtime(*args):
        storage = original(*args)
        os.utime(storage.path, (timestamp, timestamp))
        return storage

    monkeypatch.setattr("agent_artifacts.web._materialize_revision", same_mtime)
    paths = (f"/a/{artifact_id}/raw", f"/a/{artifact_id}/download",
             RAW + f"/a/{artifact_id}/raw")
    etags = []
    for path in paths:
        response = client.get(path)
        assert response.content == first and "last-modified" not in response.headers
        etag = response.headers["etag"]
        etags.append(etag)
        resumed = client.get(path, headers={"Range": "bytes=2-6", "If-Range": etag})
        assert resumed.status_code == 206 and resumed.content == first[2:7]
        by_date = client.get(path, headers={"Range": "bytes=2-6",
                                            "If-Range": formatdate(timestamp, usegmt=True)})
        assert by_date.status_code == 200 and by_date.content == first
    assert upload(client, headers, "x.pdf", second, artifact_id).status_code == 200
    for path, etag in zip(paths, etags, strict=True):
        response = client.get(path, headers={"Range": "bytes=2-6", "If-Range": etag})
        assert response.status_code == 200 and response.content == second
        assert response.headers["etag"] != etag and "last-modified" not in response.headers
        by_date = client.get(path, headers={"Range": "bytes=2-6",
                                            "If-Range": formatdate(timestamp, usegmt=True)})
        assert by_date.status_code == 200 and by_date.content == second


def test_provider_errors_and_retention_are_safe(env):
    client, store, drive, headers, admin = env
    project = client.post("/api/v1/projects", headers=headers, json={"title": "P"}).json()["id"]
    artifact_id = upload(client, headers, "x.html", b"old", project=project).json()["id"]
    drive.failure = DriveTransientError("secret from provider")
    response = upload(client, headers, "x.html", b"new", artifact_id)
    assert response.status_code == 503 and b"secret from provider" not in response.content
    drive.failure = None
    response = upload(client, headers, "x.html", b"again", artifact_id)
    assert response.status_code == 409 and response.json()["error"] == "storage_conflict"
    for path, auth in ((f"/api/v1/artifacts/{artifact_id}", headers),
                       (f"/api/v1/projects/{project}", headers),
                       ("/api/v1/admin/users/agent", {"Authorization": f"Bearer {admin}"})):
        response = client.delete(path, headers=auth)
        assert response.status_code == 409 and response.json()["error"] == "retention_unresolved"
    assert store.get_drive(artifact_id) is not None


def test_temporary_revision_cleaned_after_failure_and_cancellation(tmp_path):
    class BrokenDrive:
        def open_revision(self, revision, *, control=None):
            raise DriveTransientError("unavailable")

    before = set(Path(tempfile.gettempdir()).glob("aa-drive-*"))
    with pytest.raises(DriveTransientError):
        _materialize_revision(BrokenDrive(), DriveRevision("file", "rev"), 3, 100,
                              DriveReadControl(time.monotonic() + 5))
    assert set(Path(tempfile.gettempdir()).glob("aa-drive-*")) == before

    class CancelledResponse(Response):
        async def __call__(self, scope, receive, send):
            raise asyncio.CancelledError

    directory = tempfile.TemporaryDirectory(dir=tmp_path)
    storage = _TemporaryStorage(directory)
    storage.path.write_bytes(b"content")
    opened = storage.open("id")
    response = _TemporaryResponse(CancelledResponse(), storage)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(response({}, None, None))
    assert opened.closed and not storage.path.exists()


@pytest.mark.parametrize("route", ["view", "raw", "download", "raw_origin"])
def test_asgi_disconnect_stops_drive_worker_before_response(env, monkeypatch, route):
    client, store, drive, headers, _ = env
    data = b"%PDF-1.4\nprivate\n%%EOF\n"
    artifact_id = upload(client, headers, "x.pdf", data).json()["id"]
    sign_in(client, MAIN, f"/a/{artifact_id}")
    sign_in(client, RAW, f"/a/{artifact_id}/raw")
    host = "raw.example" if route == "raw_origin" else "app.example"
    path = f"/a/{artifact_id}" + ("/raw" if route in ("raw", "raw_origin") else
                                  "/download" if route == "download" else "")
    cookie = client.cookies.get(SESSION_COOKIE, domain=host)
    entered, stopped, cleaned = threading.Event(), threading.Event(), threading.Event()
    original_storage = _TemporaryStorage

    class TrackedStorage(original_storage):
        def cleanup(self):
            super().cleanup()
            assert not self.path.exists()
            cleaned.set()

    @contextmanager
    def slow(revision, *, control):
        def chunks():
            entered.set()
            try:
                while True:
                    time.sleep(0.01)
                    control.check()
            finally:
                stopped.set()
            yield b""  # pragma: no cover
        yield chunks()

    monkeypatch.setattr("agent_artifacts.web._TemporaryStorage", TrackedStorage)
    monkeypatch.setattr(drive, "open_revision", slow)
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "GET", "scheme": "https", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "server": (host, 443), "client": ("127.0.0.1", 1234),
             "headers": [(b"host", host.encode()),
                         (b"cookie", f"{SESSION_COOKIE}={cookie}".encode())]}
    messages = []

    async def run():
        count = 0

        async def receive():
            nonlocal count
            count += 1
            if count == 1:
                return {"type": "http.request", "body": b"", "more_body": False}
            assert await asyncio.to_thread(entered.wait, 2)
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        await asyncio.wait_for(client.app(scope, receive, send), 1)
        assert await asyncio.to_thread(stopped.wait, 1)
        assert await asyncio.to_thread(cleaned.wait, 1)

    asyncio.run(run())
    assert messages == []
    assert store.get_drive(artifact_id) is not None


def test_asgi_deadline_stops_trickling_worker_returns_503(env, monkeypatch):
    client, store, drive, headers, _ = env
    data = b"%PDF-1.4\n" + b"x" * 100 + b"\n%%EOF\n"
    artifact_id = upload(client, headers, "x.pdf", data).json()["id"]
    sign_in(client, MAIN, f"/a/{artifact_id}")
    cookie = client.cookies.get(SESSION_COOKIE, domain="app.example")
    stopped, cleaned = threading.Event(), threading.Event()
    original_storage = _TemporaryStorage

    class TrackedStorage(original_storage):
        def cleanup(self):
            super().cleanup()
            cleaned.set()

    @contextmanager
    def trickle(revision, *, control):
        def chunks():
            try:
                for _ in data:
                    time.sleep(0.02)
                    control.check()
                    yield b"x"
            finally:
                stopped.set()
        yield chunks()

    monkeypatch.setattr("agent_artifacts.web._TemporaryStorage", TrackedStorage)
    monkeypatch.setattr("agent_artifacts.web._DRIVE_READ_BUDGET_SECONDS", 0.12)
    monkeypatch.setattr(drive, "open_revision", trickle)
    path = f"/a/{artifact_id}/raw"
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "GET", "scheme": "https", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "server": ("app.example", 443), "client": ("127.0.0.1", 1234),
             "headers": [(b"host", b"app.example"),
                         (b"cookie", f"{SESSION_COOKIE}={cookie}".encode())]}
    messages = []

    async def run():
        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        await asyncio.wait_for(client.app(scope, receive, send), 1)
        assert await asyncio.to_thread(stopped.wait, 1)
        assert await asyncio.to_thread(cleaned.wait, 1)

    asyncio.run(run())
    assert messages[0]["status"] == 503
    assert store.get_drive(artifact_id) is not None


def test_deadline_detaches_blocked_worker_then_cleans_and_reports(env, monkeypatch, caplog):
    client, store, drive, headers, _ = env
    data = b"%PDF-1.4\nprivate\n%%EOF\n"
    artifact_id = upload(client, headers, "x.pdf", data).json()["id"]
    sign_in(client, MAIN, f"/a/{artifact_id}")
    cookie = client.cookies.get(SESSION_COOKIE, domain="app.example")
    entered, release, stopped, cleaned = (threading.Event() for _ in range(4))
    original_storage = _TemporaryStorage

    class TrackedStorage(original_storage):
        def cleanup(self):
            super().cleanup()
            cleaned.set()

    @contextmanager
    def blocked(revision, *, control):
        def chunks():
            entered.set()
            assert release.wait(3)
            try:
                control.check()
            finally:
                stopped.set()
            yield data
        yield chunks()

    monkeypatch.setattr("agent_artifacts.web._TemporaryStorage", TrackedStorage)
    monkeypatch.setattr("agent_artifacts.web._DRIVE_READ_BUDGET_SECONDS", 0.1)
    monkeypatch.setattr(drive, "open_revision", blocked)
    path = f"/a/{artifact_id}/download"
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "GET", "scheme": "https", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "server": ("app.example", 443), "client": ("127.0.0.1", 1234),
             "headers": [(b"host", b"app.example"),
                         (b"cookie", f"{SESSION_COOKIE}={cookie}".encode())]}
    messages = []

    async def run():
        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        try:
            await asyncio.wait_for(client.app(scope, receive, send), 0.5)
            assert entered.is_set() and not stopped.is_set()
            assert messages[0]["status"] == 503
        finally:
            release.set()
        assert await asyncio.to_thread(stopped.wait, 1)
        assert await asyncio.to_thread(cleaned.wait, 1)
        await asyncio.sleep(0.02)

    asyncio.run(run())
    assert "detached Drive worker: DriveTransientError" in caplog.text
    assert store.get_drive(artifact_id) is not None


def test_cancel_during_materialization_cleans_finished_worker(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    paths = []

    def slow(*args):
        storage = _TemporaryStorage(tempfile.TemporaryDirectory(dir=tmp_path))
        storage.path.write_bytes(b"private")
        paths.append(storage.path)
        entered.set()
        assert release.wait(5)
        return storage

    monkeypatch.setattr("agent_artifacts.web._materialize_revision", slow)

    class Request:
        async def receive(self):
            await asyncio.Event().wait()

    async def run():
        task = asyncio.create_task(
            _materialize_async(Request(), None, DriveRevision("file", "rev"), 7, 100)
        )
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        assert paths[0].exists()
        release.set()
        for _ in range(100):
            if not paths[0].exists():
                return
            await asyncio.sleep(0.01)
        pytest.fail("late worker did not clean up")

    asyncio.run(run())
    assert paths and not paths[0].exists()


@pytest.mark.parametrize("failure", ["limit", "create", "write"])
def test_drive_staging_failures_return_safe_api_errors(tmp_path, monkeypatch, failure):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    config = Config(data_dir=tmp_path, public_url=MAIN,
                    workspace=WorkspaceConfig("client", "secret", "company.example"))
    store = Store(tmp_path, max_total_bytes=config.max_total_bytes)
    key = store.add_agent("agent")
    transport = FakeTransport()
    drive = DriveStorage(transport, "folder", max_bytes=2 if failure == "limit" else 100, timeout=3)
    original = tempfile.TemporaryFile
    opened = []

    class BrokenFile:
        def __init__(self):
            self.file = original()
            opened.append(self.file)

        def write(self, chunk):
            raise OSError("secret filesystem path")

        def close(self):
            self.file.close()

    def temp_file():
        if failure == "create":
            raise OSError("secret filesystem path")
        if failure == "write":
            return BrokenFile()
        file = original()
        opened.append(file)
        return file

    with TestClient(create_app(config, store, drive=drive), base_url=MAIN) as client:
        monkeypatch.setattr("agent_artifacts.drive_storage.tempfile.TemporaryFile", temp_file)
        response = upload(client, {"Authorization": f"Bearer {key}"}, "x.txt", b"abc")
    assert response.status_code == (413 if failure == "limit" else 503)
    code = "too_large" if failure == "limit" else "storage_unavailable"
    assert response.json()["error"] == code
    assert b"secret filesystem path" not in response.content
    assert all(call[0] == "GET" for call in transport.calls)
    assert all(file.closed for file in opened)
    assert store.drive_attempts() == [] and store.list("agent") == []


def test_public_restart_rejects_persisted_drive_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    workspace = Config(data_dir=tmp_path, public_url=MAIN,
                       workspace=WorkspaceConfig("client", "secret", "company.example"))
    store = Store(tmp_path, max_total_bytes=workspace.max_total_bytes)
    key = store.add_agent("agent")
    project = store.add_project("agent", "private project")
    art = store.put_drive(FakeDrive(), io.BytesIO(b"secret"), owner="agent",
                          name="x.txt", kind="txt", size=6, project=project.id)
    store.agent_comment(art.id, "private comment", None, owner="agent",
                        validate_anchor=lambda anchor, pin: None)
    public = Config(data_dir=tmp_path, public_url=MAIN)
    with pytest.raises(ValueError, match="Workspace"):
        create_app(public)
    reopened = Store(tmp_path, max_total_bytes=workspace.max_total_bytes)
    with pytest.raises(ValueError, match="Workspace"):
        create_app(public, reopened)
    with TestClient(create_app(workspace, reopened), base_url=MAIN) as client:
        for path in (f"/p/{project.id}", f"/a/{art.id}/comments"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code in (303, 401)
            assert b"private comment" not in response.content
        response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"})
        assert response.status_code == 200
    clean = Config(data_dir=tmp_path / "public", public_url=MAIN)
    with TestClient(create_app(clean), base_url=MAIN) as client:
        assert client.get("/health").status_code == 200


def test_drive_injection_requires_workspace(tmp_path):
    config = Config(data_dir=tmp_path, public_url=MAIN)
    with pytest.raises(ValueError, match="Workspace"):
        create_app(config, drive=FakeDrive())
