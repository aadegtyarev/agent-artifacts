import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from starlette.testclient import TestClient

from agent_artifacts.config import Config
from agent_artifacts.store import (
    DRAFT_TTL_SECONDS, DeletionPending, NoSuchProject, QuotaExceeded, StorageFull, Store, hash_key,
)
from agent_artifacts.trash import UNDO_SECONDS
from agent_artifacts.web import create_app


def claim(store, name):
    key = store.add_agent(name)
    result = store.accounts.claim_agent(store.accounts.issue_claim(hash_key(key)))
    return key, result.account_id, {"device_token": result.device_token}


def put(store, agent="owner", name="sample.md", project=None, size=5):
    return store.put(BytesIO(b"x" * size), owner=agent, name=name, kind="md", size=size, project=project)


def test_standalone_file_stays_on_disk_in_storage_quota_and_can_be_undone(tmp_path):
    now = [1000.0]
    store = Store(tmp_path, max_total_bytes=5, user_quota_bytes=5, ttl_seconds=12, clock=lambda: now[0])
    _, account, owner = claim(store, "owner")
    _, outsider_id, outsider = claim(store, "outsider")
    file = put(store)
    assert store.trash.delete("file", file.id, **outsider).status == "missing"
    now[0] += 3
    assert store.trash.delete("file", file.id, **owner).status == "deleted"
    [entry] = store.trash.pending(account)
    assert entry.can_undo and entry.deadline == now[0] + UNDO_SECONDS
    assert store.trash.pending(outsider_id) == []
    assert store.trash.undo(entry.id, **outsider) == "missing"
    assert store.get(file.id) is None and store.list("owner") == []
    assert store.comments(file.id) is None and store.drafts(file.id, "key") is None
    assert store.delete(file.id, owner="owner") is False
    assert store.edit(file.id, owner="owner", changes={"title": "Do not edit"}) is None
    assert store.put(BytesIO(b"y"), owner="owner", name="replaced.md", kind="md", size=1,
                     artifact_id=file.id) is None
    now[0] += 15  # the original file expiry would have passed
    assert store.purge_expired() == 0 and store.storage.exists(file.id)
    assert store.agent_info("owner").used_bytes == file.size
    with pytest.raises(QuotaExceeded):
        put(store, size=1)
    with pytest.raises(StorageFull):
        put(store, agent="outsider", size=1)
    assert store.trash.undo(entry.id, **owner) == "restored"
    restored = store.get(file.id)
    assert restored.id == file.id and restored.title == file.title
    assert restored.expires_at - now[0] == 9
    with store.storage.open(file.id) as data:
        assert data.read() == b"xxxxx"
    assert store.trash.undo(entry.id, **owner) == "missing"


def test_project_confirmation_exact_scope_and_distinct_file_undo(tmp_path):
    now = [1000.0]
    store = Store(tmp_path, max_total_bytes=100_000, clock=lambda: now[0])
    _, account, owner = claim(store, "owner")
    project = store.add_project("owner", "My project")
    prior = put(store, name="one.md", project=project.id)
    shared = put(store, name="two.md", project=project.id)
    confirmation = store.trash.delete("project", project.id, **owner)
    assert confirmation.status == "confirm" and confirmation.scope.count == 2
    assert store.trash.pending(account) == []
    assert store.trash.delete("file", prior.id, **owner).status == "deleted"
    [prior_entry] = store.trash.pending(account)
    with pytest.raises(DeletionPending):
        store.delete_project(project.id, owner="owner")
    added = put(store, name="new.md", project=project.id)
    changed = store.trash.delete("project", project.id, fingerprint=confirmation.scope.fingerprint, **owner)
    assert changed.status == "changed" and changed.scope.count == 2  # different file set, same size
    assert store.public_project(project.id)[0].files == 2
    assert store.trash.delete("project", project.id, fingerprint=changed.scope.fingerprint, **owner).status == "deleted"
    entries = {entry.kind: entry for entry in store.trash.pending(account)}
    assert entries["file"].id == prior_entry.id and not entries["file"].can_undo
    assert store.trash.undo(prior_entry.id, **owner) == "project_hidden"
    assert store.get(shared.id) is None and store.get(added.id) is None
    assert store.public_project(project.id) is None and store.projects("owner") == []
    assert store.project_summary(project.id, "key") is None and store.agent_summary(project.id, owner="owner", since=None) is None
    assert store.rename_project(project.id, owner="owner", title="Do not rename") is None
    assert store.delete_project(project.id, owner="owner") is False
    with pytest.raises(NoSuchProject):
        put(store, project=project.id)
    assert store.trash.undo(entries["project"].id, **owner) == "restored"
    assert {file.id for file in store.public_project(project.id)[1]} == {shared.id, added.id}
    assert store.get(prior.id) is None
    assert store.trash.undo(prior_entry.id, **owner) == "restored"
    assert store.public_project(project.id)[0].files == 3


