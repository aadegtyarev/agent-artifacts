import io

import pytest
from starlette.testclient import TestClient

from agent_artifacts.config import Config
from agent_artifacts.pages import PINS_SCRIPT, SANDBOX_CSP
from agent_artifacts.storage import DiskStorage
from agent_artifacts.store import Store
from agent_artifacts.web import create_app


def test_existing_disk_files_and_failed_replacement(tmp_path, monkeypatch):
    disk = DiskStorage(tmp_path)
    store = Store(tmp_path, max_total_bytes=10000, storage=disk)
    key = store.add_agent("alice")
    art = store.put(io.BytesIO(b"<b>old</b>"), owner="alice", name="a.html", kind="html", size=10)
    comment = store.agent_comment(
        art.id, "review", {"type": "element"}, owner="alice",
        validate_anchor=lambda anchor, pin: '{"type":"element"}',
    )
    before = store.get(art.id)

    def fail(*args):
        raise OSError("rename failed")

    monkeypatch.setattr("agent_artifacts.storage.os.replace", fail)
    with pytest.raises(OSError, match="rename failed"):
        store.put(io.BytesIO(b"new"), owner="alice", name="new.html", kind="html", size=3,
                  artifact_id=art.id)
    assert store.get(art.id) == before
    assert store.comments(art.id, owner="alice")[1] == [comment]
    assert list((tmp_path / "files").iterdir()) == [tmp_path / "files" / art.id]
    config = Config(data_dir=tmp_path, public_url="https://art.example.org", max_total_bytes=10000)
    with TestClient(create_app(config, store)) as client:
        raw = client.get(f"/a/{art.id}/raw")
        assert raw.content == b"<b>old</b>" + PINS_SCRIPT
        assert raw.headers["content-security-policy"] == SANDBOX_CSP
        download = client.get(f"/a/{art.id}/download", headers={"Range": "bytes=0-2"})
        assert download.status_code == 206 and download.content == b"<b>"
        assert client.head(f"/a/{art.id}/download").headers["content-length"] == "10"
        deleted = client.delete(
            f"/api/v1/artifacts/{art.id}", headers={"Authorization": f"Bearer {key}"}
        )
        assert deleted.status_code == 204
        assert not (tmp_path / "files" / art.id).exists()


@pytest.mark.parametrize("ttl", [0, -10])
def test_nonpositive_ttl_disk_upload_and_project(tmp_path, ttl):
    store = Store(tmp_path, max_total_bytes=10000, ttl_seconds=ttl)
    key = store.add_agent("alice")
    project = store.add_project("alice", "Project")
    config = Config(data_dir=tmp_path, public_url="https://art.example.org", max_total_bytes=10000)
    with TestClient(create_app(config, store)) as client:
        headers = {"Authorization": f"Bearer {key}"}
        response = client.post("/api/v1/artifacts", headers=headers,
                               files={"file": ("a.txt", b"x")})
        assert response.status_code == 404
        response = client.post("/api/v1/artifacts", headers=headers,
                               files={"file": ("b.txt", b"y")}, data={"project": project.id})
        assert response.status_code == 201
        assert response.json()["project"] == project.id


def test_failed_staging_cleans_temporary(tmp_path):
    class Broken(io.BytesIO):
        def read(self, size=-1):
            raise OSError("read failed")

    store = Store(tmp_path, max_total_bytes=100)
    store.add_agent("alice")
    with pytest.raises(OSError, match="read failed"):
        store.put(Broken(), owner="alice", name="a.txt", kind="txt", size=1)
    assert list((tmp_path / "files").iterdir()) == []


def test_upload_clock_is_sampled_after_copy(tmp_path):
    now = [100.0]
    store = Store(tmp_path, max_total_bytes=100, ttl_seconds=10, clock=lambda: now[0])
    store.add_agent("alice")
    old = store.put(io.BytesIO(b"old"), owner="alice", name="a.txt", kind="txt", size=3)

    class SlowCopy(io.BytesIO):
        def read(self, size=-1):
            now[0] = 111.0
            return super().read(size)

    replaced = store.put(
        SlowCopy(b"new"), owner="alice", name="a.txt", kind="txt", size=3, artifact_id=old.id
    )
    assert replaced is None
    assert (tmp_path / "files" / old.id).read_bytes() == b"old"
    assert [p.name for p in (tmp_path / "files").iterdir()] == [old.id]
    now[0] = 100.0
    fresh = store.put(SlowCopy(b"new"), owner="alice", name="b.txt", kind="txt", size=3)
    assert (fresh.created_at, fresh.expires_at) == (111.0, 121.0)


