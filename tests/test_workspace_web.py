import re
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_artifacts.config import Config, WorkspaceConfig
from agent_artifacts.google_oidc import GoogleIdentity
from agent_artifacts.pages import SANDBOX_CSP
from agent_artifacts.store import Store
from agent_artifacts.web import create_app
from agent_artifacts.workspace_auth import SESSION_COOKIE

MAIN = "https://app.example"
RAW = "https://raw.example"
SECRET = b"<h1>private content</h1>"


class FakeGoogle:
    def __init__(self, config, public_url, raw_url):
        self.callbacks = {url + "/auth/callback" for url in (public_url, raw_url) if url}

    def authorization_url(self, **kwargs):
        return "https://accounts.google.com/auth?" + urlencode(kwargs)

    def exchange(self, **kwargs):
        return GoogleIdentity("google-sub", "person@company.example", "Person")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    config = Config(
        data_dir=tmp_path,
        public_url=MAIN,
        raw_url=RAW,
        workspace=WorkspaceConfig("client", "secret", "company.example"),
    )
    store = Store(tmp_path, max_total_bytes=config.max_total_bytes)
    key = store.add_agent("agent", admin=True)
    _, invite = store.add_invite("newperson", by="agent", ttl=3600)
    app = create_app(config, store)

    async def identity(request):
        return JSONResponse(request.state.workspace_identity.subject)

    app.router.routes.append(Route("/a/{id:artifact_id}/identity", identity))
    with TestClient(app, base_url=MAIN) as client:
        headers = {"Authorization": f"Bearer {key}"}
        project = client.post(
            "/api/v1/projects", headers=headers, json={"title": "private project"}
        )
        assert project.status_code == 201
        project_id = project.json()["id"]
        uploaded = client.post(
            "/api/v1/artifacts",
            headers=headers,
            files={"file": ("private.html", SECRET)},
            data={"project": project_id},
        )
        assert uploaded.status_code == 201
        yield client, store, key, uploaded.json()["id"], project_id, invite


