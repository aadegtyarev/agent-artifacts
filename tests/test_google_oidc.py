import datetime as dt
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

from agent_artifacts.config import Config, WorkspaceConfig
from agent_artifacts.google_oidc import GoogleOIDC, GoogleOIDCError, GoogleOIDCFailure


@pytest.fixture
def oidc():
    return GoogleOIDC(WorkspaceConfig("client", "secret", "company.example"),
                      "https://app.example", "https://raw.example")


@pytest.fixture(scope="module")
def keys():
    def make():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = dt.datetime.now(dt.UTC)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        return crypt.RSASigner.from_string(pem, key_id="test-key"), cert.public_bytes(
            serialization.Encoding.PEM).decode()
    return make(), make()


def test_config_defaults_and_complete_settings(monkeypatch):
    for name in ("AA_GOOGLE_CLIENT_ID", "AA_GOOGLE_CLIENT_SECRET", "AA_GOOGLE_HOSTED_DOMAIN",
                 "AA_PUBLIC_URL", "AA_RAW_URL"):
        monkeypatch.delenv(name, raising=False)
    assert Config.from_env().workspace is None
    monkeypatch.setenv("AA_GOOGLE_CLIENT_ID", "")
    with pytest.raises(ValueError):
        Config.from_env()
    monkeypatch.setenv("AA_GOOGLE_CLIENT_ID", "client")
    with pytest.raises(ValueError):
        Config.from_env()
    monkeypatch.setenv("AA_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AA_GOOGLE_HOSTED_DOMAIN", "company.example")
    with pytest.raises(ValueError):
        Config.from_env()  # default HTTP is not permitted in Workspace mode
    monkeypatch.setenv("AA_PUBLIC_URL", "https://app.example")
    monkeypatch.setenv("AA_RAW_URL", "https://raw.example")
    config = Config.from_env()
    assert config.workspace == WorkspaceConfig("client", "secret", "company.example")
    assert "secret" not in repr(config)
    monkeypatch.setenv("AA_RAW_URL", "http://localhost")
    with pytest.raises(ValueError):
        Config.from_env()


@pytest.mark.parametrize("domain", ["Company.example", "company.example.", "*.example", "a" * 64])
def test_invalid_hosted_domain(monkeypatch, domain):
    monkeypatch.setenv("AA_GOOGLE_CLIENT_ID", "client")
    monkeypatch.setenv("AA_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AA_GOOGLE_HOSTED_DOMAIN", domain)
    monkeypatch.setenv("AA_PUBLIC_URL", "https://app.example")
    monkeypatch.delenv("AA_RAW_URL", raising=False)
    with pytest.raises(ValueError):
        Config.from_env()


@pytest.mark.parametrize("url", ["https://app.example/path", "https://u:p@app.example",
                                  "https://app.example?query=1", "https://app.example:bad"])
def test_config_rejects_invalid_workspace_origins(monkeypatch, url):
    monkeypatch.setenv("AA_GOOGLE_CLIENT_ID", "client")
    monkeypatch.setenv("AA_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AA_GOOGLE_HOSTED_DOMAIN", "company.example")
    monkeypatch.setenv("AA_PUBLIC_URL", url)
    monkeypatch.delenv("AA_RAW_URL", raising=False)
    with pytest.raises(ValueError):
        Config.from_env()


def test_authorization_url_and_callback(oidc):
    url = oidc.authorization_url(redirect_uri="https://raw.example/auth/callback", state="state",
                                 nonce="nonce", code_challenge="challenge")
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "accounts.google.com"
    assert parse_qs(parts.query) == {
        "client_id": ["client"], "redirect_uri": ["https://raw.example/auth/callback"],
        "response_type": ["code"], "scope": ["openid email profile"], "state": ["state"],
        "nonce": ["nonce"], "hd": ["company.example"], "code_challenge": ["challenge"],
        "code_challenge_method": ["S256"],
    }
    with pytest.raises(GoogleOIDCError):
        oidc.authorization_url(redirect_uri="https://evil.example/auth/callback", state="s",
                               nonce="n", code_challenge="c")


@pytest.mark.parametrize(("reported", "reason"), [
    ("invalid_client", GoogleOIDCFailure.TOKEN_CLIENT),
    ("invalid_grant", GoogleOIDCFailure.TOKEN_GRANT),
    ("provider-secret-value", GoogleOIDCFailure.TOKEN_REJECTED),
])
def test_exchange_rejects_http_errors_without_exposing_credentials(monkeypatch, oidc, reported, reason):
    monkeypatch.setattr("agent_artifacts.google_oidc.requests.post", lambda *a, **kw:
                        SimpleNamespace(status_code=400, json=lambda: {"error": reported,
                                                                       "error_description": "private-detail"}))
    with pytest.raises(GoogleOIDCError) as error:
        oidc.exchange(code="code", redirect_uri="https://app.example/auth/callback",
                      code_verifier="verifier", nonce="nonce")
    assert error.value.reason is reason
    assert "secret" not in str(error.value)
    assert "code" not in str(error.value)
    assert "private-detail" not in str(error.value)


@pytest.mark.parametrize("change", [
    {}, {"aud": "wrong"}, {"iss": "https://evil.example"}, {"nonce": "wrong"},
    {"hd": "other.example"}, {"hd": None}, {"email_verified": False},
    {"email_verified": "true"}, {"sub": ""}, {"exp": 1}, {"signature": "wrong"},
])
def test_exchange_verifies_signed_token(monkeypatch, oidc, keys, change):
    (signer, cert), (other_signer, _) = keys
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": "client", "sub": "123456",
              "email": "user@company.example", "email_verified": True,
              "hd": "company.example", "name": "Verified Name", "nonce": "nonce",
              "iat": now, "exp": now + 600}
    changes = dict(change)
    wrong_signature = changes.pop("signature", None)
    claims.update(changes)
    token = jwt.encode(other_signer if wrong_signature else signer, claims).decode()
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {"id_token": token})

    def cert_request(url, **kwargs):
        assert url == "https://www.googleapis.com/oauth2/v1/certs"
        return SimpleNamespace(status=200, data=json.dumps({"test-key": cert}).encode())

    monkeypatch.setattr("agent_artifacts.google_oidc.requests.post", post)
    def exchange():
        return oidc.exchange(code="code", redirect_uri="https://app.example/auth/callback",
                             code_verifier="verifier", nonce="nonce", verifier_request=cert_request)
    if change:
        with pytest.raises(GoogleOIDCError) as error:
            exchange()
        label = next(iter(change))
        assert error.value.reason is {
            "hd": GoogleOIDCFailure.DOMAIN_MISMATCH,
            "email_verified": GoogleOIDCFailure.EMAIL_UNVERIFIED,
            "nonce": GoogleOIDCFailure.NONCE_MISMATCH,
            "sub": GoogleOIDCFailure.PROFILE_INVALID,
        }.get(label, GoogleOIDCFailure.ID_TOKEN_INVALID)
    else:
        identity = exchange()
        assert (identity.subject, identity.email, identity.name) == (
            "123456", "user@company.example", "Verified Name")
    assert calls == [("https://oauth2.googleapis.com/token", {
        "data": {"code": "code", "client_id": "client", "client_secret": "secret",
                 "redirect_uri": "https://app.example/auth/callback",
                 "grant_type": "authorization_code", "code_verifier": "verifier"},
        "timeout": 10, "allow_redirects": False,
    })]
