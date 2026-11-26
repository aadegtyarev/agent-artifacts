"""Google OIDC protocol adapter. Browser state and sessions belong to the caller."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlencode

import requests
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from .config import WorkspaceConfig

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
CALLBACK_PATH = "/auth/callback"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_IDENTITY_SCOPES = frozenset({"openid", "email", "profile",
                              "https://www.googleapis.com/auth/userinfo.email",
                              "https://www.googleapis.com/auth/userinfo.profile"})
_OAUTH_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]{20,4096}={0,3}")


def drive_scope_granted(scope: object) -> bool:
    if not isinstance(scope, str) or len(scope) > 1024:
        return False
    scopes = set(scope.split())
    return DRIVE_SCOPE in scopes and scopes <= _IDENTITY_SCOPES | {DRIVE_SCOPE}


def valid_oauth_token(token: object) -> bool:
    return isinstance(token, str) and _OAUTH_TOKEN.fullmatch(token) is not None


class GoogleOIDCFailure(StrEnum):
    INVALID_CALLBACK = "google_callback_not_configured"
    TOKEN_NETWORK = "google_token_network_error"
    TOKEN_CLIENT = "google_token_client_rejected"
    TOKEN_GRANT = "google_token_grant_rejected"
    TOKEN_REJECTED = "google_token_exchange_rejected"
    ID_TOKEN_MISSING = "google_id_token_missing"
    CERT_NETWORK = "google_certificate_network_error"
    ID_TOKEN_INVALID = "google_id_token_invalid"
    NONCE_MISMATCH = "google_nonce_mismatch"
    EMAIL_UNVERIFIED = "google_email_not_verified"
    DOMAIN_MISMATCH = "google_hosted_domain_mismatch"
    PROFILE_INVALID = "google_profile_invalid"


class GoogleOIDCError(ValueError):
    """A fixed failure label, never an error body, token or claim value."""

    def __init__(self, reason: GoogleOIDCFailure) -> None:
        self.reason = GoogleOIDCFailure(reason)
        super().__init__(self.reason.value)


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str


@dataclass(frozen=True)
class GoogleDriveLogin:
    identity: GoogleIdentity
    refresh_token: str | None = field(default=None, repr=False)


class GoogleOIDC:
    def __init__(self, config: WorkspaceConfig, public_url: str, raw_url: str = "") -> None:
        self.config = config
        self.callbacks = frozenset(url + CALLBACK_PATH for url in (public_url, raw_url) if url)

    def _callback(self, redirect_uri: str) -> None:
        if redirect_uri not in self.callbacks:
            raise GoogleOIDCError(GoogleOIDCFailure.INVALID_CALLBACK)

    def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str,
        drive: bool = False, consent: bool = False,
    ) -> str:
        self._callback(redirect_uri)
        if consent and not drive:
            raise ValueError("Drive consent requires Drive authorization")
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "hd": self.config.hosted_domain,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if drive:
            params.update(scope=params["scope"] + " " + DRIVE_SCOPE, access_type="offline",
                          include_granted_scopes="false")
            if consent:
                params["prompt"] = "consent"
        return AUTHORIZATION_ENDPOINT + "?" + urlencode(params)

    def exchange(self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str,
                 verifier_request=None) -> GoogleIdentity:  # noqa: ANN001
        return self._exchange(code, redirect_uri, code_verifier, nonce, verifier_request)[0]

    def exchange_with_drive(self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str,
                            verifier_request=None) -> GoogleDriveLogin:  # noqa: ANN001
        identity, data = self._exchange(code, redirect_uri, code_verifier, nonce, verifier_request)
        refresh = data.get("refresh_token")
        if not drive_scope_granted(data.get("scope")) or not valid_oauth_token(refresh):
            return GoogleDriveLogin(identity)
        return GoogleDriveLogin(identity, refresh)

    def _exchange(self, code: str, redirect_uri: str, code_verifier: str, nonce: str,
                  verifier_request) -> tuple[GoogleIdentity, dict]:  # noqa: ANN001
        self._callback(redirect_uri)
        try:
            response = requests.post(
                TOKEN_ENDPOINT,
                data={"code": code, "client_id": self.config.client_id,
                      "client_secret": self.config.client_secret, "redirect_uri": redirect_uri,
                      "grant_type": "authorization_code", "code_verifier": code_verifier},
                timeout=10, allow_redirects=False,
            )
        except requests.RequestException:
            raise GoogleOIDCError(GoogleOIDCFailure.TOKEN_NETWORK) from None
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = None
            error = body.get("error") if isinstance(body, dict) else None
            if error in ("invalid_client", "unauthorized_client"):
                reason = GoogleOIDCFailure.TOKEN_CLIENT
            elif error == "invalid_grant":
                reason = GoogleOIDCFailure.TOKEN_GRANT
            else:
                reason = GoogleOIDCFailure.TOKEN_REJECTED
            raise GoogleOIDCError(reason)
        try:
            data = response.json()
            token = data["id_token"]
        except (ValueError, KeyError, TypeError):
            raise GoogleOIDCError(GoogleOIDCFailure.ID_TOKEN_MISSING) from None
        if not isinstance(token, str) or not token:
            raise GoogleOIDCError(GoogleOIDCFailure.ID_TOKEN_MISSING)
        try:
            claims = id_token.verify_oauth2_token(
                token, verifier_request or GoogleRequest(), audience=self.config.client_id
            )
        except (TransportError, requests.RequestException):
            raise GoogleOIDCError(GoogleOIDCFailure.CERT_NETWORK) from None
        except (ValueError, KeyError, TypeError, GoogleAuthError):
            raise GoogleOIDCError(GoogleOIDCFailure.ID_TOKEN_INVALID) from None
        return self._identity(claims, nonce), data

    def _identity(self, claims: dict, nonce: str) -> GoogleIdentity:
        subject, email, name = claims.get("sub"), claims.get("email"), claims.get("name")
        token_nonce = claims.get("nonce")
        if (claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
                or claims.get("aud") != self.config.client_id):
            raise GoogleOIDCError(GoogleOIDCFailure.ID_TOKEN_INVALID)
        if (not isinstance(token_nonce, str) or not nonce
                or not hmac.compare_digest(token_nonce.encode(), nonce.encode())):
            raise GoogleOIDCError(GoogleOIDCFailure.NONCE_MISMATCH)
        if claims.get("email_verified") is not True:
            raise GoogleOIDCError(GoogleOIDCFailure.EMAIL_UNVERIFIED)
        if claims.get("hd") != self.config.hosted_domain:
            raise GoogleOIDCError(GoogleOIDCFailure.DOMAIN_MISMATCH)
        if (not isinstance(subject, str) or not re.fullmatch(r"[!-~]{1,255}", subject)
                or not isinstance(email, str) or not email or len(email) > 320):
            raise GoogleOIDCError(GoogleOIDCFailure.PROFILE_INVALID)
        if not isinstance(name, str) or not name.strip():
            name = email
        return GoogleIdentity(subject=subject, email=email, name=name)
