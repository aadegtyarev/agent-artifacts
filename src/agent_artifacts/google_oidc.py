"""Google OIDC protocol adapter. Browser state and sessions belong to the caller."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from .config import WorkspaceConfig

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
CALLBACK_PATH = "/auth/callback"


class GoogleOIDCError(ValueError):
    """The exchange or verified identity was rejected; never includes token data."""


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
            raise GoogleOIDCError("invalid callback")

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
            if response.status_code != 200:
                raise GoogleOIDCError("token exchange rejected")
            token = response.json()["id_token"]
            if not isinstance(token, str) or not token:
                raise GoogleOIDCError("missing ID token")
            claims = id_token.verify_oauth2_token(
                token, verifier_request or GoogleRequest(), audience=self.config.client_id
            )
        except GoogleOIDCError:
            raise
        except (ValueError, KeyError, TypeError, requests.RequestException, GoogleAuthError):
            raise GoogleOIDCError("token verification failed") from None
        return self._identity(claims, nonce)

    def _identity(self, claims: dict, nonce: str) -> GoogleIdentity:
        subject, email, name = claims.get("sub"), claims.get("email"), claims.get("name")
        token_nonce = claims.get("nonce")
        if (claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
                or claims.get("aud") != self.config.client_id
                or not isinstance(token_nonce, str) or not nonce
                or not hmac.compare_digest(token_nonce.encode(), nonce.encode())
                or claims.get("email_verified") is not True
                or claims.get("hd") != self.config.hosted_domain
                or not isinstance(subject, str) or not re.fullmatch(r"[!-~]{1,255}", subject)
                or not isinstance(email, str) or not email or len(email) > 320):
            raise GoogleOIDCError("identity claims rejected")
        if not isinstance(name, str) or not name.strip():
            name = email
        return GoogleIdentity(subject=subject, email=email, name=name)
