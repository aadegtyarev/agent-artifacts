"""Project title renames keep the registered folder and every child ID."""
import sqlite3
from contextlib import closing
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
import requests
from starlette.testclient import TestClient
from test_drive_archive import close
from test_drive_rename import sign_reader
from test_drive_web import upload
from test_workspace_drive import reply
from test_workspace_review_routes import env as report_env, writes  # noqa: F401
from test_workspace_web import MAIN

from agent_artifacts.drive_layout import UNFILED_NAME
from agent_artifacts.drive_recovery import inventory
from agent_artifacts.web import create_app


def folder_transport(env, monkeypatch, *, lose=None, can_rename=True):
    _, store, cloud, _, _, first, _ = env
    project = first["project"]
    with store._conn() as db:
        folder, = db.execute("SELECT folder_id FROM drive_folders WHERE scope=?", ("p-" + project,)).fetchone()
    base = cloud.request
    patches = []

    def request(actor, method, url, **kwargs):
        if urlsplit(url).path == "/drive/v3/files/" + folder:
            if method == "GET":
                result = base(actor, method, url, **kwargs)
                result.data["capabilities"]["canRename"] = can_rename
                return result
            assert actor == "author" and method == "PATCH"
            with store._conn() as db:
                assert db.execute("SELECT sent FROM drive_project_renames WHERE project_id=?",
                                  (project,)).fetchone() == (1,)
            data = kwargs["json"]
            assert set(data) == {"name", "appProperties"}
            assert data["appProperties"]["agentArtifactsFolder"] == "p-" + project
            assert set(kwargs["params"]) == {"fields", "supportsAllDrives"}
            patches.append(data)
            cloud.calls.append((actor, method, urlsplit(url).path))
            if lose != "unapplied":
                cloud.drive.entries[folder].update(data)
            if lose is not None:
                raise requests.Timeout("private provider response")
            return reply(200, {"id": folder})
        return base(actor, method, url, **kwargs)

    monkeypatch.setattr(cloud, "request", request)
    return folder, patches


def test_project_title_preserves_folder_file_versions_and_doc(report_env, monkeypatch):
    client, store, cloud, keys, config, first, second = report_env
    close(report_env)
    folder, patches = folder_transport(report_env, monkeypatch)
    project = first["project"]
    route = f"/api/v1/projects/{project}"
    ref = store.get_drive(first["id"], owner="author")[1]
    result = client.patch(route, headers=keys["author"], json={"title": UNFILED_NAME})
    assert result.status_code == 200 and result.json()["title"] == UNFILED_NAME
    assert result.json()["url"] == MAIN + "/p/" + project
    assert len(patches) == 1 and patches[0]["name"] == f"{UNFILED_NAME} · {project[:8]}"
    assert cloud.metadata["parents"] == cloud.drive.entries[ref.file_id]["parents"] == [folder]
    assert store.get_drive(first["id"], owner="author")[1] == ref
    with store._conn() as db:
        assert db.execute("SELECT doc_id FROM review_documents WHERE artifact_id=?", (first["id"],)).fetchone() == ("doc",)
        assert db.execute("SELECT folder_id,name FROM drive_folders WHERE scope=?", ("p-" + project,)).fetchone() == (
            folder, f"{UNFILED_NAME} · {project[:8]}")
    sign_reader(client, config)
    for version, content in ((first, b"old bytes"), (second, b"new bytes")):
        response = client.get(urlsplit(version["version_url"]).path + "/download")
        assert response.status_code == 200 and response.content == content
    cloud.calls.clear()
    assert client.patch(route, headers=keys["author"], json={"title": UNFILED_NAME}).status_code == 200
    assert len(patches) == 1 and not cloud.calls


