"""Move coordination with a fake provider and a restart between possible writes."""
import json
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit

import pytest
import requests
from starlette.testclient import TestClient
from test_drive_archive import close
from test_drive_rename import sign_reader
from test_drive_web import upload
from test_workspace_drive import reply
from test_workspace_review_routes import endpoint, env as report_env, round_body  # noqa: F401

from agent_artifacts.drive_archive import ArchiveError, plan_archive
from agent_artifacts.drive_destination import DriveDestination
from agent_artifacts.drive_recovery import inventory
from agent_artifacts.web import create_app


def transport(env, monkeypatch, *, lose=(), different_permissions=False):
    _, store, cloud, *_ = env
    original = cloud.request
    patches = []
    drop = list(lose)
    folders = set()

    def request(actor, method, url, **kwargs):
        path = urlsplit(url).path
        if path.endswith("/permissions"):
            assert actor == "author" and method == "GET"
            folder = path.split("/")[-2]
            folders.add(folder)
            owner = {"id": "owner", "type": "user", "role": "owner"}
            rows = [owner]
            if different_permissions and len(folders) == 2:
                rows.append({"id": "second-member", "type": "user", "role": "reader"})
            return reply(200, {"permissions": rows})
        if path.startswith("/drive/v3/files/"):
            file_id = path.rsplit("/", 1)[-1]
            fields = kwargs.get("params", {}).get("fields", "")
            entry = cloud.metadata if file_id == "doc" else cloud.drive.entries.get(file_id)
            if method == "GET" and "ownedByMe" in fields:
                r = original(actor, method, url, **kwargs)
                r.data["ownedByMe"] = True
                return r
            if method == "GET" and "canMoveItemWithinDrive" in fields:
                raw = original(actor, method, url, **kwargs)
                try:
                    data = json.loads(b"".join(raw.iter_content(4096)))
                finally:
                    raw.close()
                data["capabilities"].update(canMoveItemWithinDrive=True, canRename=True)
                r = reply(200, data)
                r.headers = {"ETag": '"move-precondition"'}
                return r
            if method == "PATCH" and "removeParents" in kwargs["params"]:
                assert actor == "author" and entry is not None
                with store._conn() as db:
                    claim = db.execute("SELECT blob_sent,doc_sent FROM drive_moves WHERE file_id=? OR doc_id=?",
                                       (file_id, file_id)).fetchone()
                assert claim == ((1, 1) if file_id == "doc" else (1, 0))
                assert kwargs["headers"]["If-Match"] == '"move-precondition"'
                params = kwargs["params"]
                body = kwargs["json"]
                assert params["supportsAllDrives"] == "true" and "uploadType" not in params
                assert entry["parents"] == [params["removeParents"]]
                assert set(body) == ({"appProperties"} if file_id == "doc" else {"name", "appProperties"})
                entry["appProperties"].update(body["appProperties"])
                if "name" in body:
                    entry["name"] = body["name"]
                entry["parents"] = [params["addParents"]]
                patches.append(file_id)
                if drop and drop[0] == file_id:
                    drop.pop(0)
                    raise requests.Timeout("private provider error")
                return reply(200, {"id": file_id})
        return original(actor, method, url, **kwargs)

    monkeypatch.setattr(cloud, "request", request)
    return patches


def test_unfiled_to_project_preserves_the_exact_blob_and_allocates_a_free_name(report_env, monkeypatch):
    client, store, cloud, keys, config, first, _ = report_env
    owner = keys["author"]
    made = upload(client, owner, "review.txt", b"unfiled original")
    assert made.status_code == 201
    item = made.json()
    href = "/api/v1/artifacts/" + item["id"]
    revision = store.get_drive(item["id"], owner="author")[1]
    actions = transport(report_env, monkeypatch)
    change = {"project": first["project"], "section": "Moved"}
    response = client.patch(href, headers=owner, json=change)
    assert response.status_code == 200
    moved = response.json()
    assert moved["url"] == item["url"] and moved["version_url"] == item["version_url"]
    assert moved["project"] == first["project"] and moved["section"] == "Moved" and moved["expires_at"] is None
    assert store.get_drive(item["id"], owner="author")[1] == revision
    assert cloud.drive.entries[revision.file_id]["name"] == f"review · {item['id'][:8]}.txt"
    assert client.patch(href, headers=owner, json=change).json() == moved
    assert actions == [revision.file_id]
    sign_reader(client, config)
    assert client.get(urlsplit(item["version_url"]).path + "/download").content == b"unfiled original"


