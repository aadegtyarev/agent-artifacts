"""Workspace login and opaque sessions; instantiate only for enabled deployments.

Each controller is bound to one configured origin. Callers must route requests to
it by a trusted origin/host check, never by passing a request's Host as an origin.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import LiteralString
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import pages
from .config import WorkspaceConfig
from .drive_grants import DriveGrantStoreError, WorkspaceDriveGrants
from .google_oidc import (
    CALLBACK_PATH, GoogleIdentity, GoogleOIDC, GoogleOIDCError, GoogleOIDCFailure,
)
from .store import ID_RE, TOKEN_RE

log = logging.getLogger(__name__)

LOGIN_COOKIE = "__Host-aa-workspace-login"
SESSION_COOKIE = "__Host-aa-workspace-session"
LOGIN_TTL = 300
SESSION_TTL = 8 * 60 * 60
_RANDOM = re.compile(r"[A-Za-z0-9_-]{43}")
_MAIN_RETURN = re.compile(
    rf"(?:/|/a/{ID_RE}(?:/versions/{ID_RE})?(?:/(?:raw|download))?|/p/{ID_RE}|/join/{TOKEN_RE})"
)
_RAW_RETURN = re.compile(rf"/a/{ID_RE}/raw")
ACCOUNT_ENTRY_PATHS = frozenset(("/account", "/account/claim"))
_AUTH_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
_DRIVE_LOGIN = "wdrive."
_DRIVE_RENEW = "wdrive-renew."


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


def _device_id(token_hash: str) -> str:
    # A list item identifier, not the session token, its stored hash, or the
    # page marker. Only list or remove devices of a verified subject.
    digest = hashlib.sha256(b"workspace-device-v1\0" + token_hash.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class WorkspaceDevice:
    id: str
    current: bool
    created_at: float


class WorkspaceAuthStore:
    """A separate SQLite database, created only when Workspace is enabled."""

    def __init__(self, path: Path, policy: WorkspaceConfig, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self.hosted_domain = policy.hosted_domain
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

    def devices(self, token: str, origin: str, subject: str) -> list[WorkspaceDevice]:
        current = self._hash(token)
        with self._db() as db:
            rows = db.execute("SELECT token_hash, expires FROM sessions WHERE origin = ? AND subject = ? "
                              "AND expires > ? ORDER BY expires, token_hash",
                              (origin, subject, self.clock())).fetchall()
        if not any(key == current for key, _ in rows):
            return []
        return [WorkspaceDevice(_device_id(key), key == current, expires - SESSION_TTL) for key, expires in rows]

    def revoke_device(self, token: str, origin: str, subject: str, device_id: str) -> bool | None:
        current = self._hash(token)
        now = self.clock()
        with self._db() as db:
            rows = db.execute("SELECT token_hash FROM sessions WHERE origin = ? AND subject = ? AND expires > ?",
                              (origin, subject, now)).fetchall()
            target = next((key for (key,) in rows if _device_id(key) == device_id), None)
            if target is None:
                return None
            removed = db.execute(
                "DELETE FROM sessions WHERE token_hash = ? AND origin = ? AND subject = ? AND expires > ? "
                "AND EXISTS (SELECT 1 FROM sessions WHERE token_hash = ? AND origin = ? AND subject = ? AND expires > ?)",
                (target, origin, subject, now, current, origin, subject, now),
            ).rowcount
        return (target == current) if removed else None


class WorkspaceAuth:
    """Endpoints and session lookup for exactly one configured origin."""

    def __init__(self, store: WorkspaceAuthStore, oidc: GoogleOIDC, *, origin: str,
                 raw: bool = False, accounts: bool = False,
                 drive_grants: WorkspaceDriveGrants | None = None) -> None:
        if raw and drive_grants is not None:
            raise ValueError("the raw origin cannot request Drive grants")
        self.store = store
        self.oidc = oidc
        self.origin = origin
        self.raw = raw
        self.accounts = accounts and not raw
        self.drive_grants = drive_grants
        self.callback_uri = origin + CALLBACK_PATH
        if self.callback_uri not in oidc.callbacks:
            raise ValueError("origin is not a configured OIDC callback origin")

    def _destination(self, value: str) -> bool:
        pattern = _RAW_RETURN if self.raw else _MAIN_RETURN
        return bool(len(value) <= 256 and (pattern.fullmatch(value)
                    or (self.accounts and value in ACCOUNT_ENTRY_PATHS)))

    async def sign_in(self, request: Request) -> Response:
        path = request.url.path
        values = request.query_params.getlist("return") if path == "/auth/login" else []
        if len(values) > 1 or (values and not self._destination(values[0])):
            return self._sign_in_failed(400)
        destination = values[0] if values else "/account" if self.accounts else "/"
        identity = await self.identity(request)
        if identity and destination != "/":
            return RedirectResponse(destination, status_code=303, headers=_AUTH_HEADERS)
        if identity:
            return pages.sign_in_page(self.store.hosted_domain, destination, email=identity.email,
                                      title="Signed in", account_mode=self.accounts)
        title = "Signed out" if path == "/auth/signed-out" else "Sign in"
        return pages.sign_in_page(self.store.hosted_domain, destination, title=title, account_mode=self.accounts)

    def _sign_in_failed(self, status: int, destination: str = "/") -> Response:
        if self.raw:
            return _response(status, "Login failed" if status == 401 else "Invalid login")
        return pages.sign_in_page(self.store.hosted_domain, destination, title="Could not sign in",
                                  status=status, account_mode=self.accounts)

    async def drive_access(self, request: Request) -> Response:
        values = request.query_params.getlist("return")
        if self.drive_grants is None or len(values) != 1 or not self._destination(values[0]):
            return _response(404, "Not found")
        if await self.identity(request) is None:
            return pages.sign_in_page(self.store.hosted_domain, values[0], account_mode=self.accounts)
        return self._drive_consent_page(values[0], 403, "Google Drive access needed")

    def _drive_consent_page(self, destination: str, status: int, title: str) -> HTMLResponse:
        return pages.sign_in_page(self.store.hosted_domain, destination, title=title, status=status,
                                  account_mode=self.accounts, drive_consent=True)

    async def login(self, request: Request) -> Response:
        values = request.query_params.getlist("return")
        if len(values) != 1 or not self._destination(values[0]):
            return _response(400, "Invalid return path")
        consent = request.query_params.getlist("consent")
        if consent and (consent != ["drive"] or self.drive_grants is None):
            return _response(400, "Invalid authorization request")
        renew = bool(consent)
        if renew and await self.identity(request) is None:
            return _response(401, "Sign in before renewing Google Drive access.")
        prefix = _DRIVE_RENEW if renew else _DRIVE_LOGIN if self.drive_grants is not None else ""
        state, browser, nonce, verifier = prefix + _random(), _random(), _random(), _random()
        bound_state = state + "." + self.page_marker(request) if renew else state
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        await run_in_threadpool(self.store.begin, bound_state, browser, self.origin, nonce, verifier, values[0])
        options = {"drive": True, "consent": renew} if self.drive_grants is not None else {}
        url = self.oidc.authorization_url(redirect_uri=self.callback_uri, state=state, nonce=nonce,
                                          code_challenge=challenge.rstrip(b"=").decode("ascii"), **options)
        response = RedirectResponse(url, status_code=303, headers=_AUTH_HEADERS)
        _cookie(response, LOGIN_COOKIE, browser, LOGIN_TTL)
        return response

    async def callback(self, request: Request) -> Response:
        started = time.monotonic()
        renew = False

        def fail(status: int, reason: LiteralString | GoogleOIDCFailure,
                 destination: str | None = None) -> Response:
            # Labels and timing only: never log request data or provider errors.
            log.warning("Workspace sign-in failed: %s (%d ms)", reason,
                        round((time.monotonic() - started) * 1000))
            response = (self._drive_consent_page(destination, status, "Could not authorize Google Drive")
                        if renew and destination is not None else self._sign_in_failed(status, destination or "/"))
            if destination is not None:
                _clear_cookie(response, LOGIN_COOKIE)
            return response

        states, codes = request.query_params.getlist("state"), request.query_params.getlist("code")
        errors = request.query_params.getlist("error")
        denied = len(errors) == 1 and not codes
        browser = request.cookies.get(LOGIN_COOKIE)
        if len(states) != 1:
            return fail(400, "callback_state_invalid")
        state = states[0]
        renew = state.startswith(_DRIVE_RENEW)
        prefix = _DRIVE_RENEW if renew else _DRIVE_LOGIN if state.startswith(_DRIVE_LOGIN) else ""
        if not _valid_token(state[len(prefix):]) or bool(prefix) != (self.drive_grants is not None):
            return fail(400, "callback_state_invalid")
        if not _valid_token(browser):
            return fail(400, "callback_browser_cookie_missing_or_invalid")
        if not (denied or (not errors and len(codes) == 1 and 0 < len(codes[0]) <= 4096)):
            return fail(400, "callback_result_invalid")
        current = await self.identity(request) if renew else None
        if renew and current is None:
            return fail(400, "drive_renew_session_missing")
        bound_state = state + "." + self.page_marker(request) if renew else state
        attempt = await run_in_threadpool(self.store.consume, bound_state, browser, self.origin)
        if attempt is None:
            return fail(400, "callback_attempt_not_found_or_expired")
        nonce, verifier, destination = attempt
        if denied:
            if errors[0] in ("org_internal", "admin_policy_enforced"):
                return fail(400, "google_organization_policy", destination)
            if errors[0] == "access_denied":
                return fail(400, "google_access_denied", destination)
            return fail(400, "google_authorization_error", destination)
        callback = {"code": codes[0], "redirect_uri": self.callback_uri,
                    "code_verifier": verifier, "nonce": nonce}
        try:
            if self.drive_grants is None:
                identity = await run_in_threadpool(self.oidc.exchange, **callback)
            else:
                login = await run_in_threadpool(self.oidc.exchange_with_drive, **callback)
                identity = login.identity
                if current is not None and identity.subject != current.subject:
                    return fail(403, "drive_renew_subject_changed", destination)
                if login.refresh_token is not None:
                    try:
                        await run_in_threadpool(self.drive_grants.put, identity.subject, login.refresh_token)
                    except DriveGrantStoreError:
                        log.warning("Workspace Drive grant not saved: store unavailable")
                        if renew:
                            return fail(503, "drive_grant_store_unavailable", destination)
                elif renew:
                    return fail(403, "drive_renew_missing_grant", destination)
        except GoogleOIDCError as e:
            return fail(401, e.reason, destination)
        if renew:
            response = RedirectResponse(destination, status_code=303, headers=_AUTH_HEADERS)
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

    def devices(self, request: Request, subject: str) -> list[WorkspaceDevice]:
        token = request.cookies.get(SESSION_COOKIE)
        return self.store.devices(token, self.origin, subject) if _valid_token(token) else []

    def revoke_device(self, request: Request, subject: str, device_id: str) -> bool | None:
        token = request.cookies.get(SESSION_COOKIE)
        return self.store.revoke_device(token, self.origin, subject, device_id) if _valid_token(token) else None

    def clear_session(self, response: Response) -> None:
        _clear_cookie(response, SESSION_COOKIE)

    async def logout(self, request: Request) -> Response:
        origins = request.headers.getlist("origin")
        if len(origins) != 1 or not same_origin(origins[0], self.origin):
            return _response(403, "Invalid origin")
        token = request.cookies.get(SESSION_COOKIE)
        if _valid_token(token):
            await run_in_threadpool(self.store.logout, token, self.origin)
        response = (RedirectResponse("/auth/signed-out", status_code=303, headers=_AUTH_HEADERS)
                    if not self.raw and request.query_params.getlist("view") == ["sign-in"]
                    else _response(204, ""))
        _clear_cookie(response, SESSION_COOKIE)
        return response
