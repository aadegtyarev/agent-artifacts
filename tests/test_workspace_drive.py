"""Workspace Drive authorization and per-request routing; never contacts Google."""

import json
import stat
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from starlette.testclient import TestClient
from test_drive_layout import LayoutDrive
from test_drive_storage import Response
from test_drive_web import upload
from test_workspace_web import MAIN, RAW, page_marker

from agent_artifacts.config import Config, DriveFolderConfig, WorkspaceConfig
from agent_artifacts.drive_grants import DriveGrantStoreError, WorkspaceDriveGrants
from agent_artifacts.drive_storage import DriveTransientError
from agent_artifacts.google_oidc import DRIVE_SCOPE, TOKEN_ENDPOINT, GoogleIdentity, GoogleOIDC
from agent_artifacts.store import Store
from agent_artifacts.web import create_app
from agent_artifacts.workspace_auth import SESSION_COOKIE, WorkspaceAuthStore
from agent_artifacts.workspace_drive import DriveGrantRequired, refresh_access

POLICY = WorkspaceConfig("workspace-client", "synthetic-client-secret", "company.example")
ROOT = "configured-test-folder"
REFRESH = {name: "synthetic-refresh-value-" + name for name in ("author", "viewer", "denied")}
ACCESS = {name: "synthetic-access-value-" + name for name in REFRESH}
DATA = b"exact bytes from the first author"


def reply(status, payload):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode("utf-8")
    result._content_consumed = True
    return result


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(tmp_path, max_total_bytes=100_000)
    folder = LayoutDrive("my-drive", root=ROOT)
    calls = {"exchanges": [], "refreshes": [], "operations": [], "closed": []}
    can_refresh = set(REFRESH)
    logins_without_token = set()

    def exchange(self, code, uri, code_verifier, nonce, verifier_request):
        calls["exchanges"].append((code, uri))
        who = code.removesuffix("-fresh")
        # The shared email proves association does not use an email string.
        identity = GoogleIdentity(who, "same@company.example", who)
        supplied = "synthetic-refresh-fresh-value-author" if code.endswith("-fresh") else REFRESH.get(who)
        data = {"scope": f"openid https://www.googleapis.com/auth/userinfo.email {DRIVE_SCOPE}"}
        if supplied and who not in logins_without_token:
            data["refresh_token"] = supplied
        return identity, data

    def refresh(url, *, data, **kwargs):
        assert url == TOKEN_ENDPOINT and data["scope"] == DRIVE_SCOPE and data["client_secret"] == POLICY.client_secret
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        token = data["refresh_token"]
        who = next((user for user, secret in REFRESH.items() if secret == token), None)
        who = "author" if token == "synthetic-refresh-fresh-value-author" else who
        assert who is not None
        calls["refreshes"].append(who)
        if who not in can_refresh:
            return reply(400, {"error": "invalid_grant", "error_description": "never display this"})
        return reply(200, {"access_token": ACCESS[who], "token_type": "Bearer", "scope": DRIVE_SCOPE})

    class Session:
        def __init__(self, credentials):
            self.who = next(who for who, token in ACCESS.items() if token == credentials.token)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls["closed"].append(self.who)

        def request(self, method, url, **kwargs):
            if method in {"POST", "PATCH", "DELETE"}:
                with store._conn() as db:
                    assert db.execute("SELECT folder_id, folder_kind FROM drive_destination").fetchone() == (ROOT, "my-drive")
            calls["operations"].append((self.who, method, urlsplit(url).path))
            if self.who == "denied":
                return Response(status=403)
            if url.endswith("/" + ROOT):
                assert method == "GET"
                return Response(data=folder.entries[ROOT] | {"trashed": False, "capabilities": {
                    "canListChildren": True, "canAddChildren": self.who == "author"}})
            if self.who == "viewer":
                assert method == "GET"
            return folder.request(method, url, **kwargs)

    monkeypatch.setattr(GoogleOIDC, "_exchange", exchange)
    monkeypatch.setattr("agent_artifacts.workspace_drive.requests.post", refresh)
    monkeypatch.setattr("agent_artifacts.workspace_drive.DriveAuthorizedSession", Session)
    # A disk file from before the migration stays on disk, even for an unlinked agent.
    off = Config(tmp_path, MAIN, raw_url=RAW, workspace=POLICY, accounts=True)
    unlinked = {"Authorization": "Bearer " + store.add_agent("old-disk-agent")}
    with TestClient(create_app(off, store), base_url=MAIN) as old:
        disk = upload(old, unlinked, "legacy.txt", b"old disk bytes").json()
    config = Config(tmp_path, MAIN, raw_url=RAW, workspace=POLICY, accounts=True,
                    drive=DriveFolderConfig(ROOT, "my-drive"))
    keys = {}
    for who in ("author", "viewer", "denied", "no-consent"):
        store.accounts.google_account(who)
        key = store.accounts.issue_agent_key("agent-" + who, google_subject=who)
        keys[who] = {"Authorization": "Bearer " + key}
    keys["unlinked"] = unlinked
    grants = WorkspaceDriveGrants(tmp_path, POLICY)
    with TestClient(create_app(config, store), base_url=MAIN) as author:
        yield author, store, folder, keys, grants, calls, can_refresh, logins_without_token, disk