def sign_in(client, origin, destination):
    response = client.get(
        origin + "/auth/login", params={"return": destination}, follow_redirects=False
    )
    assert response.status_code == 303
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    response = client.get(
        origin + "/auth/callback", params={"state": state, "code": "fake"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == destination


def page_marker(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return re.search(r'data-workspace-marker="([A-Za-z0-9_-]+)"', response.text)[1]


def test_anonymous_denied_without_bytes_or_mutations(env):
    client, store, key, artifact, project, invite = env
    nav = (f"/a/{artifact}", f"/p/{project}", f"/join/{invite}")
    for path in nav:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login?return=" + path
        assert response.headers["cache-control"] == "no-store"
        assert SECRET not in response.content
        response = client.head(path, follow_redirects=False)
        assert response.status_code == 401 and not response.content
    for path in (
        f"/a/{artifact}/raw",
        f"/a/{artifact}/download",
        f"/a/{artifact}/comments",
        f"/a/{artifact}/drafts",
        f"/p/{project}/summary",
    ):
        for method in ("GET", "HEAD"):
            response = client.request(method, path, follow_redirects=False)
            assert response.status_code == 401 and not response.content
            assert response.headers["cache-control"] == "no-store"
    for path in (
        f"/a/{artifact}/drafts",
        f"/a/{artifact}/drafts/batch",
        f"/a/{artifact}/drafts/delete",
        f"/a/{artifact}/review",
        f"/join/{invite}",
    ):
        response = client.post(path, json={"author": "x", "text": "bad"}, headers={"Origin": MAIN})
        assert response.status_code == 401 and not response.content
    assert store.invite_for_token(invite) is not None
    assert (
        client.get(
            f"/api/v1/artifacts/{artifact}/comments", headers={"Authorization": f"Bearer {key}"}
        ).json()
        == []
    )


def test_raw_origin_isolation_and_navigation(env):
    client, _, _, artifact, project, _ = env
    path = f"/a/{artifact}/raw"
    for headers in ({}, {"sec-fetch-dest": "iframe"}):
        response = client.get(RAW + path, headers=headers, follow_redirects=False)
        assert response.status_code == 401 and not response.content
    assert client.head(RAW + path).status_code == 401
    response = client.get(
        RAW + path, headers={"sec-fetch-dest": "document"}, follow_redirects=False
    )
    assert (
        response.status_code == 303 and response.headers["location"] == "/auth/login?return=" + path
    )
    for path in (f"/p/{project}", "/api/v1/me", f"/a/{artifact}", "/static/site.css"):
        assert client.get(RAW + path, follow_redirects=False).status_code == 404
    sign_in(client, MAIN, f"/a/{artifact}")
    token = client.cookies.get(SESSION_COOKIE, domain="app.example")
    response = client.get(
        RAW + f"/a/{artifact}/raw", headers={"Cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert response.status_code == 401 and not response.content
    sign_in(client, RAW, f"/a/{artifact}/raw")
    response = client.get(RAW + f"/a/{artifact}/raw")
    assert response.status_code == 200 and SECRET in response.content
    assert response.headers["content-security-policy"] == SANDBOX_CSP
    assert response.headers["cache-control"] == "no-store"
    assert client.head(RAW + f"/a/{artifact}/raw").status_code == 200
    assert client.get(RAW + f"/p/{project}").status_code == 404


def test_authorized_main_and_csrf(env):
    client, store, key, artifact, project, invite = env
    sign_in(client, MAIN, f"/a/{artifact}")
    marker = page_marker(client, f"/a/{artifact}")
    for path in (
        f"/a/{artifact}",
        f"/a/{artifact}/raw",
        f"/a/{artifact}/download",
        f"/a/{artifact}/comments",
        f"/p/{project}",
        f"/p/{project}/summary",
        f"/join/{invite}",
    ):
        response = client.get(path, headers={"X-Workspace-Session": marker})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    assert client.head(f"/a/{artifact}").status_code == 200
    assert client.head(f"/a/{artifact}/raw").status_code == 200
    assert client.get(f"/a/{artifact}/identity").json() == "google-sub"
    path = f"/a/{artifact}/drafts"
    body = {"author": "user", "text": "draft"}
    author = {"X-Author-Key": "a" * 22, "X-Workspace-Session": marker}
    for headers in (
        author,
        author | {"Origin": "https://evil.example"},
        author | {"Origin": "https://app.example."},
        author | {"Origin": "https://app.example:444"},
    ):
        assert client.post(path, json=body, headers=headers).status_code == 403
    assert client.post(f"/join/{invite}").status_code == 403
    assert store.invite_for_token(invite) is not None
    response = client.post(path, json=body, headers=author | {"Origin": MAIN + ":443"})
    assert response.status_code == 201
    assert client.get(path, headers=author).json()[0]["text"] == "draft"
    # Agent APIs remain key-authorized and do not require browser Origin.
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert client.post(f"/join/{invite}", headers={"Origin": MAIN}).status_code == 200
    assert store.invite_for_token(invite) is None
    assert client.post("/auth/logout", headers={"Origin": MAIN + ":443"}).status_code == 204
    assert client.get(f"/a/{artifact}/comments").status_code == 401


def test_authorities_host_header_and_auth_methods(env):
    client, _, key, artifact, _, _ = env
    path = f"/a/{artifact}"
    for host in ("evil.example", "app.example:444", "raw.example:444", "app.example.evil"):
        response = client.get(
            path, headers={"Host": host, "X-Forwarded-Host": "app.example"}, follow_redirects=False
        )
        assert response.status_code == 400 and not response.content
    response = client.get(
        path,
        headers={"Host": "app.example:443", "X-Forwarded-Host": "evil.example"},
        follow_redirects=False,
    )
    assert (
        response.status_code == 303 and response.headers["location"] == "/auth/login?return=" + path
    )
    assert (
        client.get(RAW + "/api/v1/me", headers={"Authorization": f"Bearer {key}"}).status_code
        == 404
    )
    for origin in (MAIN, RAW):
        assert client.post(origin + "/auth/login").status_code == 405
        assert client.head(origin + "/auth/callback").status_code == 405
        assert client.get(origin + "/auth/logout").status_code == 405


def test_public_mode_still_anonymous_and_no_workspace_db(tmp_path):
    config = Config(data_dir=tmp_path, public_url=MAIN)
    store = Store(tmp_path, max_total_bytes=config.max_total_bytes)
    key = store.add_agent("agent")
    with TestClient(create_app(config, store), base_url=MAIN) as client:
        response = client.post(
            "/api/v1/artifacts",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("private.html", SECRET)},
        )
        artifact = response.json()["id"]
        page = client.get(f"/a/{artifact}")
        assert page.status_code == 200 and "data-workspace-marker" not in page.text
        assert SECRET in client.get(f"/a/{artifact}/raw").content
        assert client.get(f"/a/{artifact}/comments").status_code == 200
        assert (
            client.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        )
        assert client.get("/auth/login").status_code == 404
    assert not (tmp_path / "workspace-auth.sqlite").exists()


def test_nondefault_ports_are_not_interchangeable(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    config = Config(
        data_dir=tmp_path,
        public_url="https://app.example:8443",
        raw_url="https://raw.example:9443",
        workspace=WorkspaceConfig("client", "secret", "company.example"),
    )
    with TestClient(create_app(config), base_url=config.public_url) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health", headers={"Host": "app.example"}).status_code == 400
        assert client.get("/health", headers={"Host": "app.example:443"}).status_code == 400
        assert client.get("/health", headers={"Host": "app.example:9443"}).status_code == 400
        assert client.get("/health", headers={"Host": "raw.example:9443"}).status_code == 404
        assert client.get("/health", headers={"Host": "raw.example:443"}).status_code == 400


def test_verified_identity_draft_isolation_and_attribution(env, monkeypatch):
    client, _, key, artifact, project, _ = env
    current = [GoogleIdentity("sub-a", "a@company.example", "Alice\n<script>")]
    monkeypatch.setattr(FakeGoogle, "exchange", lambda self, **kwargs: current[0])
    path = f"/a/{artifact}"
    first_key = {"Origin": MAIN, "X-Author-Key": "a" * 22}
    other_key = {"Origin": MAIN, "X-Author-Key": "b" * 22}

    def login_as_current():
        sign_in(client, MAIN, path)
        marker = page_marker(client, path)
        first_key["X-Workspace-Session"] = marker
        other_key["X-Workspace-Session"] = marker

    login_as_current()
    response = client.post(
        path + "/drafts", headers=first_key, json={"author": None, "text": "private draft"}
    )
    assert response.status_code == 201
    draft = response.json()
    assert draft["author"] == "Alice <script>"
    page = client.get(path).text
    assert 'data-workspace-author="Alice &lt;script&gt;"' in page
    assert "What should we call you?" not in page and 'id="settings-name"' not in page
    assert 'id="workspace-sign-out"' in page
    project_page = client.get(f"/p/{project}").text
    assert "Alice &lt;script&gt;" in project_page and 'id="workspace-sign-out"' in project_page
    # A separate session for the same subject sees the draft with a different browser key.
    client.cookies.clear()
    login_as_current()
    assert client.get(path + "/drafts", headers=other_key).json()[0]["id"] == draft["id"]
    assert client.get(f"/p/{project}/summary", headers=other_key).json()[artifact]["drafts"] == 1
    client.cookies.clear()
    current[0] = GoogleIdentity("sub-b", "b@company.example", "Bob")
    login_as_current()
    assert client.get(path + "/drafts", headers=first_key).json() == []
    assert client.get(f"/p/{project}/summary", headers=first_key).json()[artifact]["drafts"] == 0
    item = path + "/drafts/" + draft["id"]
    assert client.patch(item, headers=first_key, json={"text": "stolen"}).status_code == 404
    assert client.delete(item, headers=first_key).status_code == 404
    assert (
        client.post(
            path + "/drafts/delete", headers=first_key, json={"ids": [draft["id"]]}
        ).status_code
        == 204
    )
    assert (
        client.post(path + "/review", headers=first_key, json={"author": "Alice"}).status_code
        == 400
    )
    response = client.post(
        path + "/review", headers=first_key, json={"author": "Alice", "text": "Bob's comment"}
    )
    assert response.status_code == 201 and response.json()[0]["author"] == "Bob"
    comment_n = response.json()[0]["n"]
    client.cookies.clear()
    current[0] = GoogleIdentity("sub-a", "a@company.example", "Alice\n<script>")
    login_as_current()
    assert client.get(path + "/drafts", headers=other_key).json()[0]["id"] == draft["id"]
    response = client.post(
        path + "/drafts",
        headers=other_key,
        json={"kind": "reply", "of": comment_n, "text": "reply", "author": "Spoof"},
    )
    assert response.status_code == 201 and response.json()["author"] == "Alice <script>"
    response = client.post(
        path + "/drafts/batch",
        headers=other_key,
        json={"author": None, "decisions": [{"kind": "approve", "of": comment_n}]},
    )
    assert response.status_code == 201 and response.json()[0]["author"] == "Alice <script>"
    response = client.post(path + "/review", headers=other_key, json={"agent": False})
    assert response.status_code == 201
    published = client.get(path + "/comments", headers=other_key).json()
    bob = next(c for c in published if c["n"] == comment_n)
    alice = next(c for c in published if c["text"] == "private draft")
    assert bob["author"] == "Bob" and not bob["mine"]
    assert bob["approved_by"] == "Alice <script>"
    assert bob["replies"][0]["author"] == "Alice <script>" and bob["replies"][0]["mine"]
    assert alice["author"] == "Alice <script>" and alice["mine"]
    assert (
        client.get(
            f"/api/v1/artifacts/{artifact}/comments", headers={"Authorization": f"Bearer {key}"}
        ).status_code
        == 200
    )
    client.cookies.clear()
    assert client.get(path + "/drafts", headers=first_key).status_code == 401
    assert client.post(path + "/review", headers=first_key, json={"text": "x"}).status_code == 401


def test_workspace_invite_referrer_and_claim(env):
    client, store, _, artifact, _, invite = env
    sign_in(client, MAIN, f"/a/{artifact}")
    response = client.get(f"/join/{invite}")
    assert response.headers["referrer-policy"] == "strict-origin"
    assert client.head(f"/join/{invite}").headers["referrer-policy"] == "no-referrer"
    assert client.post(f"/join/{invite}", headers={"Origin": "null"}).status_code == 403
    assert store.invite_for_token(invite) is not None
    assert client.post(f"/join/{invite}", headers={"Origin": MAIN}).status_code == 200


def test_workspace_name_falls_back_to_verified_email(env, monkeypatch):
    client, _, _, artifact, _, _ = env
    identity = GoogleIdentity("stable", "verified@example.com", "\n\u202e")
    monkeypatch.setattr(FakeGoogle, "exchange", lambda self, **kwargs: identity)
    path = f"/a/{artifact}"
    sign_in(client, MAIN, path)
    response = client.post(
        path + "/drafts",
        headers={"Origin": MAIN, "X-Workspace-Session": page_marker(client, path)},
        json={"author": {"spoof": True}, "text": "hi"},
    )
    assert response.status_code == 201 and response.json()["author"] == "verified@example.com"


def test_sessions_and_pending_logins_follow_workspace_policy_on_restart(tmp_path, monkeypatch):
    from agent_artifacts.workspace_auth import LOGIN_COOKIE

    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    store = Store(tmp_path, max_total_bytes=10_000)
    key = store.add_agent("agent")
    policy = WorkspaceConfig("client-a", "secret", "a.example")

    def config(workspace):
        return Config(data_dir=tmp_path, public_url=MAIN, workspace=workspace)

    with TestClient(create_app(config(policy), store), base_url=MAIN) as client:
        upload = client.post("/api/v1/artifacts", headers={"Authorization": f"Bearer {key}"},
                             files={"file": ("a.html", SECRET)})
        assert upload.status_code == 201
        path = f"/a/{upload.json()['id']}"
        sign_in(client, MAIN, path)
        session = client.cookies.get(SESSION_COOKIE, domain="app.example")
        marker = page_marker(client, path)
        started = client.get("/auth/login", params={"return": path}, follow_redirects=False)
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        browser = client.cookies.get(LOGIN_COOKIE, domain="app.example")

    for changed in (WorkspaceConfig("client-a", "secret", "b.example"),
                    WorkspaceConfig("client-b", "secret", "a.example")):
        with TestClient(create_app(config(changed), store), base_url=MAIN) as client:
            response = client.get(path + "/comments",
                                  headers={"Cookie": f"{SESSION_COOKIE}={session}"})
            assert response.status_code == 401 and not response.content
            response = client.get("/auth/callback", params={"state": state, "code": "fake"},
                                  headers={"Cookie": f"{LOGIN_COOKIE}={browser}"},
                                  follow_redirects=False)
            assert response.status_code == 400

    # Secret rotation is not a change of identity policy; unchanged policy still works.
    with TestClient(create_app(config(WorkspaceConfig("client-a", "rotated", "a.example")), store),
                    base_url=MAIN) as client:
        response = client.get(path + "/comments", headers={"Cookie": f"{SESSION_COOKIE}={session}",
                                                           "X-Workspace-Session": marker})
        assert response.status_code == 200
        response = client.get("/auth/callback", params={"state": state, "code": "fake"},
                              headers={"Cookie": f"{LOGIN_COOKIE}={browser}"},
                              follow_redirects=False)
        assert response.status_code == 303


def test_stale_viewer_cannot_read_or_publish_another_session(env, monkeypatch):
    client, _, _, artifact, _, _ = env
    identity = [GoogleIdentity("alice", "alice@example.com", "Alice")]
    monkeypatch.setattr(FakeGoogle, "exchange", lambda self, **kwargs: identity[0])
    path = f"/a/{artifact}"
    sign_in(client, MAIN, path)
    alice_marker = page_marker(client, path)
    alice_cookie = client.cookies.get(SESSION_COOKIE, domain="app.example")
    assert alice_marker != alice_cookie and "alice" not in alice_marker
    alice_headers = {"Origin": MAIN, "X-Workspace-Session": alice_marker}
    assert client.post(path + "/drafts", headers=alice_headers,
                       json={"text": "Alice's draft"}).status_code == 201

    # A second session for the same subject owns the same drafts, but must use its own marker.
    sign_in(client, MAIN, path)
    same_sub_marker = page_marker(client, path)
    assert same_sub_marker != alice_marker
    assert client.get(path + "/drafts", headers=alice_headers).status_code == 409
    same_headers = {"Origin": MAIN, "X-Workspace-Session": same_sub_marker}
    assert client.get(path + "/drafts", headers=same_headers).json()[0]["text"] == "Alice's draft"

    identity[0] = GoogleIdentity("bob", "bob@example.com", "Bob")
    sign_in(client, MAIN, path)
    bob_marker = page_marker(client, path)
    bob_headers = {"Origin": MAIN, "X-Workspace-Session": bob_marker}
    bob_draft = client.post(path + "/drafts", headers=bob_headers, json={"text": "Bob's draft"})
    assert bob_draft.status_code == 201
    draft_id = bob_draft.json()["id"]
    for method, suffix, payload in (
        ("GET", "/comments", None), ("GET", "/drafts", None),
        ("POST", "/drafts", {"text": "stale compose"}),
        ("POST", "/drafts/batch", {"decisions": [{"kind": "approve", "of": 1}]}),
        ("POST", "/drafts/delete", {"ids": [draft_id]}),
        ("PATCH", "/drafts/" + draft_id, {"text": "stale edit"}),
        ("DELETE", "/drafts/" + draft_id, None),
        ("POST", "/review", {"text": "Alice's unsent review"}),
    ):
        response = client.request(method, path + suffix, headers=alice_headers, json=payload)
        assert response.status_code == 409
        assert response.json()["error"] == "workspace_session_changed"
        assert response.headers["cache-control"] == "no-store"
    assert client.get(path + "/drafts").status_code == 409
    assert client.post(path + "/review", headers={"X-Workspace-Session": bob_marker},
                       json={"text": "x"}).status_code == 403
    assert client.get(path + "/drafts", headers=bob_headers).json()[0]["text"] == "Bob's draft"
    response = client.post(path + "/review", headers=bob_headers, json={"agent": False})
    assert response.status_code == 201
    assert [(c["author"], c["text"]) for c in response.json()] == [("Bob", "Bob's draft")]
    assert client.get(path + "/comments", headers=bob_headers).json()[0]["text"] == "Bob's draft"
    assert client.get(path + "/raw").text.find(bob_marker) == -1
    client.cookies.clear()
    assert client.get(path + "/drafts", headers=bob_headers).status_code == 401
