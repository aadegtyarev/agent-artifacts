"""Project layout through the real store, HTTP routes and Drive adapter; no network."""

import json
from urllib.parse import urlsplit

import pytest
import requests
from starlette.testclient import TestClient
from test_drive_storage import Response
from test_drive_web import upload
from test_workspace_web import MAIN, FakeGoogle, sign_in

from agent_artifacts.config import Config, WorkspaceConfig
from agent_artifacts.drive_storage import DriveAccessError, DriveStorage
from agent_artifacts.store import Store
from agent_artifacts.web import create_app

FOLDER = "application/vnd.google-apps.folder"


class LayoutDrive:
    def __init__(self, folder_kind="shared-drive", *, root="root"):
        self.folder_kind = folder_kind
        self.root = root
        self.entries = {root: {"id": root, "mimeType": FOLDER},
                        "drive": {"id": "drive", "mimeType": FOLDER}}
        self.revisions = {}
        self.next_id = 0
        self.folder_posts = 0
        self.blob_posts = 0
        self.lose_folder_response = False

    def request(self, method, url, **kw):
        assert url.startswith("https://www.googleapis.com/")
        assert kw["allow_redirects"] is False and kw["stream"] is True
        parts = urlsplit(url).path.split("/")
        if parts[-1] == "generateIds":
            self.next_id += 1
            return Response(data={"ids": [f"id-{self.next_id}"]})
        if "/upload/" in url:
            body = kw["data"].read()
            boundary = kw["headers"]["Content-Type"].split("boundary=")[1].encode()
            blocks = body.split(b"--" + boundary)
            meta = json.loads(blocks[1].split(b"\r\n\r\n", 1)[1])
            content = blocks[2].split(b"\r\n\r\n", 1)[1][:-2]
            if method == "POST":
                self.blob_posts += 1
                file_id = meta["id"]
                assert file_id not in self.entries
                self.entries[file_id] = meta
                self.revisions[file_id] = []
            else:
                assert method == "PATCH"
                file_id = parts[-1]
                self.entries[file_id].update(meta)
            self.revisions[file_id].append(content)
            head = str(len(self.revisions[file_id]))
            self.entries[file_id]["headRevisionId"] = head
            return Response(data={"id": file_id, "headRevisionId": head})
        if method == "POST":
            self.folder_posts += 1
            assert parts[-1] == "files" and kw["params"]["supportsAllDrives"] == "true"
            meta = kw["json"]
            assert meta["id"] not in self.entries and meta["parents"] == [self.root]
            assert meta["mimeType"] == FOLDER
            self.entries[meta["id"]] = meta
            if self.lose_folder_response:
                self.lose_folder_response = False
                raise requests.Timeout("response lost")
            file_id = meta["id"]
        elif "revisions" in parts:
            return Response(content=self.revisions[parts[-3]][int(parts[-1]) - 1])
        else:
            assert method == "GET"
            file_id = parts[-1]
        if file_id not in self.entries:
            return Response(status=404)
        location = {"driveId": "drive"} if self.folder_kind == "shared-drive" else {}
        return Response(data={**location, "trashed": False,
                              "capabilities": {"canEdit": True, "canAddChildren": True,
                                               "canListChildren": True, "canDownload": True},
                              **self.entries[file_id]})


@pytest.fixture(params=["shared-drive", "my-drive"])
def env(tmp_path, monkeypatch, request):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    store = Store(tmp_path, max_total_bytes=100_000)
    key = store.add_agent("owner")
    drive = LayoutDrive(request.param)
    config = Config(tmp_path, MAIN, workspace=WorkspaceConfig("client", "fake", "company.example"))
    storage = DriveStorage(drive, "root", max_bytes=1000, timeout=3, project_layout=True,
                           folder_kind=request.param)
    with TestClient(create_app(config, store, drive=storage), base_url=MAIN) as browser:
        yield browser, store, drive, {"Authorization": f"Bearer {key}"}


def ref(store, artifact):
    return store.get_drive(artifact["id"])[1].file_id


def test_readable_names_projects_sections_and_exact_revisions(env):
    browser, store, drive, key = env
    project = store.add_project("owner", "Пусконаладка")
    duplicate_project = store.add_project("owner", "ПУСКОНАЛАДКА")
    first = upload(browser, key, "source.html", b"<p>First</p>", title="Экран настройки",
                   project=project.id, section="UI").json()
    dup = upload(browser, key, "other.html", b"<p>Second</p>", title="ЭКРАН НАСТРОЙКИ",
                 project=project.id, section="Notes").json()
    elsewhere = upload(browser, key, "source.html", b"<p>Elsewhere</p>", title="Экран настройки",
                       project=duplicate_project.id).json()
    free = upload(browser, key, "notes.md", b"notes", title="Заметки.md").json()
    first_id = ref(store, first)
    one, two, three, four = [drive.entries[ref(store, artifact)] for artifact in (first, dup, elsewhere, free)]
    assert one["name"] == three["name"] == "Экран настройки.html"
    assert two["name"] == f"ЭКРАН НАСТРОЙКИ · {dup['id'][:8]}.html"
    assert four["name"] == "Заметки.md"
    assert one["parents"] == two["parents"] and one["parents"] != three["parents"]
    assert drive.entries[one["parents"][0]]["name"] == "Пусконаладка"
    assert drive.entries[three["parents"][0]]["name"] == f"ПУСКОНАЛАДКА · {duplicate_project.id[:8]}"
    assert drive.entries[four["parents"][0]]["name"] == "Без проекта"
    assert drive.folder_posts == 3  # sections were not made into folders

    replacement = upload(browser, key, "source.html", b"<p>Changed</p>", first["id"])
    assert replacement.status_code == 200 and ref(store, replacement.json()) == first_id
    assert len(drive.revisions[first_id]) == 2 and drive.folder_posts == 3 and drive.blob_posts == 4
    version = urlsplit(first["version_url"]).path
    current = urlsplit(first["url"]).path
    assert browser.get(version + "/download").status_code == 401
    sign_in(browser, MAIN, version)
    assert browser.get(version + "/download").content == b"<p>First</p>"
    assert browser.get(current + "/download").content == b"<p>Changed</p>"
    drive.entries[one["parents"][0]]["parents"] = ["foreign"]
    assert browser.get(version + "/download").status_code == 503