def test_partial_blob_and_doc_moves_reconcile_by_id_and_keep_the_original_project_until_both(report_env, monkeypatch, tmp_path):
    client, store, cloud, keys, config, first, second = report_env
    close(report_env)
    target = client.post("/api/v1/projects", headers=keys["author"], json={"title": "Finished work"}).json()
    href = "/api/v1/artifacts/" + first["id"]
    file_id = store.get_drive(first["id"], owner="author")[1].file_id
    actions = transport(report_env, monkeypatch, lose=(file_id, "doc"))
    change = {"project": target["id"], "title": "Moved report", "section": "Reports"}
    assert client.patch(href, headers=keys["viewer"], json=change).status_code == 404
    assert client.patch(href, headers=keys["author"], json=change).status_code == 503
    assert store.get(first["id"]).project == first["project"]
    assert cloud.drive.entries[file_id]["parents"] != cloud.metadata["parents"]
    assert client.patch(href, headers=keys["author"], json=change | {"title": "Different"}).status_code == 409
    assert upload(client, keys["author"], "review.txt", b"unexpected replace", first["id"]).status_code == 409
    assert client.post(endpoint(first), headers=keys["author"], json=round_body(first)).status_code == 409
    assert client.delete(f"/api/v1/projects/{target['id']}", headers=keys["author"]).status_code == 409
    backup = tmp_path / "move.sqlite"
    with store._conn() as current, closing(sqlite3.connect(backup)) as out:
        current.backup(out)
    ticket, = inventory(backup)["moves"]
    assert (ticket["file_id"], ticket["doc_id"], ticket["blob_sent"], ticket["doc_sent"]) == (file_id, "doc", 1, 0)
    policy = DriveDestination.from_config(config.drive, config.workspace, config.public_url)
    with pytest.raises(ArchiveError, match="pending"):
        plan_archive(backup, policy, first["id"], include_report=True)
    with TestClient(create_app(config), base_url=config.public_url) as restarted:
        assert restarted.patch(href, headers=keys["author"], json=change).status_code == 503
        assert store.get(first["id"]).project == first["project"]
        response = restarted.patch(href, headers=keys["author"], json=change)
        assert response.json()["version_url"] == second["version_url"]
    assert actions == [file_id, "doc"]
    assert store.get(first["id"]).project == target["id"]
    assert cloud.drive.entries[file_id]["parents"] == cloud.metadata["parents"]
    with store._conn() as db:
        assert db.execute("SELECT artifact_id,doc_id FROM review_documents").fetchone() == (first["id"], "doc")
        assert db.execute("SELECT count(*) FROM drive_moves").fetchone() == (0,)
    sign_reader(client, config)
    for item, data in ((first, b"old bytes"), (second, b"new bytes")):
        assert client.get(urlsplit(item["version_url"]).path + "/download").content == data


def test_move_does_not_cross_different_folder_permissions(report_env, monkeypatch):
    client, store, cloud, keys, _, first, _ = report_env
    owner = keys["author"]
    target = client.post("/api/v1/projects", headers=owner, json={"title": "Other scope"}).json()
    assert upload(client, owner, "existing.txt", b"existing file", project=target["id"]).status_code == 201
    actions = transport(report_env, monkeypatch, different_permissions=True)
    result = client.patch("/api/v1/artifacts/" + first["id"], headers=owner, json={"project": target["id"]})
    assert result.status_code == 403
    assert not actions and store.get(first["id"]).project == first["project"]
    with store._conn() as db:
        assert db.execute("SELECT count(*) FROM drive_moves").fetchone() == (0,)