def test_lost_project_rename_reply_is_reconciled_read_only_after_restart(report_env, monkeypatch, tmp_path):
    client, store, cloud, keys, config, first, _ = report_env
    folder, patches = folder_transport(report_env, monkeypatch, lose="applied")
    route = "/api/v1/projects/" + first["project"]
    body = {"title": "Name in Drive"}
    assert client.patch(route, headers=keys["author"], json=body).status_code == 503
    assert store.project(first["project"], owner="author")[0].title == "Project"
    backup = tmp_path / "backup.sqlite"
    with store._conn() as src, closing(sqlite3.connect(backup)) as out:
        src.backup(out)
    state = inventory(backup)
    assert state["metadata_schema"] == 19 and state["project_renames"][0]["sent"] == 1
    assert state["project_renames"][0]["folder_id"] == folder
    cloud.calls.clear()
    with TestClient(create_app(config), base_url=MAIN) as restarted:
        result = restarted.patch(route, headers=keys["author"], json=body)
        assert result.status_code == 200 and result.json()["title"] == body["title"]
    assert not writes(cloud) and len(patches) == 1


def test_unconfirmed_folder_rename_reserves_name_without_affecting_child_ids(report_env, monkeypatch):
    client, store, cloud, keys, _, first, _ = report_env
    folder, patches = folder_transport(report_env, monkeypatch, lose="unapplied")
    route = "/api/v1/projects/" + first["project"]
    expected = {"title": "Reserved"}
    assert client.patch(route, headers=keys["author"], json=expected).status_code == 503
    cloud.calls.clear()
    assert client.patch(route, headers=keys["author"], json={"title": "Different"}).status_code == 409
    assert client.patch(route, headers=keys["author"], json=expected).status_code == 409
    assert not writes(cloud) and len(patches) == 1
    existing = upload(client, keys["author"], "added.txt", b"same folder", project=first["project"]).json()
    file = store.get_drive(existing["id"], owner="author")[1].file_id
    assert cloud.drive.entries[file]["parents"] == [folder]
    other = client.post("/api/v1/projects", headers=keys["author"], json=expected).json()["id"]
    file = upload(client, keys["author"], "other.txt", b"different folder", project=other).json()
    file_id = store.get_drive(file["id"], owner="author")[1].file_id
    target, = cloud.drive.entries[file_id]["parents"]
    assert target != folder and cloud.drive.entries[target]["name"] == f"Reserved · {other[:8]}"


@pytest.mark.parametrize("failure", ["access", "outside", "unconfirmed-create"])
def test_folder_preflight_does_not_write_on_access_or_scope_failure(report_env, monkeypatch, failure):
    client, store, cloud, keys, _, first, _ = report_env
    folder, patches = folder_transport(report_env, monkeypatch, can_rename=failure != "access")
    if failure == "outside":
        cloud.drive.entries[folder]["parents"] = ["another-drive-root"]
    if failure == "unconfirmed-create":
        with store._conn(write=True) as db:
            db.execute("UPDATE drive_folders SET ready=0 WHERE folder_id=?", (folder,))
    out = client.patch("/api/v1/projects/" + first["project"], headers=keys["author"], json={"title": "Unsafe"})
    assert out.status_code == (409 if failure == "unconfirmed-create" else 403)
    assert not writes(cloud) and not patches
    with store._conn() as db:
        assert db.execute("SELECT count(*) FROM drive_project_renames").fetchone() == (0,)


def test_project_ownership_and_pause_but_empty_disk_project_renames_locally(report_env, monkeypatch):
    client, store, cloud, keys, config, first, _ = report_env
    _, patches = folder_transport(report_env, monkeypatch)
    route = "/api/v1/projects/" + first["project"]
    body = {"title": "Safe"}
    assert client.patch(route, headers=keys["viewer"], json=body).status_code == 404
    with TestClient(create_app(replace(config, drive_read_only=True), store), base_url=MAIN) as paused:
        out = paused.patch(route, headers=keys["author"], json=body)
        assert out.status_code == 503 and out.json()["error"] == "drive_read_only"
        key = "Bearer " + store.add_agent("disk-only-agent")
        disk = store.add_project("disk-only-agent", "Disk project")
        result = paused.patch("/api/v1/projects/" + disk.id, headers={"Authorization": key}, json=body)
        assert result.status_code == 200 and result.json()["title"] == body["title"]
    assert not cloud.calls and not patches
