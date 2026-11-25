import re
from urllib.parse import parse_qs, urlencode, urlsplit

from starlette.testclient import TestClient

from agent_artifacts.account_pages import DEVICE_COOKIE
from agent_artifacts.config import Config, WorkspaceConfig
from agent_artifacts.google_oidc import GoogleIdentity
from agent_artifacts.store import Store, hash_key
from agent_artifacts.web import create_app

BASE = "https://app.example"
RAW = "https://raw.example"
ORIGIN = {"Origin": BASE}


def upload(client, key, title, project=None):
    response = client.post("/api/v1/artifacts", headers={"Authorization": f"Bearer {key}"},
                           files={"file": ("sample.md", b"# Fixture")},
                           data={"title": title, **({"project": project} if project else {})})
    assert response.status_code == 201
    return response.json()["id"]


def request_claim(client, key):
    response = client.post("/api/v1/me/account-claim", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["url"] == BASE + "/account/claim" and body["expires_in"] == 600
    assert key not in response.text and body["code"] not in body["url"]
    return body["code"]


def use_claim(client, code, follow=False):
    return client.post("/account/claim", data={"code": code, "confirm": "1"}, headers=ORIGIN,
                       follow_redirects=follow)


def test_public_browser_claims_pairs_and_revokes_one_device(tmp_path):
    store = Store(tmp_path, max_total_bytes=1_000_000)
    key, outsider = store.add_agent("alice-agent"), store.add_agent("unclaimed-agent")
    app = create_app(Config(tmp_path, BASE, raw_url=RAW, accounts=True), store)
    with TestClient(app, base_url=BASE) as owner, TestClient(app, base_url=BASE) as receiver:
        project = owner.post("/api/v1/projects", headers={"Authorization": f"Bearer {key}"},
                             json={"title": "My Project"}).json()["id"]
        member = upload(owner, key, "A grouped file", project)
        standalone = upload(owner, key, "A stand-alone file")
        upload(owner, outsider, "A file this owner cannot list")
        assert receiver.get(f"/a/{member}").status_code == 200
        root = receiver.get("/")
        assert 'name="code"' not in root.text and "To connect this device" in root.text
        assert receiver.get(RAW + "/").status_code == 404
        assert owner.post("/api/v1/me/account-claim").status_code == 401
        code = request_claim(owner, key)
        assert owner.post("/account/claim", data={"code": code, "confirm": "1"}).status_code == 403
        preview = owner.post("/account/claim", data={"code": code}, headers=ORIGIN)
        assert preview.status_code == 200 and "alice-agent" in preview.text
        assert 'name="confirm"' in preview.text and DEVICE_COOKIE not in owner.cookies
        response = use_claim(owner, code)
        assert response.status_code == 303 and response.headers["location"] == "/account"
        assert all(token in response.headers["set-cookie"].lower() for token in ("secure", "httponly", "samesite=lax"))
        assert use_claim(receiver, code).status_code == 409
        assert owner.post("/api/v1/me/account-claim", headers={"Authorization": f"Bearer {key}"}).status_code == 409
        home = owner.get("/")
        assert home.url.path == "/account" and "Your account" in home.text
        assert f'href="/p/{project}"' in home.text and f'href="/a/{member}"' in home.text
        assert f'href="/a/{standalone}"' in home.text and "Files without a project" in home.text
        assert "A file this owner cannot list" not in home.text
        assert owner.post("/account/devices/connect", headers={"Origin": "https://other.example"}).status_code == 403
        assert receiver.post("/account/devices/connect", headers=ORIGIN, follow_redirects=False).status_code == 303
        offer = owner.post("/account/devices/connect", headers=ORIGIN)
        code = re.search(r'class="account-code">([A-Z2-9]{4}-[A-Z2-9]{4})', offer.text)[1]
        assert offer.headers["cache-control"] == "no-store"
        assert code not in owner.get("/account").text
        assert 'name="code"' in receiver.get("/").text
        response = receiver.post("/account/connect", data={"code": code}, headers=ORIGIN)
        assert response.url.path == "/account" and "A grouped file" in response.text
        assert receiver.cookies.get(DEVICE_COOKIE) != owner.cookies.get(DEVICE_COOKIE)
        receiver_id = store.accounts.device(receiver.cookies.get(DEVICE_COOKIE)).id
        outsider_device = store.accounts.issue_claim(hash_key(outsider))
        with TestClient(app, base_url=BASE) as other:
            assert use_claim(other, outsider_device).status_code == 303
            unavailable = other.post("/account/devices/sign-out", data={"device": receiver_id}, headers=ORIGIN)
            assert unavailable.status_code == 404
        assert owner.post("/account/devices/sign-out", data={"device": receiver_id}, headers=ORIGIN).status_code == 200
        assert receiver.get("/account").url.path == "/"
        assert 'name="code"' not in receiver.get("/").text
        assert receiver.post("/account/connect", data={"code": code}, headers=ORIGIN).status_code == 400
        assert receiver.get(f"/a/{member}").status_code == 200


class FakeGoogle:
    def __init__(self, config, public_url, raw_url):
        self.callbacks = {url + "/auth/callback" for url in (public_url, raw_url) if url}

    def authorization_url(self, **kwargs):
        return "https://accounts.google.com/auth?" + urlencode(kwargs)

    def exchange(self, *, code, **kwargs):
        # The same email is not proof of the same subject.
        return GoogleIdentity(code, "person@company.example", "Person")


def test_browser_recovers_and_changes_only_the_requested_agent_key(tmp_path):
    store = Store(tmp_path, max_total_bytes=1_000_000)
    existing_key = store.add_agent("old-agent")
    app = create_app(Config(tmp_path, BASE, accounts=True), store)
    with TestClient(app, base_url=BASE) as owner, TestClient(app, base_url=BASE) as receiver:
        assert use_claim(owner, request_claim(owner, existing_key)).status_code == 303
        assert owner.post("/account/agents/key", data={"name": "other-agent"}).status_code == 403
        issued = owner.post("/account/agents/key", data={"name": "other-agent"}, headers=ORIGIN)
        key = re.search(r'AGENT_ARTIFACTS_KEY=(aa_[\w-]+)', issued.text)[1]
        assert issued.headers["cache-control"] == "no-store"
        file = upload(owner, key, "Keep this file")
        assert key not in owner.get("/account").text
        assert owner.post("/account/agents/key", data={"name": "other-agent"}, headers=ORIGIN).status_code == 409
        confirm = owner.post("/account/agents/revoke", data={"name": "other-agent"}, headers=ORIGIN)
        assert confirm.status_code == 200 and "Revoke this key?" in confirm.text
        headers = {"Authorization": f"Bearer {key}"}
        assert owner.get("/api/v1/me", headers=headers).status_code == 200
        response = owner.post("/account/agents/revoke", data={"name": "other-agent", "confirm": "1"}, headers=ORIGIN)
        assert "Key revoked" in response.text and "Keep this file" in response.text
        assert owner.get("/api/v1/me", headers=headers).status_code == 401
        assert receiver.get(f"/a/{file}").status_code == 200
        assert owner.get("/api/v1/me", headers={"Authorization": f"Bearer {existing_key}"}).status_code == 200

        def recovery_code():
            result = owner.post("/account/recovery/key", headers=ORIGIN)
            assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
            return re.search(r'id="setup"[^>]+value="([\w-]{43})"', result.text)[1]

        first, replacement = recovery_code(), recovery_code()
        assert first != replacement and replacement not in owner.get("/account").text
        assert receiver.post("/account/recover", data={"code": replacement}).status_code == 403
        assert receiver.post("/account/recover", data={"code": first}, headers=ORIGIN).status_code == 400
        recovered = receiver.post("/account/recover", data={"code": replacement}, headers=ORIGIN)
        assert recovered.url.path == "/account" and "Keep this file" in recovered.text
        assert owner.get("/account").url.path == "/"
        assert owner.post("/account/recover", data={"code": replacement}, headers=ORIGIN).status_code == 400
        new = receiver.post("/account/agents/key", data={"name": "other-agent"}, headers=ORIGIN)
        next_key = re.search(r'AGENT_ARTIFACTS_KEY=(aa_[\w-]+)', new.text)[1]
        assert receiver.get("/api/v1/me", headers={"Authorization": f"Bearer {next_key}"}).status_code == 200
        assert receiver.get("/api/v1/me", headers=headers).status_code == 401


def google_login(client, subject, dest):
    start = client.get("/auth/start", params={"return": dest}, follow_redirects=False)
    assert start.status_code == 303
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = client.get("/auth/callback", params={"state": state, "code": subject}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == dest


def test_workspace_entry_uses_same_account_page_without_public_pairing(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_artifacts.web.GoogleOIDC", FakeGoogle)
    store = Store(tmp_path, max_total_bytes=1_000_000)
    key = store.add_agent("old-agent")
    config = Config(tmp_path, BASE, raw_url=RAW, workspace=WorkspaceConfig("client", "secret", "company.example"),
                    accounts=True)
    app = create_app(config, store)
    with TestClient(app, base_url=BASE) as owner, TestClient(app, base_url=BASE) as outsider:
        file = upload(owner, key, "Company file")
        assert owner.get("/").status_code == 200
        assert 'name="return" value="/account"' in owner.get("/").text
        assert owner.get("/account/claim").url.path == "/auth/login"
        assert owner.get(f"/a/{file}").url.path == "/auth/login"
        assert owner.get(f"/a/{file}/download").status_code == 401
        token = request_claim(owner, key)
        google_login(owner, "verified-subject", "/account")
        account = owner.get("/")
        assert account.url.path == "/account" and "Your account" in account.text
        assert "Connect another device" not in account.text and "Account recovery" not in account.text
        assert "Company file" not in account.text
        assert use_claim(owner, token, follow=True).status_code == 200
        assert "Company file" in owner.get("/account").text
        assert owner.get(f"/a/{file}").status_code == 200
        assert DEVICE_COOKIE not in owner.cookies
        assert owner.post("/account/devices/connect", headers=ORIGIN).status_code == 404
        google_login(outsider, "different-subject", "/account")
        assert "Company file" not in outsider.get("/account").text
        assert outsider.get(RAW + "/account").status_code == 404
        pattern = r'name="device" value="([\w-]+)"'
        own_id = re.search(pattern, owner.get("/account").text)[1]
        with TestClient(app, base_url=BASE) as another:
            google_login(another, "verified-subject", "/account")
            devices = re.findall(pattern, owner.get("/account").text)
            assert len(devices) == 2 and own_id in devices
            another_id = next(id_ for id_ in devices if id_ != own_id)
            assert outsider.post("/account/devices/sign-out", data={"device": another_id}, headers=ORIGIN).status_code == 404
            assert owner.post("/account/devices/sign-out", data={"device": another_id}, headers=ORIGIN).status_code == 200
            assert another.get("/account").url.path == "/auth/login"
            response = owner.post("/account/devices/sign-out", data={"device": own_id}, headers=ORIGIN)
            assert response.url.path == "/auth/signed-out"
            assert owner.get("/account").url.path == "/auth/login"