def test_project_undo_preserves_comments_and_pauses_live_draft_lifetime(tmp_path):
    now = [1000.0]
    store = Store(tmp_path, max_total_bytes=100_000, clock=lambda: now[0])
    _, account, owner = claim(store, "owner")
    project = store.add_project("owner", "My project")
    artifact = put(store, project=project.id)
    comment = store.agent_comment(artifact.id, "Keep this note", None, owner="owner", validate_anchor=lambda *_: None)
    draft = store.add_draft(artifact.id, "key", author="Reviewer", text="Keep this draft", anchor=None)
    now[0] += DRAFT_TTL_SECONDS - 5
    stamp = store.trash.delete("project", project.id, **owner).scope.fingerprint
    assert store.trash.delete("project", project.id, fingerprint=stamp, **owner).status == "deleted"
    [entry] = store.trash.pending(account)
    now[0] += 20
    assert store.purge_expired() == 0
    assert store.comments(artifact.id) is None and store.drafts(artifact.id, "key") is None
    assert not store.delete_comment(artifact.id, comment.n, owner="owner")
    assert not store.delete_draft(artifact.id, "key", draft.id)
    assert store.trash.undo(entry.id, **owner) == "restored"
    assert store.comments(artifact.id)[1] == [comment]
    [restored] = store.drafts(artifact.id, "key")
    assert restored.id == draft.id and restored.text == draft.text and restored.created_at == draft.created_at
    assert restored.updated_at - draft.updated_at == 20
    now[0] += 6
    assert store.drafts(artifact.id, "key") == []


def test_cleanup_wins_at_undo_deadline_and_removes_metadata_and_bytes(tmp_path):
    now = [1000.0]
    store = Store(tmp_path, max_total_bytes=100_000, clock=lambda: now[0])
    _, account, owner = claim(store, "owner")
    project = store.add_project("owner", "My project")
    a, b = put(store, project=project.id), put(store, project=project.id)
    stamp = store.trash.delete("project", project.id, **owner).scope.fingerprint
    store.trash.delete("project", project.id, fingerprint=stamp, **owner)
    [entry] = store.trash.pending(account)
    now[0] = entry.deadline
    with ThreadPoolExecutor(max_workers=2) as pool:
        undo = pool.submit(store.trash.undo, entry.id, **owner)
        purge = pool.submit(store.purge_expired)
        assert undo.result() == "missing" and purge.result() == 2
    assert store.trash.pending(account) == [] and store.public_project(project.id) is None
    assert not store.storage.exists(a.id) and not store.storage.exists(b.id)
    with store._conn() as db:
        assert db.execute("SELECT count(*) FROM projects WHERE id = ?", (project.id,)).fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM account_deletions").fetchone() == (0,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_undo_toast_does_not_get_a_new_deadline_on_reload(tmp_path):
    now = [1000.0]
    base = "https://app.example"
    store = Store(tmp_path, max_total_bytes=100_000, clock=lambda: now[0])
    _, _, owner = claim(store, "owner")
    artifact = put(store)
    app = create_app(Config(tmp_path, base, accounts=True), store)
    with TestClient(app, base_url=base) as browser:
        browser.cookies.set("__Host-aa-account-device", owner["device_token"], domain="app.example", path="/")
        undo_page = browser.post("/account/delete", headers={"Origin": base}, data={"kind": "file", "target": artifact.id})
        assert 'class="toast toast-top on"' in undo_page.text and 'data-undo-ms="60000"' in undo_page.text
        deletion = re.search(r'name="deletion" value="([\w-]+)"', undo_page.text)[1]
        assert undo_page.url.fragment == f"account-undo-{deletion}"
        now[0] += 25
        assert 'data-undo-ms="35000"' in browser.get("/account").text
        now[0] += 35
        assert 'id="account-deletions"' not in browser.get("/account").text
        response = browser.post("/account/undo", headers={"Origin": base}, data={"deletion": deletion})
        assert response.status_code == 404 and store.get(artifact.id) is None


