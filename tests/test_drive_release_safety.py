"""Destination, maintenance mode and snapshot inventory; no live Google calls."""
import json
import sqlite3
import stat
from contextlib import closing
from dataclasses import replace
from functools import partial
from io import BytesIO
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient
from test_drive_web import upload
from test_workspace_drive import POLICY, ROOT
from test_workspace_review_routes import endpoint, env as report_env, round_body  # noqa: F401
from test_workspace_web import MAIN

from agent_artifacts.config import Config, DriveFolderConfig
from agent_artifacts.drive_destination import DriveDestination, DriveDestinationError, DriveDestinationStore
from agent_artifacts.drive_recovery import InventoryError, inventory, main, save_inventory
from agent_artifacts.google_oidc import GoogleIdentity
from agent_artifacts.store import Store
from agent_artifacts.web import create_app
from agent_artifacts.workspace_auth import SESSION_COOKIE, WorkspaceAuthStore
from agent_artifacts.workspace_drive import WorkspaceDrive


def target(kind="my-drive", folder=ROOT):
    return DriveDestination.from_config(DriveFolderConfig(folder, kind), POLICY, MAIN)


def test_until_pin_settings_may_change_and_a_stale_provider_cannot_open(tmp_path):
    store = Store(tmp_path, max_total_bytes=1000)
    bindings = DriveDestinationStore(store._conn)
    mine, other = target(), target(folder="another-root-folder")
    assert not bindings.check(mine) and not bindings.check(other)
    assert not bindings.check(None)

    class NeverRead:
        def get(self, subject):
            raise AssertionError("must fail before looking up a grant")

    stale = WorkspaceDrive(POLICY, NeverRead(), folder_id=other.folder_id, folder_kind=other.folder_kind,
                           max_bytes=1000, check_destination=partial(bindings.check, other))
    bindings.pin(mine)
    bindings.pin(mine)  # identical settings remain idempotent
    assert bindings.check(mine)
    with pytest.raises(DriveDestinationError):
        bindings.pin(other)
    with pytest.raises(DriveDestinationError), stale.open("reader"):
        pytest.fail("outdated provider opened")


def test_unbound_ambiguous_folder_or_upload_cannot_adopt_env_values(tmp_path):
    store = Store(tmp_path, max_total_bytes=1000)
    bindings = DriveDestinationStore(store._conn)
    with store._conn(write=True) as db:
        db.execute("INSERT INTO drive_folders VALUES ('unfiled', 'different-root', 'reserved-folder', 'name', 'name', 0)")
    with pytest.raises(DriveDestinationError, match="unbound Drive data"):
        bindings.pin(target())
    with pytest.raises(DriveDestinationError):
        create_app(Config(tmp_path, MAIN, workspace=POLICY, accounts=True, drive=DriveFolderConfig(ROOT)), store)
    with store._conn(write=True) as db:
        db.execute("DELETE FROM drive_folders")
        db.execute("INSERT INTO drive_attempts VALUES ('op', 'artifact', 'remote', NULL, NULL, 10)")
    with pytest.raises(DriveDestinationError, match="unbound Drive data"):
        bindings.check(target())
    with store._conn() as db:
        assert db.execute("SELECT count(*) FROM drive_destination").fetchone() == (0,)


def test_config_cannot_disable_or_redirect_existing_exact_versions(report_env):
    _, store, cloud, _, config, _, _ = report_env
    assert not cloud.calls
    dest = DriveDestinationStore(store._conn)
    assert dest.check(target())
    changed = (
        replace(config, drive=None, review_docs=False),
        replace(config, drive=DriveFolderConfig(ROOT, "shared-drive")),
        replace(config, drive=DriveFolderConfig("another-approved-folder", "my-drive")),
        replace(config, public_url="https://moved.example"),
        replace(config, workspace=replace(POLICY, client_id="another-app")),
        replace(config, workspace=replace(POLICY, hosted_domain="other.example")),
    )
    for candidate in changed:
        with pytest.raises(DriveDestinationError, match="destination does not match"):
            create_app(candidate, store)
    create_app(replace(config, workspace=replace(POLICY, client_secret="rotated-secret")), store)
    assert not cloud.calls