def start(client, *, destination="/account", raw=False, renew=False):
    route = RAW + "/auth/login" if raw else MAIN + "/auth/start"
    params = {"return": destination}
    if renew:
        params["consent"] = "drive"
    result = client.get(route, params=params, follow_redirects=False)
    assert result.status_code == 303
    google = parse_qs(urlsplit(result.headers["location"]).query)
    expected = {"openid", "email", "profile"}
    if not raw:
        expected.add(DRIVE_SCOPE)
    assert set(google["scope"][0].split()) == expected
    assert google.get("access_type") == (None if raw else ["offline"])
    assert google.get("prompt") == (["consent"] if renew else None)
    assert google["code_challenge_method"] == ["S256"]
    return google["state"][0]


def finish(client, state, who, *, raw=False):
    origin = RAW if raw else MAIN
    return client.get(origin + "/auth/callback", params={"state": state, "code": who}, follow_redirects=False)


def login(client, who, **kwargs):
    state = start(client, **kwargs)
    response = finish(client, state, who, raw=kwargs.get("raw", False))
    assert response.status_code == 303
    assert response.headers["location"] == kwargs.get("destination", "/account")
    return state


def test_routes_use_each_actor_or_fail_closed(env):
    client, store, folder, keys, grants, calls, _, _, disk = env
    login(client, "viewer", raw=True, destination="/a/abcdefghijklmnopqrstuv/raw")
    assert grants.get("viewer") is None  # an identity-only callback cannot persist a grant
    login(client, "author")
    assert grants.get("author") == REFRESH["author"] and grants.get("viewer") is None
    project = client.post("/api/v1/projects", headers=keys["author"], json={"title": "Drive project"}).json()
    result = upload(client, keys["author"], "report.txt", DATA, project=project["id"])
    assert result.status_code == 201
    item = result.json()
    exact, current = urlsplit(item["version_url"]).path, urlsplit(item["url"]).path
    assert upload(client, keys["author"], "report.txt", b"second version", item["id"]).status_code == 200
    # The agent's linked subject wins over any browser session or Origin.
    login(client, "viewer", destination=exact)
    assert client.get(exact + "/download").content == DATA
    assert client.get(current + "/download").content == b"second version"
    assert client.get(exact).status_code == 200
    marker = page_marker(client, current)
    assert client.get(current + "/comments", headers={"X-Workspace-Session": marker}).status_code == 200
    assert calls["operations"][-1][0] == "viewer"
    assert upload(client, keys["author"], "report.txt", b"third version", item["id"]).status_code == 200
    assert calls["refreshes"][-1] == "author"
    refused = upload(client, keys["viewer"], "viewer.txt", b"denied write")
    assert refused.status_code == 403 and refused.json()["error"] == "drive_access_denied"
    assert not store.list("agent-viewer") and not store.storage.available_for_serving(item["id"])
    before = calls["refreshes"].copy()
    refused = upload(client, keys["no-consent"], "none.txt", b"no grant")
    assert refused.status_code == 403 and refused.json()["error"] == "drive_authorization_required"
    refused = upload(client, keys["unlinked"], "none.txt", b"must not use browser grant")
    assert refused.status_code == 403 and refused.json()["error"] == "drive_account_required"
    assert calls["refreshes"] == before
    assert upload(client, keys["unlinked"], "legacy.txt", b"new disk bytes", disk["id"]).status_code == 200
    assert client.get(urlsplit(disk["url"]).path + "/download").content == b"new disk bytes"
    assert calls["refreshes"] == before and folder.blob_posts == 1
    login(client, "denied", destination=exact)
    for path in (current, exact, exact + "/raw", exact + "/download"):
        response = client.get(path)
        assert response.status_code == 403 and b"Ask your administrator" in response.content and DATA not in response.content
    own_marker = page_marker(client, urlsplit(disk["url"]).path)
    refused = client.get(current + "/comments", headers={"X-Workspace-Session": own_marker})
    assert refused.status_code == 403 and refused.json()["error"] == "drive_access_denied"
    assert refused.headers["Cache-Control"] == "no-store"
    login(client, "author", raw=True, destination=current + "/raw")
    before = calls["refreshes"].copy()
    assert client.get(RAW + current + "/raw").status_code == 404
    assert client.get(RAW + "/auth/drive-access?return=/account").status_code == 404
    assert calls["refreshes"] == before
    assert calls["closed"] == calls["refreshes"]


