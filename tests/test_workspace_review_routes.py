"""Owner reports through the running app, with persistent SQLite and simulated Google."""
import json
import runpy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from io import BytesIO
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

import pytest
import requests
from starlette.testclient import TestClient
from test_drive_layout import LayoutDrive
from test_drive_web import upload
from test_report_compaction_dispatch import AtomicDocs
from test_report_compaction_readback import model_document, table
from test_workspace_drive import ACCESS, POLICY, REFRESH, ROOT, reply
from test_workspace_web import MAIN, page_marker

from agent_artifacts.config import Config, DriveFolderConfig
from agent_artifacts.drive_grants import WorkspaceDriveGrants
from agent_artifacts.drive_storage import DriveStorage
from agent_artifacts.google_oidc import DRIVE_SCOPE, GoogleIdentity
from agent_artifacts.review_report_routes import report_routes
from agent_artifacts.store import Store
from agent_artifacts.web import create_app
from agent_artifacts.workspace_auth import SESSION_COOKIE, WorkspaceAuthStore

DOC_MIME = "application/vnd.google-apps.document"


class Cloud:
    def __init__(self):
        self.drive = LayoutDrive("my-drive", root=ROOT)
        self.docs = AtomicDocs()
        self.metadata = None
        self.creates = 0
        self.appends = 0
        self.lose_create = False
        self.lose_append = False
        self.hide_lookup = False
        self.mangle_link = False
        self.create_gate = None
        self.calls = []

    def request(self, actor, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append((actor, method, path))
        assert actor == "author"
        if path == "/drive/v3/files" and method == "POST" and kwargs["json"]["mimeType"] == DOC_MIME:
            self.creates += 1
            assert self.creates == 1
            document = kwargs["json"]
            parent, = document["parents"]
            assert parent != ROOT and self.drive.entries[parent]["parents"] == [ROOT]
            self.metadata = document | {"id": "doc", "trashed": False, "capabilities": {"canEdit": True}}
            if self.create_gate is not None:
                self.create_gate()
            if self.lose_create:
                raise requests.Timeout("private create error")
            return reply(200, {"id": "doc"})
        if path == "/drive/v3/files" and method == "GET":
            assert self.metadata is not None
            query = kwargs["params"]["q"]
            assert f"'{self.metadata['parents'][0]}' in parents" in query
            assert self.metadata["appProperties"]["agentArtifactsReport"] in query
            assert kwargs["params"]["corpora"] == "user"
            return reply(200, {"files": [] if self.hide_lookup else [{"id": "doc"}]})
        if path == "/drive/v3/files/doc":
            assert method == "GET"
            return reply(200, self.metadata)
        if path.startswith("/v1/documents/doc"):
            if path.endswith(":batchUpdate") and not any("insertTable" in r for r in kwargs["json"]["requests"]):
                self.appends += 1
                result = self.docs.request(method, url, **kwargs)
                if self.mangle_link:
                    a, b, href = self.docs.link_spans[-1]
                    self.docs.link_spans[-1] = (a, b, href + "-not-the-exact-version")
                if self.lose_append:
                    self.lose_append = False
                    raise requests.Timeout("private append error")
                return result
            return self.docs.request(method, url, **kwargs)
        return self.drive.request(method, url, **kwargs)


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    # Replay the old scratch protocol only in suites that test that behavior.
    if getattr(request, "param", True):
        monkeypatch.setattr("agent_artifacts.web.report_routes", partial(report_routes, include_legacy_probe_routes=True))
    cloud = Cloud()
    store = Store(tmp_path, max_total_bytes=100_000)
    grants = WorkspaceDriveGrants(tmp_path, POLICY)
    keys = {}
    for actor in ("author", "viewer"):
        store.accounts.google_account(actor)
        keys[actor] = {"Authorization": "Bearer " + store.accounts.issue_agent_key(actor, google_subject=actor)}
        grants.put(actor, REFRESH[actor])

    def exchange(url, *, data, **kwargs):
        actor, = [actor for actor, token in REFRESH.items() if token == data["refresh_token"]]
        cloud.calls.append((actor, "POST", "/token"))
        return reply(200, {"access_token": ACCESS[actor], "token_type": "Bearer", "scope": DRIVE_SCOPE})

    class Session:
        def __init__(self, credentials):
            self.actor, = [actor for actor, token in ACCESS.items() if token == credentials.token]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def request(self, method, url, **kwargs):
            return cloud.request(self.actor, method, url, **kwargs)

    monkeypatch.setattr("agent_artifacts.workspace_drive.requests.post", exchange)
    monkeypatch.setattr("agent_artifacts.workspace_drive.DriveAuthorizedSession", Session)
    config = Config(tmp_path, MAIN, workspace=POLICY, accounts=True,
                    drive=DriveFolderConfig(ROOT, "my-drive"), review_docs=True)
    with TestClient(create_app(config, store), base_url=MAIN) as client:
        project = client.post("/api/v1/projects", headers=keys["author"], json={"title": "Project"}).json()
        first = upload(client, keys["author"], "review.txt", b"old bytes", project=project["id"]).json()
        second = upload(client, keys["author"], "review.txt", b"new bytes", first["id"]).json()
        # This is the same operation used by Finish review. It only publishes the comments.
        assert store.publish(first["id"], "reader", author="Reader", text="Thread evidence")[0].n == 1
        store.add_draft(first["id"], "reader", author="Reader", text="Private draft", anchor=None)
        cloud.calls.clear()
        yield client, store, cloud, keys, config, first, second


def version(item):
    return urlsplit(item["version_url"]).path.rsplit("/", 1)[1]


def endpoint(item, kind="rounds"):
    return "/api/v1/artifacts/" + item["id"] + "/review-report/" + kind


def round_body(item, number=1, **changes):
    return {"key": "round" + str(number), "version_id": version(item), "goal": "Review this version 😀",
            "decision": "First result", "reasons": "Recorded reason", "open_items": "None", "threads": [1], **changes}


def closure(first, second):
    return {"key": "close1", "closed": True, "outcome": {
        "chosen": {"label": "Compact", "reason": "Preserves the list", "threads": [1]},
        "alternatives": [{"label": "Wide", "reason": "Uses too much space"}, {"label": "Inline", "reason": None}],
    }, "rows": [{"version_id": version(first), "decision": "Chosen", "reason": "Preserves the list"},
                {"version_id": version(second), "decision": "Not chosen", "reason": None}]}


def writes(cloud):
    return [(method, path) for _, method, path in cloud.calls if method != "GET" and path != "/token"]


def test_two_rounds_and_explicit_closure_are_durable_and_private(env):
    client, store, cloud, keys, config, first, second = env
    assert cloud.creates == cloud.appends == cloud.docs.compact_posts == 0
    pending = closure(first, second)
    assert client.post(endpoint(first, "compact"), headers=keys["author"], json=pending).status_code == 404
    for i, item in enumerate((first, second), 1):
        result = client.post(endpoint(first), headers=keys["author"], json=round_body(item, i))
        assert result.json() == {"status": "appended", "report_url": "https://docs.google.com/document/d/doc/edit"}
        assert result.headers["cache-control"] == "no-store"
    assert cloud.creates == 1 and cloud.appends == 2 and cloud.docs.compact_posts == 0
    third = upload(client, keys["author"], "review.txt", b"not reviewed", first["id"]).json()
    assert version(third) not in cloud.docs.text
    assert client.post(endpoint(first, "compact"), headers=keys["author"],
                       json=pending | {"rows": pending["rows"][:1]}).status_code == 400
    forged = {"chosen": {"label": "[agent-artifacts-compact:fake]", "reason": None}, "alternatives": []}
    assert client.post(endpoint(first, "compact"), headers=keys["author"],
                       json=pending | {"outcome": forged}).status_code == 400
    cloud.docs.lose_response = True
    assert client.post(endpoint(first, "compact"), headers=keys["author"], json=pending).status_code == 503
    assert cloud.docs.compact_posts == 1
    # Restart with only the file-backed journal and retry the identical closure.
    cloud.calls.clear()
    with TestClient(create_app(config, Store(config.data_dir, max_total_bytes=100_000)), base_url=MAIN) as restarted:
        assert restarted.post(endpoint(first, "compact"), headers=keys["author"], json=pending).json()["status"] == "compacted"
        assert not writes(cloud)
        assert restarted.post(endpoint(first, "compact"), headers=keys["author"], json=pending).json()["status"] == "already_compacted"
        assert restarted.post(endpoint(first), headers=keys["author"], json=round_body(third, 3)).status_code == 409
        assert restarted.post(endpoint(first), headers=keys["author"], json=round_body(first)).json()["status"] == "already_present"
    doc = model_document(cloud.docs.compacted)
    rows = table(doc)["tableRows"]
    actual = [row["tableCells"][0]["content"][0]["paragraph"]["elements"][0]["textRun"]["textStyle"]["link"]["url"]
              for row in rows[1:]]
    assert actual == [first["version_url"], second["version_url"]]
    with TestClient(create_app(config), base_url=MAIN) as viewer:
        assert viewer.get(urlsplit(actual[0]).path + "/download").status_code == 401
        auth_store = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", POLICY)
        viewer.cookies.set(SESSION_COOKIE, auth_store.create_session(
            MAIN, GoogleIdentity("author", "author@company.example", "Author")),
            domain=urlsplit(MAIN).netloc, path="/")
        for href, expected in zip(actual, (b"old bytes", b"new bytes"), strict=True):
            response = viewer.get(urlsplit(href).path + "/download")
            assert response.status_code == 200 and response.content == expected
    body = doc["tabs"][0]["documentTab"]["body"]["content"]
    text = "".join(run["textRun"]["content"] for block in body if "paragraph" in block for run in block["paragraph"]["elements"])
    assert "Chosen: Compact\nWhy: Preserves the list\nPublished threads: #1" in text
    assert "Why not: Uses too much space" in text and "Inline\n  Why not: Not recorded" in text
    assert "Private draft" not in text and "Thread evidence" not in text and version(third) not in text
    assert cloud.docs.compact_posts == 1
    with store._conn() as db:
        assert db.execute("SELECT count(*) FROM review_operations WHERE confirmed_at IS NOT NULL AND payload IS NULL").fetchone() == (3,)


def test_discussion_links_only_to_confirmed_summary_for_authorized_viewer(env, monkeypatch):
    client, store, cloud, keys, config, first, second = env
    path = urlsplit(first["url"]).path
    auth = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", POLICY)

    def sign_in(viewer, subject):
        viewer.cookies.set(SESSION_COOKIE, auth.create_session(
            MAIN, GoogleIdentity(subject, subject + "@company.example", "Reviewer")),
            domain=urlsplit(MAIN).netloc, path="/")

    assert client.get(path, follow_redirects=False).status_code == 303
    sign_in(client, "author")
    for i, item in enumerate((first, second), 1):
        assert client.post(endpoint(first), headers=keys["author"], json=round_body(item, i)).status_code == 200
    assert 'data-review-state="closable"' in client.get(path).text
    marker = {"X-Workspace-Session": page_marker(client, path)}
    assert client.get(path + "/comments", headers=marker).headers["X-Review-State"] == "closable"
    cloud.docs.lose_response = True
    body = closure(first, second)
    assert client.post(endpoint(first, "compact"), headers=keys["author"], json=body).status_code == 503
    assert client.get(path + "/comments", headers=marker).headers["X-Review-State"] == "pending"
    assert 'data-review-state="pending"' in client.get(path).text
    assert client.post(endpoint(first, "compact"), headers=keys["author"], json=body).status_code == 200
    result = client.get(path)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    discussion = result.text.partition('<aside id="comments"')[2].partition('</aside>')[0]
    assert 'data-review-state="ready:doc"' in discussion
    assert client.get(path + "/comments", headers=marker).headers["X-Review-State"] == "ready:doc"
    assert 'ready:doc' not in client.get(urlsplit(first["version_url"]).path + "/raw").text
    with TestClient(create_app(replace(config, review_docs=False), store), base_url=MAIN) as disabled:
        sign_in(disabled, "author")
        assert 'data-review-state=' not in disabled.get(path).text

    original = cloud.request

    def denied(actor, method, url, **kwargs):
        if actor == "viewer":
            return reply(403, {})
        return original(actor, method, url, **kwargs)

    monkeypatch.setattr(cloud, "request", denied)
    sign_in(client, "viewer")
    forbidden = client.get(path)
    assert forbidden.status_code == 403 and 'ready:doc' not in forbidden.text


@pytest.mark.parametrize("env", [False], indirect=True)
def test_agent_cli_saves_round_notes_and_outcome_from_captured_evidence(env, monkeypatch, tmp_path):
    from test_review_notes import NotesDocs

    client, _, cloud, keys, config, first, second = env
    cloud.docs = NotesDocs()
    path = urlsplit(first["url"]).path
    auth = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", POLICY)
    client.cookies.set(SESSION_COOKIE, auth.create_session(
        MAIN, GoogleIdentity("author", "author@company.example", "Author")),
        domain=urlsplit(MAIN).netloc, path="/")
    headers = {"Origin": MAIN, "X-Workspace-Session": page_marker(client, path)}
    for i, item in enumerate((first, second)):
        close = i == 1
        submitted = {"submission": {"id": ("b" if close else "a") * 22,
                                    "version": version(item), "close": close},
                     "text": "Compact preserves the list. Список."}
        assert client.post(path + "/review", headers=headers, json=submitted).status_code == 201
    script = Path(__file__).resolve().parents[1] / "skill/agent-artifacts/scripts/artifacts_cli.py"
    cli = runpy.run_path(str(script))
    monkeypatch.setenv("AGENT_ARTIFACTS_URL", MAIN)
    monkeypatch.setenv("AGENT_ARTIFACTS_KEY", keys["author"]["Authorization"].removeprefix("Bearer "))
    raw = tmp_path / "report.json"

    def send(c, method, path, body=None, ctype=""):
        assert c.url == MAIN
        if method == "POST":
            assert body == raw.read_bytes()
        response = client.request(method, path, content=body,
                                  headers={"Authorization": f"Bearer {c.key}", "Content-Type": ctype})
        assert response.status_code == 200
        return response.text

    monkeypatch.setattr(cli["Client"], "call", send)
    pending = json.loads(cli["run"](["review-notes-source", first["id"]]))
    assert pending["id"] == "a" * 22 and pending["version_url"] == first["version_url"]
    round_evidence = cli["run"](["review-notes-evidence", first["id"], pending["id"], "2"])
    assert json.loads(round_evidence)["text"] == "Compact preserves the list. Список."
    raw.write_text(json.dumps({"submission_id": pending["id"], "summary": "Keeps the list. Список.",
                              "threads": [2]}, ensure_ascii=False), encoding="utf-8")
    note = json.loads(cli["run"](["review-notes-update", first["id"], str(raw)]))
    assert note["status"] == "updated"
    source = json.loads(cli["run"](["review-source", first["id"]]))
    assert source["id"] == "b" * 22
    evidence = json.loads(cli["run"](["review-evidence", first["id"], source["id"], "2"]))
    assert evidence["text"] == "Compact preserves the list. Список."
    outcomes = closure(first, second)
    outcomes["outcome"]["chosen"]["threads"] = [2]
    raw.write_text(json.dumps({"closure_id": source["id"], "outcome": outcomes["outcome"], "rows": outcomes["rows"]},
                              ensure_ascii=False), encoding="utf-8")
    args = ["review-finalize", first["id"], str(raw)]
    assert json.loads(cli["run"](args)) == {"status": "finalized", "report_url": note["report_url"]}
    assert json.loads(cli["run"](args))["status"] == "already_finalized"
    assert (cloud.creates, cloud.appends, cloud.docs.compact_posts) == (1, 2, 0)
    raw.write_bytes(b" " * 16385)
    with pytest.raises(cli["Usage"], match="16384 bytes"):
        cli["run"](args)


def test_unknown_create_and_append_never_repeat_a_mutation(env):
    client, _, cloud, keys, _, first, _ = env
    url, body = endpoint(first), round_body(first)
    cloud.lose_create = cloud.hide_lookup = True
    assert client.post(url, headers=keys["author"], json=body).status_code == 503
    cloud.calls.clear()
    assert client.post(url, headers=keys["author"], json=body).json()["error"] == "report_pending"
    assert not writes(cloud) and cloud.creates == 1
    cloud.hide_lookup = False
    cloud.lose_append = True
    assert client.post(url, headers=keys["author"], json=body).status_code == 503
    cloud.calls.clear()
    assert client.post(url, headers=keys["author"], json=body | {"goal": "different"}).status_code == 409
    assert not cloud.calls
    assert client.post(url, headers=keys["author"], json=body).json()["status"] == "appended"
    assert not writes(cloud) and cloud.creates == cloud.appends == 1


def test_concurrent_requests_cannot_send_a_second_create_or_new_round(env):
    client, _, cloud, keys, _, first, second = env
    entered, release = Event(), Event()

    def gate():
        entered.set()
        assert release.wait(10), "test did not release the mock create"

    cloud.hide_lookup = True
    cloud.create_gate = gate
    path = endpoint(first)
    with ThreadPoolExecutor(1) as worker:
        creating = worker.submit(client.post, path, headers=keys["author"], json=round_body(first))
        try:
            assert entered.wait(10)
            duplicate = client.post(path, headers=keys["author"], json=round_body(first))
            other = client.post(path, headers=keys["author"], json=round_body(second, 2))
            assert duplicate.status_code == other.status_code == 409
            assert cloud.creates == 1 and cloud.appends == 0
        finally:
            release.set()
        assert creating.result(timeout=10).status_code == 200
    assert cloud.creates == cloud.appends == 1


def test_marker_is_not_enough_to_confirm_an_append(env):
    client, store, cloud, keys, _, first, _ = env
    cloud.mangle_link = True
    url, body = endpoint(first), round_body(first)
    assert client.post(url, headers=keys["author"], json=body).status_code == 409
    cloud.calls.clear()
    assert client.post(url, headers=keys["author"], json=body).status_code == 409
    assert not writes(cloud) and cloud.appends == 1
    with store._conn() as db:
        assert db.execute("SELECT sent,confirmed_at FROM review_operations").fetchone() == (1, None)


def test_invalid_or_cross_artifact_evidence_never_calls_google(env):
    client, _, cloud, keys, _, first, second = env
    url = endpoint(first)
    assert client.post(url, json=round_body(first)).status_code == 401
    assert client.post(url, headers=keys["viewer"], json=round_body(first)).status_code == 404
    assert client.post(url, headers=keys["author"], json=round_body(first, threads=[2])).status_code == 400
    assert client.post(url, headers=keys["author"], json=round_body(first, version_id="z" * 22)).status_code == 404
    close = endpoint(first, "compact")
    assert client.post(close, headers=keys["author"], json=closure(first, second) | {"closed": False}).status_code == 400
    duplicate = '{"key":"close1","closed":false,"closed":true,"outcome":{},"rows":[]}'
    assert client.post(close, headers=keys["author"] | {"Content-Type": "application/json"}, content=duplicate).status_code == 400
    assert not cloud.calls


def test_upgrade_from_v12_keeps_existing_exact_project_version(tmp_path):
    transport = LayoutDrive("my-drive", root=ROOT)
    drive = DriveStorage(transport, ROOT, max_bytes=1000, folder_kind="my-drive", project_layout=True)
    old = Store(tmp_path, max_total_bytes=1000)
    old.add_agent("owner")
    project = old.add_project("owner", "Existing project")
    old_artifact = old.put_drive(drive, BytesIO(b"v12 bytes"), owner="owner", name="file.txt",
                                 kind="txt", size=9, project=project.id)
    before = old.get_drive_version(old_artifact.id, old_artifact.version_id)
    with old._conn(write=True) as db:
        for name in ("review_submissions", "drive_project_renames", "drive_renames", "drive_destination",
                     "review_operations", "review_documents"):
            db.execute(f"DROP TABLE {name}")
        db.execute("PRAGMA user_version = 12")
    new = Store(tmp_path, max_total_bytes=1000)
    assert new.get_drive_version(old_artifact.id, old_artifact.version_id) == before
    with new._conn() as db:
        assert db.execute("PRAGMA user_version").fetchone() == (18,)
        assert db.execute("SELECT count(*) FROM review_documents").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM drive_destination").fetchone() == (0,)


def test_docs_is_separately_opt_in(tmp_path):
    config = Config(tmp_path, MAIN, workspace=POLICY, accounts=True, drive=DriveFolderConfig(ROOT))
    with TestClient(create_app(config), base_url=MAIN) as client:
        assert client.post(endpoint({"id": "x" * 22}), json={}).status_code == 404
    with pytest.raises(ValueError, match="require configured Workspace Drive"):
        Config(tmp_path, MAIN, workspace=POLICY, accounts=True, review_docs=True)