def test_pause_preserves_exact_reads_and_legacy_disk_without_fallback(report_env):
    _, store, cloud, keys, config, first, second = report_env
    old_disk = store.put(BytesIO(b"disk"), owner="author", name="disk.txt", kind="txt", size=4)
    paused = replace(config, drive_read_only=True)
    with TestClient(create_app(paused, store), base_url=MAIN) as browser:
        for response in (
            upload(browser, keys["author"], "blocked.txt", b"never fall back to disk"),
            upload(browser, keys["author"], "blocked.txt", b"never replace", first["id"]),
            browser.post(endpoint(first), headers=keys["author"], json=round_body(first)),
            browser.post(endpoint(first, "compact"), headers=keys["author"], json={}),
        ):
            assert response.status_code == 503 and response.json()["error"] == "drive_read_only"
        assert not cloud.calls
        replaced = upload(browser, keys["author"], "disk.txt", b"still disk", old_disk.id)
        assert replaced.status_code == 200 and store.get_with_drive(old_disk.id)[1] is None
        assert not cloud.calls
        auth = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", POLICY)
        cookie = auth.create_session(MAIN, GoogleIdentity("author", "author@company.example", "Author"))
        browser.cookies.set(SESSION_COOKIE, cookie, domain=urlsplit(MAIN).netloc, path="/")
        for obj, expected in ((first, b"old bytes"), (second, b"new bytes")):
            response = browser.get(urlsplit(obj["version_url"]).path + "/download")
            assert response.status_code == 200 and response.content == expected
        assert all(method == "GET" or path == "/token" for _, method, path in cloud.calls)


def test_booted_disk_only_workspace_worker_does_not_start_new_disk_upload_after_pin(tmp_path):
    store = Store(tmp_path, max_total_bytes=1000)
    auth = "Bearer " + store.add_agent("agent")
    config = Config(tmp_path, MAIN, workspace=POLICY, accounts=True)
    with TestClient(create_app(config, store), base_url=MAIN) as old:
        DriveDestinationStore(store._conn).pin(target())
        attempted = upload(old, {"Authorization": auth}, "blocked.txt", b"no mixed destination")
        assert attempted.status_code == 503 and not store.list("agent")


def test_prepared_backup_inventory_excludes_payloads_and_keeps_unconfirmed_writes(report_env, tmp_path, capsys):
    client, store, cloud, keys, _, first, _ = report_env
    cloud.lose_append = True
    assert client.post(endpoint(first), headers=keys["author"], json=round_body(first)).status_code == 503
    with store._conn(write=True) as db:
        db.execute("INSERT INTO drive_attempts VALUES ('media-write', ?, 'backup-file', 'old', 'new', 10)",
                   (first["id"],))
    backup = tmp_path / "metadata-copy.sqlite"
    with store._conn() as source, closing(sqlite3.connect(backup)) as copy:
        source.backup(copy)
    frozen = backup.read_bytes()
    output = tmp_path / "private-inventory.json"
    args = ["--backup", str(backup), "--output", str(output)]
    assert main(args) == 0 and backup.read_bytes() == frozen
    saved = output.read_text()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    data = json.loads(saved)
    assert data["destination"]["folder_id"] == ROOT and not data["remote_bytes_verified"]
    assert len(data["versions"]) == 2 and len(data["reports"]) == 1
    assert data["media_attempts"][0]["returned_revision"] == "new"
    assert data["report_attempts"] == [{"artifact_id": first["id"], "operation_key": "round1", "kind": "round", "sent": 1}]
    for forbidden in ("Private draft", "Thread evidence", "Recorded reason", "Review this version", "synthetic-client-secret"):
        assert forbidden not in saved
    assert ROOT not in capsys.readouterr().out
    assert main(args) == 1 and output.read_text() == saved


def test_inventory_refuses_sidecar_wrong_schema_and_symlink_output(tmp_path):
    raw = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(raw)) as db:
        db.execute("PRAGMA user_version=12")
    with pytest.raises(InventoryError, match="schema"):
        inventory(raw)
    sidecar = tmp_path / "old.sqlite-wal"
    sidecar.touch()
    with pytest.raises(InventoryError, match="sidecars"):
        inventory(raw)
    link = tmp_path / "target.json"
    link.symlink_to(raw)
    before = raw.read_bytes()
    with pytest.raises(InventoryError, match="new private"):
        save_inventory(link, {})
    assert link.is_symlink() and raw.read_bytes() == before