def test_lost_folder_response_is_checked_without_second_create(env):
    browser, store, drive, key = env
    project = store.add_project("owner", "Project")
    drive.lose_folder_response = True
    first = upload(browser, key, "sample.txt", b"test", project=project.id)
    assert first.status_code == 503 and drive.folder_posts == 1 and drive.blob_posts == 0
    assert store.list("owner") == [] and store.drive_attempts() == []
    with store._conn() as db:
        folder_id, ready = db.execute("SELECT folder_id, ready FROM drive_folders").fetchone()
    assert ready == 0
    formed = drive.entries.pop(folder_id)
    unconfirmed = upload(browser, key, "sample.txt", b"test", project=project.id)
    assert unconfirmed.status_code == 503 and drive.folder_posts == 1 and drive.blob_posts == 0
    drive.entries[folder_id] = formed
    second = upload(browser, key, "sample.txt", b"test", project=project.id)
    assert second.status_code == 201 and drive.folder_posts == drive.blob_posts == 1


def test_project_layout_refuses_the_root_of_an_entire_shared_drive():
    fake = LayoutDrive()
    storage = DriveStorage(fake, "drive", max_bytes=1000, timeout=3, project_layout=True)
    with pytest.raises(DriveAccessError):
        storage.validate_folder()
    assert fake.folder_posts == fake.blob_posts == 0


@pytest.mark.parametrize(("actual", "configured"), [("my-drive", "shared-drive"), ("shared-drive", "my-drive")])
def test_project_layout_refuses_mismatched_drive_kind(actual, configured):
    fake = LayoutDrive(actual)
    storage = DriveStorage(fake, "root", max_bytes=1000, timeout=3,
                           project_layout=True, folder_kind=configured)
    with pytest.raises(DriveAccessError):
        storage.validate_folder()
    assert fake.folder_posts == fake.blob_posts == 0


def test_read_permission_is_enough_to_open_an_exact_revision_but_not_to_write(env):
    browser, store, drive, key = env
    project = store.add_project("owner", "Read-only check")
    item = upload(browser, key, "sample.txt", b"kept bytes", project=project.id).json()
    exact = urlsplit(item["version_url"]).path
    drive.entries["root"]["capabilities"] = {"canListChildren": True, "canAddChildren": False}
    sign_in(browser, MAIN, exact)
    response = browser.get(exact + "/download")
    assert response.status_code == 200 and response.content == b"kept bytes"
    assert upload(browser, key, "sample.txt", b"attempted replacement", item["id"]).status_code == 503
    assert drive.revisions[ref(store, item)] == [b"kept bytes"]
    root_entry = drive.entries["root"]
    root_entry["capabilities"]["canListChildren"] = False
    assert browser.get(exact + "/download").status_code == 503


def test_file_moved_to_another_drive_cannot_be_read_or_replaced(env):
    browser, store, drive, key = env
    project = store.add_project("owner", "Move boundary")
    item = upload(browser, key, "sample.txt", b"kept bytes", project=project.id).json()
    exact = urlsplit(item["version_url"]).path
    file_id = ref(store, item)
    drive.entries[file_id]["driveId"] = "another-shared-drive"
    sign_in(browser, MAIN, exact)
    assert browser.get(exact + "/download").status_code == 503
    assert upload(browser, key, "sample.txt", b"attempted replacement", item["id"]).status_code == 503
    assert drive.revisions[file_id] == [b"kept bytes"]


def test_test_slice_does_not_silently_move_or_rename_drive_items(env):
    browser, store, drive, key = env
    a = store.add_project("owner", "Project A")
    b = store.add_project("owner", "Project B")
    item = upload(browser, key, "sample.txt", b"test", project=a.id).json()
    path = f"/api/v1/artifacts/{item['id']}"
    assert browser.patch(path, headers=key, json={"project": b.id}).status_code == 409
    assert browser.patch(path, headers=key, json={"title": "New name"}).status_code == 409
    assert browser.patch(f"/api/v1/projects/{a.id}", headers=key, json={"title": "New"}).status_code == 409
    assert upload(browser, key, "sample.txt", b"change", item["id"], project=b.id).status_code == 409
    assert browser.patch(path, headers=key, json={"section": "Drafts"}).status_code == 200
    assert drive.folder_posts == 1 and len(drive.revisions[ref(store, item)]) == 1