def test_open_review_reads_the_displayed_revision_even_after_a_replace(env):
    client, _, _, keys, _, _, _, _, disk = env
    login(client, "author")
    first = upload(client, keys["author"], "review.txt", b"original bytes").json()
    stable = urlsplit(first["url"]).path
    exact = urlsplit(first["version_url"]).path
    displayed = client.get(stable)
    assert displayed.status_code == 200
    assert f'data-version="{exact.rsplit("/", 1)[-1]}"' in displayed.text
    assert f'src="{exact}/raw"' in displayed.text
    assert f'href="{exact}/download"' in displayed.text
    assert f'data-url="{MAIN}{stable}"' in displayed.text

    second = upload(client, keys["author"], "other.txt", b"updated bytes", first["id"]).json()
    changed = urlsplit(second["version_url"]).path
    assert changed != exact
    marker = page_marker(client, stable)
    polled = client.get(stable + "/comments", headers={"X-Workspace-Session": marker})
    assert polled.headers["X-Artifact-Version"] == changed.rsplit("/", 1)[-1]
    # The content request from the already opened page can begin after replace.
    assert b"original bytes" in client.get(exact + "/raw").content
    assert client.get(exact + "/download").content == b"original bytes"
    refreshed = client.get(stable)
    assert f'src="{changed}/raw"' in refreshed.text and f'src="{exact}/raw"' not in refreshed.text
    assert client.get(stable + "/download").content == b"updated bytes"
    legacy = client.get(urlsplit(disk["url"]).path)
    assert legacy.status_code == 200 and "data-version=" not in legacy.text


def test_grant_recovery_stays_with_same_verified_session_and_subject(env):
    client, _, _, keys, grants, calls, can_refresh, without, _ = env
    assert client.get("/auth/start?return=/account&consent=drive").status_code == 401
    author_state = login(client, "author")
    assert finish(client, author_state, "viewer").status_code == 400
    assert grants.get("viewer") is None
    file = upload(client, keys["author"], "example.txt", DATA).json()
    file_path = urlsplit(file["url"]).path
    without.add("author")
    login(client, "author")
    assert grants.get("author") == REFRESH["author"]  # repeated Google sign-in need not return a refresh token
    can_refresh.remove("author")
    response = client.get(file_path, follow_redirects=False)
    assert response.headers["location"] == "/auth/drive-access?return=" + file_path
    prompt = client.get(response.headers["location"])
    assert prompt.status_code == 403 and 'name="consent" value="drive"' in prompt.text
    assert client.get("/auth/drive-access?return=//wrong.example").status_code == 404
    without.clear()
    old_session = client.cookies[SESSION_COOKIE]
    retry = start(client, destination=file_path, renew=True)
    auth_store = WorkspaceAuthStore(grants.directory.parent / "workspace-auth.sqlite", POLICY)
    other = auth_store.create_session(MAIN, GoogleIdentity("viewer", "same@company.example", "Viewer"))
    client.cookies.set(SESSION_COOKIE, other, domain=urlsplit(MAIN).netloc, path="/")
    before = calls["exchanges"].copy()
    assert finish(client, retry, "author-fresh").status_code == 400 and calls["exchanges"] == before
    client.cookies.set(SESSION_COOKIE, old_session, domain=urlsplit(MAIN).netloc, path="/")
    assert finish(client, retry, "viewer").status_code == 403
    assert grants.get("viewer") is None and grants.get("author") == REFRESH["author"]
    retry = start(client, destination=file_path, renew=True)
    response = finish(client, retry, "author-fresh")
    assert response.status_code == 303 and response.headers["location"] == file_path
    assert client.cookies[SESSION_COOKIE] == old_session
    assert grants.get("author") == "synthetic-refresh-fresh-value-author"
    can_refresh.add("author")
    assert client.get(file_path + "/download").content == DATA


