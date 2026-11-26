"""A file title changes without new remote objects or revisions, even after a lost reply."""
import sqlite3
from contextlib import closing
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
import requests
from starlette.testclient import TestClient
from test_drive_archive import close
from test_drive_web import upload
from test_workspace_drive import POLICY
from test_workspace_review_routes import endpoint, env as report_env, round_body, writes  # noqa: F401
from test_workspace_web import MAIN

from agent_artifacts.drive_archive import ArchiveError, plan_archive
from agent_artifacts.drive_destination import DriveDestination
from agent_artifacts.drive_recovery import inventory
from agent_artifacts.google_oidc import GoogleIdentity
from agent_artifacts.web import create_app
from agent_artifacts.workspace_auth import SESSION_COOKIE, WorkspaceAuthStore


def rename_transport(env, monkeypatch, *, lose=None, can_rename=True):
    _, store, cloud, _, _, first, _ = env
    file_id = store.get_drive(first["id"], owner="author")[1].file_id
    base = cloud.request
    patches = []

    def request(actor, method, url, **kwargs):
        if urlsplit(url).path == "/drive/v3/files/" + file_id:
            if method == "GET":
                reply = base(actor, method, url, **kwargs)
                reply.data["capabilities"]["canRename"] = can_rename
                return reply
            assert actor == "author" and method == "PATCH"
            with store._conn() as db:
                assert db.execute("SELECT sent FROM drive_renames WHERE artifact_id=?", (first["id"],)).fetchone() == (1,)
            payload = kwargs["json"]
            entry = cloud.drive.entries[file_id]
            assert set(payload) == {"name", "appProperties"}
            assert payload["appProperties"]["agentArtifactsOperation"] == entry["appProperties"]["agentArtifactsOperation"]
            assert all(key not in kwargs["params"] for key in ("addParents", "removeParents", "uploadType"))
            patches.append(payload)
            cloud.calls.append((actor, method, urlsplit(url).path))
            if lose != "unapplied":
                entry.update(payload)
            if lose is not None:
                raise requests.Timeout("private error")
            from test_workspace_drive import reply
            return reply(200, {"id": file_id})
        return base(actor, method, url, **kwargs)

    monkeypatch.setattr(cloud, "request", request)
    return patches, file_id


def sign_reader(client, config):
    auth = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", POLICY)
    cookie = auth.create_session(MAIN, GoogleIdentity("author", "author@company.example", "Author"))
    client.cookies.set(SESSION_COOKIE, cookie, domain=urlsplit(MAIN).netloc, path="/")


def test_rename_keeps_original_file_report_and_every_revision(report_env, monkeypatch):
    client, store, cloud, keys, config, first, second = report_env
    close(report_env)
    patches, file_id = rename_transport(report_env, monkeypatch)
    href = "/api/v1/artifacts/" + first["id"]
    result = client.patch(href, headers=keys["author"], json={"title": "Renamed — UI"})
    assert result.status_code == 200 and result.json()["title"] == "Renamed — UI"
    assert result.json()["version_url"] == second["version_url"]
    assert patches[0]["name"] == "Renamed — UI.txt" and len(patches) == 1
    assert cloud.appends == 2 and cloud.docs.compact_posts == 1
    with store._conn() as db:
        assert db.execute("SELECT file_id,revision_id FROM drive_refs").fetchone() == (file_id, "2")
        assert db.execute("SELECT doc_id,file_id FROM review_documents").fetchone() == ("doc", file_id)
        assert db.execute("SELECT count(*) FROM drive_renames").fetchone() == (0,)
    sign_reader(client, config)
    for version, content in ((first, b"old bytes"), (second, b"new bytes")):
        assert client.get(urlsplit(version["version_url"]).path + "/download").content == content
    cloud.calls.clear()
    assert client.patch(href, headers=keys["author"], json={"title": "Renamed — UI"}).status_code == 200
    assert not cloud.calls and len(patches) == 1


