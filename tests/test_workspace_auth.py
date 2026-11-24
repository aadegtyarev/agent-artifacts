import asyncio
import base64
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_artifacts.config import WorkspaceConfig
from agent_artifacts.google_oidc import GoogleIdentity, GoogleOIDCError
from agent_artifacts.workspace_auth import (
    LOGIN_COOKIE,
    LOGIN_TTL,
    SESSION_COOKIE,
    SESSION_TTL,
    WorkspaceAuth,
    WorkspaceAuthStore,
)

MAIN = "https://app.example"
RAW = "https://raw.example"
ID = "a" * 22
PATH = f"/a/{ID}"
RAW_PATH = f"{PATH}/raw"


class FakeGoogle:
    callbacks = {MAIN + "/auth/callback", RAW + "/auth/callback"}

    def __init__(self):
        self.authorizations = []
        self.exchanges = []
        self.fail = False

    def authorization_url(self, **kwargs):
        self.authorizations.append(kwargs)
        return "https://accounts.google.com/auth?" + urlencode(kwargs)

    def exchange(self, **kwargs):
        try:
            asyncio.get_running_loop()
            on_event_loop = True
        except RuntimeError:
            on_event_loop = False
        self.exchanges.append((kwargs, on_event_loop))
        if self.fail:
            raise GoogleOIDCError("bad token")
        return GoogleIdentity("stable-sub", "person@company.example", "Person")


@pytest.fixture
def harness(tmp_path):
    now = [1000.0]
    policy = WorkspaceConfig("client", "secret", "company.example")
    store = WorkspaceAuthStore(tmp_path / "auth.sqlite", policy, clock=lambda: now[0])
    google = FakeGoogle()
    main = WorkspaceAuth(store, google, origin=MAIN)
    raw = WorkspaceAuth(store, google, origin=RAW, raw=True)

    def app(auth):
        async def me(request):
            identity = await auth.identity(request)
            return JSONResponse(None if identity is None else identity.subject)
        return Starlette(routes=[Route("/auth/login", auth.login),
                                 Route("/auth/callback", auth.callback),
                                 Route("/auth/logout", auth.logout, methods=["POST"]),
                                 Route("/me", me)])
    return SimpleHarness(now, store, google, TestClient(app(main), base_url=MAIN),
                         TestClient(app(raw), base_url=RAW))


class SimpleHarness:
    def __init__(self, now, store, google, main, raw):
        self.now, self.store, self.google, self.main, self.raw = now, store, google, main, raw

    def start(self, client=None, dest=PATH):
        client = client or self.main
        response = client.get("/auth/login", params={"return": dest}, follow_redirects=False)
        assert response.status_code == 303
        return response, parse_qs(urlsplit(response.headers["location"]).query)["state"][0]

    def callback(self, state, client=None):
        client = client or self.main
        return client.get("/auth/callback", params={"state": state, "code": "code"},
                          follow_redirects=False)


def cookie_header(response, name):
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(name + "="):
            parsed = SimpleCookie()
            parsed.load(header)
            return parsed[name]
    raise AssertionError(f"missing {name}")


