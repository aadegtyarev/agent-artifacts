"""Google OIDC protocol adapter. Browser state and sessions belong to the caller."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
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


class GoogleOIDC:
    def __init__(self, config: WorkspaceConfig, public_url: str, raw_url: str = "") -> None:
        self.config = config
        self.callbacks = frozenset(url + CALLBACK_PATH for url in (public_url, raw_url) if url)

    def _callback(self, redirect_uri: str) -> None:
        if redirect_uri not in self.callbacks:
            raise GoogleOIDCError(GoogleOIDCFailure.INVALID_CALLBACK)

    def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str:
        self._callback(redirect_uri)
        return AUTHORIZATION_ENDPOINT + "?" + urlencode({
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "hd": self.config.hosted_domain,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })

    def exchange(self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str,
                 verifier_request=None) -> GoogleIdentity:  # noqa: ANN001
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
            token = response.json()["id_token"]
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
        return self._identity(claims, nonce)

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