def test_symlink_loop_is_not_served_or_treated_as_lost(tmp_path, monkeypatch):
    store = Store(tmp_path, max_total_bytes=100)
    store.add_agent("alice")
    art = store.put(io.BytesIO(b"old"), owner="alice", name="a.txt", kind="txt", size=3)
    path = tmp_path / "files" / art.id
    path.unlink()
    path.symlink_to(path.name)
    config = Config(data_dir=tmp_path, public_url="https://art.example.org")
    with TestClient(create_app(config, store)) as client:
        assert client.get(f"/a/{art.id}").status_code == 404
        assert client.get(f"/a/{art.id}/raw").status_code == 404
    # Isolate the lost-file check from the unrelated orphan scan.
    monkeypatch.setattr(store.storage, "cleanup", lambda known, stale: None)
    assert store.purge_expired() == 0
    assert store.get(art.id) == art


def test_default_app_reopens_existing_pdf_layout(tmp_path):
    import time

    seed = Store(tmp_path, max_total_bytes=10000)
    seed.add_agent("alice")
    artifact_id = "abcdefghijklmnopqrstuv"
    content = b"%PDF-1.4\nlegacy bytes\n%%EOF\n"
    now = time.time()
    with seed._conn(write=True) as db:
        db.execute(
            "INSERT INTO artifacts (id, owner, name, title, kind, size, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id, "alice", "legacy.pdf", "legacy.pdf", "pdf", len(content),
                now, now + 3600,
            ),
        )
    (tmp_path / "files" / artifact_id).write_bytes(content)
    config = Config(data_dir=tmp_path, public_url="https://art.example.org")
    with TestClient(create_app(config)) as client:
        url = f"/a/{artifact_id}"
        assert client.get(url).status_code == 200
        raw = client.get(url + "/raw")
        assert raw.status_code == 200 and raw.content == content
        assert raw.headers["content-type"] == "application/pdf"
        assert raw.headers["content-security-policy"] == "frame-ancestors 'self'"
        assert raw.headers["cache-control"] == "no-store"
        for suffix in ("/raw", "/download"):
            head = client.head(url + suffix)
            assert head.status_code == 200 and head.content == b""
            assert head.headers["content-length"] == str(len(content))
            part = client.get(url + suffix, headers={"Range": "bytes=2-6"})
            assert part.status_code == 206 and part.content == content[2:7]
            assert part.headers["content-range"] == f"bytes 2-6/{len(content)}"
        download = client.get(url + "/download")
        assert download.content == content
        assert 'filename="legacy.pdf"' in download.headers["content-disposition"]


def test_page_preparation_runs_in_worker_and_closes_on_error(tmp_path, monkeypatch):
    import asyncio

    from agent_artifacts import pages

    disk = DiskStorage(tmp_path)
    store = Store(tmp_path, max_total_bytes=100, storage=disk)
    store.add_agent("alice")
    art = store.put(io.BytesIO(b"hello"), owner="alice", name="a.txt", kind="txt", size=5)
    opened = []
    original_open, original_page = disk.open, pages.text_page

    def check_worker():
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()

    def open_file(artifact_id):
        check_worker()
        f = original_open(artifact_id)
        opened.append(f)
        return f

    def build(f, title):
        check_worker()
        return original_page(f, title)

    monkeypatch.setattr(disk, "open", open_file)
    monkeypatch.setattr(pages, "text_page", build)
    config = Config(data_dir=tmp_path, public_url="https://art.example.org")
    with TestClient(create_app(config, store)) as client:
        assert client.get(f"/a/{art.id}/raw").status_code == 200
        assert opened[-1].closed

        def fail(f, title):
            check_worker()
            raise ValueError("template failed")

        monkeypatch.setattr(pages, "text_page", fail)
        with pytest.raises(ValueError, match="template failed"):
            client.get(f"/a/{art.id}/raw")
        assert opened[-1].closed