def test_login_callback_cookie_pkce_and_session(harness):
    response, state = harness.start()
    assert response.headers["cache-control"] == "no-store"
    login_cookie = cookie_header(response, LOGIN_COOKIE)
    assert login_cookie["secure"] and login_cookie["httponly"]
    assert login_cookie["samesite"] == "lax" and login_cookie["path"] == "/"
    assert not login_cookie["domain"] and login_cookie["max-age"] == str(LOGIN_TTL)
    auth = harness.google.authorizations[-1]
    assert auth["redirect_uri"] == MAIN + "/auth/callback"
    assert state == auth["state"]
    response = harness.callback(state)
    assert response.status_code == 303 and response.headers["location"] == PATH
    assert response.headers["cache-control"] == "no-store"
    cookie = cookie_header(response, SESSION_COOKIE)
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "lax"
    assert cookie["path"] == "/" and not cookie["domain"]
    assert cookie["max-age"] == str(SESSION_TTL)
    assert cookie_header(response, LOGIN_COOKIE)["max-age"] == "0"
    exchange, on_event_loop = harness.google.exchanges[-1]
    assert not on_event_loop
    assert exchange["nonce"] == auth["nonce"]
    verifier = exchange["code_verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge.decode() == auth["code_challenge"]
    assert harness.main.get("/me").json() == "stable-sub"
    with sqlite3.connect(harness.store.path) as db:
        token_hash, origin, subject = db.execute(
            "SELECT token_hash, origin, subject FROM sessions").fetchone()
    assert token_hash != cookie.value and len(token_hash) == 64
    assert (origin, subject) == (MAIN, "stable-sub")


def test_missing_mismatched_and_replayed_state(harness):
    _, state = harness.start()
    browser = harness.main.cookies.get(LOGIN_COOKIE)
    harness.main.cookies.clear()
    assert harness.callback(state).status_code == 400
    harness.main.cookies.set(LOGIN_COOKIE, "a" * 43, domain="app.example", path="/")
    assert harness.callback(state).status_code == 400
    harness.main.cookies.set(LOGIN_COOKIE, browser, domain="app.example", path="/")
    assert harness.callback("b" * 43).status_code == 400
    assert harness.callback(state).status_code == 303
    harness.main.cookies.set(LOGIN_COOKIE, browser, domain="app.example", path="/")
    assert harness.callback(state).status_code == 400
    assert len(harness.google.exchanges) == 1


def test_login_expiry_and_failed_exchange_consumes_state(harness):
    _, state = harness.start()
    harness.now[0] += LOGIN_TTL
    assert harness.callback(state).status_code == 400
    assert not harness.google.exchanges
    _, state = harness.start()
    browser = harness.main.cookies.get(LOGIN_COOKIE)
    harness.google.fail = True
    assert harness.callback(state).status_code == 401
    harness.main.cookies.set(LOGIN_COOKIE, browser, domain="app.example", path="/")
    harness.google.fail = False
    assert harness.callback(state).status_code == 400
    assert len(harness.google.exchanges) == 1


def test_login_state_bound_to_origin(harness):
    _, state = harness.start()
    browser = harness.main.cookies.get(LOGIN_COOKIE)
    harness.raw.cookies.set(LOGIN_COOKIE, browser, domain="raw.example", path="/")
    assert harness.callback(state, harness.raw).status_code == 400
    assert not harness.google.exchanges
    assert harness.callback(state).status_code == 303


def test_session_expiry_logout_and_csrf(harness):
    _, state = harness.start()
    harness.callback(state)
    assert harness.main.post("/auth/logout").status_code == 403
    response = harness.main.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert harness.main.get("/me").json() == "stable-sub"
    token = harness.main.cookies.get(SESSION_COOKIE)
    response = harness.main.post("/auth/logout", headers={"Origin": MAIN})
    assert response.status_code == 204 and response.headers["cache-control"] == "no-store"
    assert cookie_header(response, SESSION_COOKIE)["max-age"] == "0"
    harness.main.cookies.set(SESSION_COOKIE, token, domain="app.example", path="/")
    assert harness.main.get("/me").json() is None
    _, state = harness.start()
    harness.callback(state)
    harness.now[0] += SESSION_TTL
    assert harness.main.get("/me").json() is None


def test_separate_origin_binding(harness):
    _, state = harness.start()
    assert harness.callback(state).status_code == 303
    token = harness.main.cookies.get(SESSION_COOKIE)
    harness.raw.cookies.set(SESSION_COOKIE, token, domain="raw.example", path="/")
    assert harness.raw.get("/me").json() is None
    _, raw_state = harness.start(harness.raw, RAW_PATH)
    assert harness.callback(raw_state, harness.raw).headers["location"] == RAW_PATH
    assert harness.raw.get("/me").json() == "stable-sub"
    raw_token = harness.raw.cookies.get(SESSION_COOKIE)
    harness.main.cookies.set(SESSION_COOKIE, raw_token, domain="app.example", path="/")
    assert harness.main.get("/me").json() is None
    assert harness.google.exchanges[-1][0]["redirect_uri"] == RAW + "/auth/callback"


@pytest.mark.parametrize("path", ["//evil.example", "https://evil.example", "/a/../evil",
                                  PATH + "?q=1", PATH + "#fragment", PATH + "/raw/extra",
                                  "/auth/logout", "/", "/a/" + "a" * 23,
                                  "/\\evil.example", "/%2f%2fevil.example"])
def test_reject_unsafe_return_paths(harness, path):
    response = harness.main.get("/auth/login", params={"return": path}, follow_redirects=False)
    assert response.status_code == 400 and response.headers["cache-control"] == "no-store"
    assert not harness.google.authorizations


def test_valid_main_and_raw_return_paths(harness):
    for path in (PATH, RAW_PATH, PATH + "/download", "/p/" + ID, "/join/" + "x" * 43):
        assert harness.start(dest=path)[0].status_code == 303
    for path in (PATH, PATH + "/download", "/p/" + ID, "/join/" + "x" * 43):
        assert harness.raw.get("/auth/login", params={"return": path}).status_code == 400
    assert harness.start(harness.raw, RAW_PATH)[0].status_code == 303


def test_duplicate_parameters_rejected(harness):
    assert harness.main.get("/auth/login?return=/a/a&return=/a/b").status_code == 400
    _, state = harness.start()
    assert harness.main.get(f"/auth/callback?state={state}&state={state}&code=c").status_code == 400
    assert harness.main.get(f"/auth/callback?state={state}&code=c&code=d").status_code == 400
    response = harness.main.get(f"/auth/callback?state={state}&code=c&error=access_denied")
    assert response.status_code == 400
    assert not harness.google.exchanges


def test_atomic_state_consumption(harness):
    _, state = harness.start()
    browser = harness.main.cookies.get(LOGIN_COOKIE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: harness.store.consume(state, browser, MAIN), range(2)))
    assert sum(result is not None for result in results) == 1


def test_legacy_unbound_auth_rows_are_not_accepted(tmp_path):
    path = tmp_path / "legacy.sqlite"
    policy = WorkspaceConfig("client", "secret", "company.example")
    WorkspaceAuthStore(path, policy, clock=lambda: 1000)
    token, state, browser = "a" * 43, "b" * 43, "c" * 43

    def legacy_hash(value):
        return hashlib.sha256(value.encode("ascii")).hexdigest()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                   (legacy_hash(token), MAIN, "sub", "a@example.com", "Alice", 2000))
        db.execute("INSERT INTO login_attempts VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (legacy_hash(state), legacy_hash(browser), MAIN, "nonce", "verifier",
                    PATH, 2000))
    reopened = WorkspaceAuthStore(path, policy, clock=lambda: 1000)
    assert reopened.identity(token, MAIN) is None
    assert reopened.consume(state, browser, MAIN) is None