def test_lost_rename_response_survives_restart_without_replay(report_env, monkeypatch, tmp_path):
    client, store, cloud, keys, config, first, _ = report_env
    patches, _ = rename_transport(report_env, monkeypatch, lose="applied")
    href = "/api/v1/artifacts/" + first["id"]
    values = {"title": "Changed remotely"}
    assert client.patch(href, headers=keys["author"], json=values).status_code == 503
    assert store.get_drive(first["id"], owner="author")[0].title != values["title"]
    copy = tmp_path / "snapshot.sqlite"
    with store._conn() as src, closing(sqlite3.connect(copy)) as out:
        src.backup(out)
    assert inventory(copy)["renames"][0]["sent"] == 1
    target = DriveDestination.from_config(config.drive, config.workspace, config.public_url)
    with pytest.raises(ArchiveError, match="pending"):
        plan_archive(copy, target, first["id"])
    cloud.calls.clear()
    with TestClient(create_app(config), base_url=MAIN) as restarted:
        result = restarted.patch(href, headers=keys["author"], json=values)
        assert result.status_code == 200 and result.json()["title"] == values["title"]
    assert len(patches) == 1 and not writes(cloud)


def test_ambiguous_unchanged_name_blocks_other_writes_and_reserves_new_name(report_env, monkeypatch):
    client, store, cloud, keys, _, first, _ = report_env
    patches, file_id = rename_transport(report_env, monkeypatch, lose="unapplied")
    href = "/api/v1/artifacts/" + first["id"]
    proposal = {"title": "Reserved name"}
    assert client.patch(href, headers=keys["author"], json=proposal).status_code == 503
    cloud.calls.clear()
    assert client.patch(href, headers=keys["author"], json={"title": "Other"}).status_code == 409
    assert client.patch(href, headers=keys["author"], json=proposal).status_code == 409
    assert upload(client, keys["author"], "review.txt", b"blocked replacement", first["id"]).status_code == 409
    assert client.post(endpoint(first), headers=keys["author"], json=round_body(first)).status_code == 409
    assert not writes(cloud) and len(patches) == 1
    new = upload(client, keys["author"], "other.txt", b"another file", title="Reserved name",
                 project=first["project"]).json()
    new_id = store.get_drive(new["id"], owner="author")[1].file_id
    assert new_id != file_id and cloud.drive.entries[new_id]["name"] == f"Reserved name · {new['id'][:8]}.txt"


@pytest.mark.parametrize("change", ["missing-can-rename", "outside-parent", "different-head"])
def test_preflight_refuses_without_google_mutation(report_env, monkeypatch, change):
    client, store, cloud, keys, _, first, _ = report_env
    patches, file = rename_transport(report_env, monkeypatch, can_rename=change != "missing-can-rename")
    if change == "outside-parent":
        cloud.drive.entries[file]["parents"] = ["wrong-managed-folder"]
    elif change == "different-head":
        cloud.drive.entries[file]["headRevisionId"] = "new-foreign-head"
    href = "/api/v1/artifacts/" + first["id"]
    result = client.patch(href, headers=keys["author"], json={"title": "Should not change"})
    assert result.status_code == (409 if change == "different-head" else 403)
    assert not patches and not writes(cloud)
    with store._conn() as db:
        assert db.execute("SELECT count(*) FROM drive_renames").fetchone() == (0,)


def test_rename_respects_owner_read_only_and_keeps_placement_separate(report_env, monkeypatch):
    client, store, cloud, keys, config, first, _ = report_env
    patches, _ = rename_transport(report_env, monkeypatch)
    href = "/api/v1/artifacts/" + first["id"]
    data = {"title": "New name"}
    assert client.patch(href, headers=keys["viewer"], json=data).status_code == 404
    assert client.patch(href, headers=keys["author"], json=data | {"project": None}).status_code == 409
    assert client.patch(href, headers=keys["author"], json={"project": None}).status_code == 409
    assert client.patch(href, headers=keys["author"], json={"section": "Local metadata"}).status_code == 200
    with TestClient(create_app(replace(config, drive_read_only=True), store), base_url=MAIN) as paused:
        result = paused.patch(href, headers=keys["author"], json=data)
        assert result.status_code == 503 and result.json()["error"] == "drive_read_only"
    assert not cloud.calls and not patches
