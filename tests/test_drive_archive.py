"""Independent selected-file backup and offline restore through the real adapters."""
import io
import json
import sqlite3
import stat
from contextlib import closing
from dataclasses import replace
from urllib.parse import urlsplit
from zipfile import ZipFile

import pytest
from test_drive_storage import Response
from test_report_compaction_readback import model_document
from test_workspace_review_routes import closure, endpoint, env as report_env, round_body, version  # noqa: F401

from agent_artifacts.drive_archive import ArchiveError, export_archive, extract_version, main, plan_archive, verify_archive
from agent_artifacts.drive_destination import DriveDestination
from agent_artifacts.drive_storage import DriveStorage


def snapshot(store, path):
    with store._conn() as src, closing(sqlite3.connect(path)) as out:
        src.backup(out)
    return path


def close(env):
    client, _, _, keys, _, first, second = env
    for i, item in enumerate((first, second), 1):
        result = client.post(endpoint(first), headers=keys["author"], json=round_body(item, i))
        assert result.status_code == 200
    assert client.post(endpoint(first, "compact"), headers=keys["author"], json=closure(first, second)).status_code == 200


class ExportReads:
    def __init__(self, cloud):
        self.cloud = cloud
        self.docs_reads = 0
        self.drift = False
        self.invalid_docx = False
        self.calls = []

    def request(self, method, url, **kwargs):
        assert method == "GET" and not kwargs["allow_redirects"]
        path = urlsplit(url).path
        self.calls.append(path)
        if path == "/v1/documents/doc":
            self.docs_reads += 1
            doc = model_document(self.cloud.docs.compacted)
            doc.update(documentId="doc", title="Review report")
            if self.drift and self.docs_reads > 1:
                doc["revisionId"] = "different"
            return Response(data=doc)
        if path == "/drive/v3/files/doc/export":
            assert kwargs["params"]["mimeType"].endswith("wordprocessingml.document")
            if self.invalid_docx:
                return Response(content=b"not a document")
            output = io.BytesIO()
            with ZipFile(output, "w") as zipfile:
                zipfile.writestr("[Content_Types].xml", "<Types/>")
                zipfile.writestr("word/document.xml", "<body>Confirmed synthetic review</body>")
            return Response(content=output.getvalue())
        return self.cloud.request("author", method, url, **kwargs)


def setup_plan(env, tmp_path, *, report=False):
    _, store, cloud, _, config, first, _ = env
    expected = DriveDestination.from_config(config.drive, config.workspace, config.public_url)
    backup = snapshot(store, tmp_path / "copy.sqlite")
    plan = plan_archive(backup, expected, first["id"], include_report=report)
    transport = ExportReads(cloud)
    drive = DriveStorage(transport, expected.folder_id, folder_kind=expected.folder_kind,
                         max_bytes=1000, project_layout=True)
    return backup, plan, transport, drive


def test_archive_native_report_and_exact_old_bytes_recover_offline(report_env, tmp_path, capsys):
    close(report_env)
    first, second = report_env[-2:]
    backup, plan, transport, drive = setup_plan(report_env, tmp_path, report=True)
    folder = tmp_path / "private-export"
    manifest = export_archive(plan, drive, folder)
    assert len(manifest["versions"]) == 2 and manifest["report"]["document_id"] == "doc"
    assert transport.docs_reads == 2
    assert [path for path in transport.calls if "/revisions/" in path] == [
        f"/drive/v3/files/{plan.file_id}/revisions/{item.revision_id}" for item in plan.versions]
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in folder.iterdir())
    assert verify_archive(folder, backup=backup) == manifest
    before = transport.calls.copy()
    for item, expected in ((first, b"old bytes"), (second, b"new bytes")):
        dest = tmp_path / (version(item) + ".txt")
        args = ["extract-version", "--archive", str(folder), "--version", version(item), "--output", str(dest)]
        assert main(args) == 0 and dest.read_bytes() == expected and main(args) == 1
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    with ZipFile(folder / "report.docx") as document:
        assert b"Confirmed synthetic review" in document.read("word/document.xml")
    native = (folder / "report.json").read_text()
    assert first["version_url"] in native and second["version_url"] in native
    assert "Why: Preserves the list" in native and "Why not: Uses too much space" in native
    assert "Private draft" not in native and transport.calls == before
    assert version(first) not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["changing-report", "invalid-docx", "size"])
def test_failed_export_leaves_no_archive_or_inconsistent_report(report_env, tmp_path, failure):
    close(report_env)
    _, plan, transport, drive = setup_plan(report_env, tmp_path, report=True)
    if failure == "changing-report":
        transport.drift = True
    elif failure == "invalid-docx":
        transport.invalid_docx = True
    else:
        row = plan.versions[0]
        volumes = transport.cloud.drive.revisions[plan.file_id]
        volumes[int(row.revision_id) - 1] = b"different size"
    folder = tmp_path / "private-export"
    with pytest.raises(ArchiveError):
        export_archive(plan, drive, folder)
    assert not folder.exists()


def test_wrong_destination_or_pending_report_is_refused_before_google(report_env, tmp_path):
    client, store, cloud, keys, config, first, _ = report_env
    backup = snapshot(store, tmp_path / "copy.sqlite")
    destination = DriveDestination.from_config(config.drive, config.workspace, config.public_url)
    with pytest.raises(ArchiveError, match="destination"):
        plan_archive(backup, replace(destination, folder_kind="shared-drive"), first["id"])
    with pytest.raises(ArchiveError, match="closed"):
        plan_archive(backup, destination, first["id"], include_report=True)
    assert not cloud.calls
    cloud.lose_append = True
    assert client.post(endpoint(first), headers=keys["author"], json=round_body(first)).status_code == 503
    second = snapshot(store, tmp_path / "pending.sqlite")
    cloud.calls.clear()
    with pytest.raises(ArchiveError, match="pending"):
        plan_archive(second, destination, first["id"])
    assert not cloud.calls


def test_verify_refuses_wrong_copy_changed_bytes_and_unsafe_paths(report_env, tmp_path):
    backup, plan, _, drive = setup_plan(report_env, tmp_path)
    folder = tmp_path / "private-export"
    manifest = export_archive(plan, drive, folder)
    damaged = tmp_path / "different.sqlite"
    damaged.write_bytes(backup.read_bytes())
    with closing(sqlite3.connect(damaged)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE projects SET title='Different copy'")
        db.commit()
    with pytest.raises(ArchiveError):
        verify_archive(folder, backup=damaged)
    row = manifest["versions"][0]
    payload = folder / row["file"]
    content = payload.read_bytes()
    payload.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises(ArchiveError, match="digest"):
        extract_version(folder, row["id"], tmp_path / "will-not-write")
    assert not (tmp_path / "will-not-write").exists()
    payload.write_bytes(content)
    rogue = manifest | {"versions": [row | {"file": "../copy.sqlite"}, *manifest["versions"][1:]]}
    doc = folder / "manifest.json"
    doc.write_text(json.dumps(rogue))
    with pytest.raises(ArchiveError, match="file name"):
        verify_archive(folder)
    doc.write_text(json.dumps(manifest))
    target = tmp_path / "external"
    target.write_bytes(content)
    target.chmod(0o600)
    payload.unlink()
    payload.symlink_to(target)
    with pytest.raises(ArchiveError):
        verify_archive(folder)
    assert target.read_bytes() == content
