"""Workspace login and opaque sessions; instantiate only for enabled deployments.

Each controller is bound to one configured origin. Callers must route requests to
it by a trusted origin/host check, never by passing a request's Host as an origin.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .config import WorkspaceConfig
from .google_oidc import CALLBACK_PATH, GoogleIdentity, GoogleOIDC, GoogleOIDCError
from .store import ID_RE, TOKEN_RE

LOGIN_COOKIE = "__Host-aa-workspace-login"
SESSION_COOKIE = "__Host-aa-workspace-session"
LOGIN_TTL = 300
SESSION_TTL = 8 * 60 * 60
_RANDOM = re.compile(r"[A-Za-z0-9_-]{43}")
_MAIN_RETURN = re.compile(rf"(?:/a/{ID_RE}(?:/(?:raw|download))?|/p/{ID_RE}|/join/{TOKEN_RE})")
_RAW_RETURN = re.compile(rf"/a/{ID_RE}/raw")
_AUTH_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def same_origin(value: str, configured: str) -> bool:
    """Compare browser origins, including equivalent explicit HTTPS default ports."""
    if not value.isascii() or any(c in value for c in "\\\\?#@ \t\r\n"):
        return False
    try:
        received, expected = urlsplit(value), urlsplit(configured)
        received_port, expected_port = received.port, expected.port
        return (received.scheme == expected.scheme == "https" and bool(received.hostname)
                and received.hostname == expected.hostname and received.path == ""
                and not received.query and not received.fragment
                and not received.netloc.endswith(":")
                and (443 if received_port is None else received_port)
                == (443 if expected_port is None else expected_port))
    except ValueError:
        return False


def _page_marker(token: str) -> str:
    digest = hashlib.sha256(b"workspace-page-marker-v1\0" + token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _random() -> str:
    return secrets.token_urlsafe(32)


def _valid_token(value: str | None) -> bool:
    return bool(value and _RANDOM.fullmatch(value))


def _response(status: int, message: str) -> Response:
    return Response(message, status_code=status, headers=_AUTH_HEADERS)


def _cookie(response: Response, name: str, value: str, ttl: int) -> None:
    response.set_cookie(name, value, max_age=ttl, path="/", secure=True, httponly=True,
                        samesite="lax")


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")


class WorkspaceAuthStore:
    """A separate SQLite database, created only when Workspace is enabled."""

    def __init__(self, path: Path, policy: WorkspaceConfig, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        # Binding the token hash avoids accepting legacy rows without a policy.
        policy_key = json.dumps([policy.client_id, policy.hosted_domain], ensure_ascii=True)
        self.policy_namespace = hashlib.sha256(policy_key.encode("ascii")).digest()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS login_attempts (
                    state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
                    origin TEXT NOT NULL, nonce TEXT NOT NULL, verifier TEXT NOT NULL,
                    destination TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, origin TEXT NOT NULL, subject TEXT NOT NULL,
                    email TEXT NOT NULL, name TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS login_expires ON login_attempts(expires);
                CREATE INDEX IF NOT EXISTS session_expires ON sessions(expires);
            """)

    def _hash(self, value: str) -> str:
        return hashlib.sha256(b"workspace-policy-v1\0" + self.policy_namespace
                              + value.encode("ascii")).hexdigest()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def begin(self, state: str, browser: str, origin: str, nonce: str, verifier: str,
              destination: str) -> None:
        now = self.clock()
        with self._db() as db:
            db.execute("DELETE FROM login_attempts WHERE expires <= ?", (now,))
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            db.execute("INSERT INTO login_attempts VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (self._hash(state), self._hash(browser), origin, nonce, verifier,
                        destination, now + LOGIN_TTL))

    def consume(self, state: str, browser: str, origin: str) -> tuple[str, str, str] | None:
        # DELETE RETURNING makes concurrent callbacks one-use, before any network call.
        with self._db() as db:
            row = db.execute(
                "DELETE FROM login_attempts WHERE state_hash = ? AND browser_hash = ? "
                "AND origin = ? AND expires > ? RETURNING nonce, verifier, destination",
                (self._hash(state), self._hash(browser), origin, self.clock()),
            ).fetchone()
        return row

    def create_session(self, origin: str, identity: GoogleIdentity) -> str:
        token = _random()
        with self._db() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                       (self._hash(token), origin, identity.subject, identity.email, identity.name,
                        self.clock() + SESSION_TTL))
        return token

    def identity(self, token: str, origin: str) -> GoogleIdentity | None:
        with self._db() as db:
            row = db.execute(
                "SELECT subject, email, name FROM sessions "
                "WHERE token_hash = ? AND origin = ? AND expires > ?",
                (self._hash(token), origin, self.clock()),
            ).fetchone()
        return GoogleIdentity(*row) if row else None

    def logout(self, token: str, origin: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ? AND origin = ?",
                       (self._hash(token), origin))