def test_account_ui_and_old_public_routes_observe_the_same_deletion(tmp_path):
    base, raw = "https://app.example", "https://raw.example"
    store = Store(tmp_path, max_total_bytes=100_000)
    key = store.add_agent("owner")
    headers = {"Authorization": f"Bearer {key}"}
    origin = {"Origin": base}
    project = store.add_project("owner", "Project with upload")
    linked = put(store, project=project.id)
    standalone = put(store, name="standalone.md")
    app = create_app(Config(tmp_path, base, raw_url=raw, accounts=True), store)
    with TestClient(app, base_url=base) as browser, TestClient(app, base_url=base) as public:
        code = browser.post("/api/v1/me/account-claim", headers=headers).json()["code"]
        browser.post("/account/claim", headers=origin, data={"code": code, "confirm": "1"})
        params = {"kind": "file", "target": standalone.id}
        assert browser.post("/account/delete", data=params).status_code == 403
        assert public.get(f"/a/{standalone.id}").status_code == 200
        assert public.get(f"{raw}/a/{standalone.id}/raw").status_code == 200
        removed = browser.post("/account/delete", headers=origin, data=params)
        deletion = re.search(r'name="deletion" value="([\w-]+)"', removed.text)[1]
        assert removed.url.fragment == f"account-undo-{deletion}"
        assert "Deleted 1 file · standalone.md" in removed.text
        for suffix in ("", "/raw", "/download", "/comments", "/drafts"):
            assert public.get(f"/a/{standalone.id}{suffix}", headers={"X-Author-Key": "v" * 22}).status_code == 404
        assert public.get(f"{raw}/a/{standalone.id}/raw").status_code == 404
        assert public.delete(f"/api/v1/artifacts/{standalone.id}", headers=headers).status_code == 404
        assert standalone.id not in {a["id"] for a in public.get("/api/v1/artifacts", headers=headers).json()}
        with TestClient(create_app(Config(tmp_path, base, accounts=False), store), base_url=base) as off:
            assert off.get(f"/a/{standalone.id}").status_code == 404
        restored = browser.post("/account/undo", headers=origin, data={"deletion": deletion})
        assert "Restored." in restored.text and public.get(f"/a/{standalone.id}").status_code == 200
        assert browser.post("/account/undo", headers=origin, data={"deletion": deletion}).status_code == 404
        params = {"kind": "project", "target": project.id}
        preview = browser.post("/account/delete", headers=origin, data=params)
        fingerprint = re.search(r'name="scope" value="([a-f0-9]{64})"', preview.text)[1]
        assert "1 file" in preview.text and public.get(f"/p/{project.id}").status_code == 200
        put(store, name="new.md", project=project.id)
        changed = browser.post("/account/delete", headers=origin, data=params | {"scope": fingerprint})
        assert changed.status_code == 409 and "2 files" in changed.text
        safe = re.search(r'name="scope" value="([a-f0-9]{64})"', changed.text)[1]
        hidden = browser.post("/account/delete", headers=origin, data=params | {"scope": safe})
        assert "Deleted 1 project, 2 files · Project with upload" in hidden.text
        deletion = re.search(r'name="deletion" value="([\w-]+)"', hidden.text)[1]
        assert public.get(f"/p/{project.id}").status_code == 404 and public.get(f"/a/{linked.id}").status_code == 404
        assert public.get(f"/api/v1/projects/{project.id}", headers=headers).status_code == 404
        browser.post("/account/undo", headers=origin, data={"deletion": deletion})
        assert public.get(f"/p/{project.id}").status_code == 200 and public.get(f"/a/{linked.id}").status_code == 200