def test_installation_selects_drive_mode_only_with_workspace_accounts(monkeypatch):
    monkeypatch.setenv("AA_PUBLIC_URL", MAIN)
    monkeypatch.delenv("AA_RAW_URL", raising=False)
    monkeypatch.setenv("AA_GOOGLE_CLIENT_ID", POLICY.client_id)
    monkeypatch.setenv("AA_GOOGLE_CLIENT_SECRET", POLICY.client_secret)
    monkeypatch.setenv("AA_GOOGLE_HOSTED_DOMAIN", POLICY.hosted_domain)
    monkeypatch.setenv("AA_ACCOUNTS", "0")
    monkeypatch.setenv("AA_DRIVE_FOLDER_ID", ROOT)
    with pytest.raises(ValueError, match="requires Workspace sign-in and accounts"):
        Config.from_env()
    monkeypatch.setenv("AA_ACCOUNTS", "1")
    monkeypatch.setenv("AA_DRIVE_FOLDER_KIND", "my-drive")
    assert Config.from_env().drive == DriveFolderConfig(ROOT, "my-drive")
    monkeypatch.setenv("AA_DRIVE_FOLDER_KIND", "invalid")
    with pytest.raises(ValueError, match="AA_DRIVE_FOLDER_KIND"):
        Config.from_env()
    monkeypatch.setenv("AA_DRIVE_FOLDER_ID", "https://drive.google.com/folders/" + ROOT)
    with pytest.raises(ValueError, match="must be a folder ID"):
        Config.from_env()
    monkeypatch.delenv("AA_DRIVE_FOLDER_ID")
    assert Config.from_env().drive is None


def test_grants_are_private_and_policy_bound(tmp_path):
    grants = WorkspaceDriveGrants(tmp_path, POLICY)
    subject = "opaque/provider:subject"
    grants.put(subject, REFRESH["author"])
    other_policy = WorkspaceDriveGrants(tmp_path, WorkspaceConfig("other-app", "secret", "company.example"))
    assert grants.get(subject) == REFRESH["author"] and other_policy.get(subject) is None
    assert stat.S_IMODE(grants.directory.stat().st_mode) == 0o700
    file, = grants.directory.iterdir()
    assert subject not in file.name and stat.S_IMODE(file.stat().st_mode) == 0o600
    file.chmod(0o644)
    with pytest.raises(DriveGrantStoreError, match="could not be read"):
        grants.get(subject)
    file.unlink()
    symlink = tmp_path / "unrelated"
    symlink.write_text(REFRESH["viewer"])
    file.symlink_to(symlink)
    with pytest.raises(DriveGrantStoreError):
        grants.get(subject)
    grants.put(subject, REFRESH["author"])
    assert not file.is_symlink() and symlink.read_text() == REFRESH["viewer"]


@pytest.mark.parametrize(("status", "body", "failure"), [
    (400, {"error": "invalid_grant"}, DriveGrantRequired),
    (200, {"token_type": "Bearer", "access_token": ACCESS["author"], "scope": "openid"}, DriveGrantRequired),
    (200, {"token_type": "Bearer", "access_token": "\r\nHeader: secret", "scope": DRIVE_SCOPE}, DriveTransientError),
    (307, {}, DriveTransientError),
])
def test_refresh_rejections_are_not_retried_or_logged(monkeypatch, status, body, failure):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return reply(status, body | {"error_description": "sensitive value"})

    monkeypatch.setattr("agent_artifacts.workspace_drive.requests.post", post)
    with pytest.raises(failure) as exc:
        refresh_access(POLICY, REFRESH["author"], timeout=3)
    assert len(calls) == 1 and calls[0][1]["allow_redirects"] is False
    assert "sensitive value" not in str(exc.value) and REFRESH["author"] not in str(exc.value)