class WorkspaceAuth:
    """Endpoints and session lookup for exactly one configured origin."""

    def __init__(self, store: WorkspaceAuthStore, oidc: GoogleOIDC, *, origin: str,
                 raw: bool = False) -> None:
        self.store = store
        self.oidc = oidc
        self.origin = origin
        self.raw = raw
        self.callback_uri = origin + CALLBACK_PATH
        if self.callback_uri not in oidc.callbacks:
            raise ValueError("origin is not a configured OIDC callback origin")

    def _destination(self, value: str) -> bool:
        pattern = _RAW_RETURN if self.raw else _MAIN_RETURN
        return bool(len(value) <= 256 and pattern.fullmatch(value))

    async def login(self, request: Request) -> Response:
        values = request.query_params.getlist("return")
        if len(values) != 1 or not self._destination(values[0]):
            return _response(400, "Invalid return path")
        state, browser, nonce, verifier = _random(), _random(), _random(), _random()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        await run_in_threadpool(self.store.begin, state, browser, self.origin, nonce, verifier,
                                values[0])
        url = self.oidc.authorization_url(redirect_uri=self.callback_uri, state=state, nonce=nonce,
                                          code_challenge=challenge.rstrip(b"=").decode("ascii"))
        response = RedirectResponse(url, status_code=303, headers=_AUTH_HEADERS)
        _cookie(response, LOGIN_COOKIE, browser, LOGIN_TTL)
        return response

    async def callback(self, request: Request) -> Response:
        states, codes = request.query_params.getlist("state"), request.query_params.getlist("code")
        browser = request.cookies.get(LOGIN_COOKIE)
        if (len(states) != 1 or not _valid_token(states[0]) or not _valid_token(browser)
                or len(codes) != 1 or not codes[0] or len(codes[0]) > 4096
                or request.query_params.getlist("error")):
            return _response(400, "Invalid login")
        attempt = await run_in_threadpool(self.store.consume, states[0], browser, self.origin)
        if attempt is None:
            return _response(400, "Invalid login")
        nonce, verifier, destination = attempt
        try:
            identity = await run_in_threadpool(
                self.oidc.exchange, code=codes[0], redirect_uri=self.callback_uri,
                code_verifier=verifier, nonce=nonce,
            )
        except GoogleOIDCError:
            response = _response(401, "Login failed")
            _clear_cookie(response, LOGIN_COOKIE)
            return response
        token = await run_in_threadpool(self.store.create_session, self.origin, identity)
        response = RedirectResponse(destination, status_code=303, headers=_AUTH_HEADERS)
        _clear_cookie(response, LOGIN_COOKIE)
        _cookie(response, SESSION_COOKIE, token, SESSION_TTL)
        return response

    async def identity(self, request: Request) -> GoogleIdentity | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not _valid_token(token):
            return None
        return await run_in_threadpool(self.store.identity, token, self.origin)

    def page_marker(self, request: Request) -> str:
        # Only call after identity() has verified this request's session cookie.
        return _page_marker(request.cookies[SESSION_COOKIE])

    async def logout(self, request: Request) -> Response:
        origins = request.headers.getlist("origin")
        if len(origins) != 1 or not same_origin(origins[0], self.origin):
            return _response(403, "Invalid origin")
        token = request.cookies.get(SESSION_COOKIE)
        if _valid_token(token):
            await run_in_threadpool(self.store.logout, token, self.origin)
        response = _response(204, "")
        _clear_cookie(response, SESSION_COOKIE)
        return response
