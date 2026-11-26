"""Offline boundary tests for the later Drive slice on top of the cabinet."""

from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient
from test_account_trash import claim, put
from test_drive_store import FakeDrive
from test_drive_web import upload
from test_workspace_web import MAIN, FakeGoogle, sign_in

from agent_artifacts import store as store_module
from agent_artifacts.config import Config, WorkspaceConfig
from agent_artifacts.store import Store
from agent_artifacts.web import create_app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    now = [100.0]
    store = Store(tmp_path, max_total_bytes=10, ttl_seconds=10, clock=lambda: now[0])
    agent = {"Authorization": f"Bearer {store.add_agent('owner')}"}
    drive = FakeDrive()
    config = Config(tmp_path, MAIN, workspace=WorkspaceConfig("client", "fake", "company.example"), accounts=True)
    with TestClient(create_app(config, store, drive=drive), base_url=MAIN) as browser:
        sign_in(browser, MAIN, "/account")
        code = browser.post("/api/v1/me/account-claim", headers=agent).json()["code"]
        claim_response = browser.post("/account/claim", headers={"Origin": MAIN},
                                      data={"code": code, "confirm": "1"})
        assert claim_response.status_code == 200 and "Your account" in claim_response.text
        yield browser, store, drive, agent, now


def test_disk_undo_keeps_quota_for_new_drive_uploads(workspace):
    browser, store, drive, agent, now = workspace
    old = put(store, size=9)
    deleted = browser.post("/account/delete", headers={"Origin": MAIN},
                           data={"kind": "file", "target": old.id})
    assert deleted.status_code == 200 and deleted.url.fragment.startswith("account-undo-")
    now[0] += 15  # after the file's old TTL but before the Undo deadline
    assert browser.get(f"/a/{old.id}/download").status_code == 404
    failed = upload(browser, agent, "new.txt", b"two")
    assert failed.status_code == 507 and drive.generated == 0 and store.storage.exists(old.id)
    now[0] += 60
    assert store.purge_expired() == 1 and not store.storage.exists(old.id)
    assert upload(browser, agent, "new.txt", b"two").status_code == 201


def test_cabinet_does_not_delete_drive_without_archive_policy(workspace):
    browser, store, drive, agent, now = workspace
    project = store.add_project("owner", "Project")
    made = upload(browser, agent, "review.txt", b"exact", project=project.id).json()
    version = urlsplit(made["version_url"]).path
    for kind, target in (("file", made["id"]), ("project", project.id)):
        response = browser.post("/account/delete", headers={"Origin": MAIN},
                                data={"kind": kind, "target": target})
        assert response.status_code == 409 and "Drive files cannot be deleted here yet." in response.text
    now[0] += 70
    assert store.purge_expired() == 0 and len(drive.files) == 1
    assert browser.get(version + "/download").content == b"exact"


def test_provider_routes_respect_hidden_scope_even_for_revision_links(workspace):
    browser, store, _, agent, now = workspace
    project = store.add_project("owner", "Project")
    made = upload(browser, agent, "review.txt", b"exact", project=project.id).json()
    version = urlsplit(made["version_url"]).path
    account = store.accounts.google_account("google-sub")
    deletion = "U" * 22
    # Defensive test of the provider lookup: the cabinet normally refuses to
    # create this marker until archive-aware closure exists.
    with store._conn(write=True) as db:
        db.execute("INSERT INTO account_deletions VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                   (deletion, account, project.id, project.title, 1, now[0], now[0] + 60))
    for path in (urlsplit(made["url"]).path, version):
        for suffix in ("", "/raw", "/download"):
            assert browser.get(path + suffix).status_code == 404
    assert upload(browser, agent, "replace.txt", b"no", made["id"]).status_code == 404
    undone = browser.post("/account/undo", headers={"Origin": MAIN}, data={"deletion": deletion})
    assert undone.status_code == 200 and browser.get(version + "/download").content == b"exact"


def test_upgrade_keeps_a_pending_cabinet_deletion(tmp_path, monkeypatch):
    migrations = store_module._MIGRATIONS
    with monkeypatch.context() as account_only:
        account_only.setattr(store_module, "_MIGRATIONS", migrations[:8])
        old = Store(tmp_path, max_total_bytes=30, clock=lambda: 100)
        _, account, owner = claim(old, "owner")
        artifact = put(old)
        with old._conn(write=True) as db:
            db.execute("INSERT INTO account_deletions VALUES (?, ?, ?, NULL, ?, 1, 100, 160)",
                       ("D" * 22, account, artifact.id, artifact.title))
    upgraded = Store(tmp_path, max_total_bytes=30, clock=lambda: 120)
    assert upgraded.get_with_drive(artifact.id) is None and upgraded.purge_expired() == 0
    assert upgraded.trash.undo("D" * 22, **owner) == "restored"
    restored, reference = upgraded.get_with_drive(artifact.id)
    assert restored.id == artifact.id and reference is None
    with upgraded.storage.open(artifact.id) as content:
        assert content.read() == b"xxxxx"
