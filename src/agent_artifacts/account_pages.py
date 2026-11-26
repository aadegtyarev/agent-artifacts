"""Shared browser account routes. Only the entry and session provider differ."""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import parse_qsl

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import pages
from .accounts import CLAIM_TTL, DEVICE_TTL, AccountFull, AgentNameUnavailable, ClaimPreview, ClaimUnavailable, Device
from .api import error, iso
from .config import Config
from .google_oidc import GoogleIdentity
from .store import ID_RE, NAME_RE, Store, hash_key
from .trash import project_delete_scope
from .workspace_auth import WorkspaceAuth, same_origin

DEVICE_COOKIE = "__Host-aa-account-device"
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "same-origin"}
_FORM_LIMIT = 1024


@dataclass(frozen=True)
class Principal:
    account_id: str
    device: Device | None = None
    google: GoogleIdentity | None = None


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303, headers=_HEADERS)


def _remember(response: Response, token: str) -> None:
    response.set_cookie(DEVICE_COOKIE, token, max_age=DEVICE_TTL, path="/", secure=True,
                        httponly=True, samesite="lax")


def _clear(response: Response) -> None:
    response.delete_cookie(DEVICE_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


async def _form(request: Request, allowed: set[str]) -> dict[str, str] | None:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        return None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _FORM_LIMIT:
            return None
        body += chunk
    try:
        items = parse_qsl(body.decode("utf-8"), keep_blank_values=False, strict_parsing=True,
                          max_num_fields=len(allowed), errors="strict")
    except (ValueError, UnicodeError):
        return None
    values = dict(items)
    return values if len(values) == len(items) and values.keys() <= allowed else None


def _client_bucket(request: Request) -> str:
    if request.client:
        try:
            return str(ipaddress.ip_address(request.client.host))
        except ValueError:
            pass
    return "unknown"


class AccountPages:
    def __init__(self, store: Store, config: Config, workspace: WorkspaceAuth | None = None) -> None:
        self.store, self.accounts = store, store.accounts
        self.origin = config.public_url.rstrip("/")
        self.workspace = workspace
        self.google_enabled = workspace is not None

    def routes(self) -> list[Route]:
        routes = [
            Route("/account", self.account),
            Route("/account/claim", self.claim, methods=["GET", "POST"]),
            Route("/account/devices/sign-out", self.sign_out, methods=["POST"]),
            Route("/account/agents/key", self.issue_key, methods=["POST"]),
            Route("/account/agents/revoke", self.revoke_key, methods=["POST"]),
            Route("/account/delete", self.delete, methods=["POST"]),
            Route("/account/undo", self.undo, methods=["POST"]),
            Route("/api/v1/me/account-claim", self.issue_claim, methods=["POST"]),
        ]
        if not self.google_enabled:
            routes.extend([
                Route("/", self.home),
                Route("/account/connect", self.connect, methods=["POST"]),
                Route("/account/devices/connect", self.offer_pairing, methods=["POST"]),
                Route("/account/recovery/key", self.issue_recovery, methods=["POST"]),
                Route("/account/recover", self.recover, methods=["GET", "POST"]),
            ])
        return routes

    def _same_origin(self, request: Request) -> bool:
        origins = request.headers.getlist("origin")
        return len(origins) == 1 and same_origin(origins[0], self.origin)

    def _forbidden(self) -> Response:
        return Response(status_code=403, headers=_HEADERS)

    async def principal(self, request: Request) -> Principal | None:
        if self.google_enabled:
            identity = getattr(request.state, "workspace_identity", None)
            if identity is None:
                return None
            return Principal(await run_in_threadpool(self.accounts.google_account, identity.subject), google=identity)
        device = await run_in_threadpool(self.accounts.device, request.cookies.get(DEVICE_COOKIE))
        return Principal(device.account_id, device=device) if device else None

    async def home(self, request: Request) -> Response:
        if await self.principal(request):
            return _redirect("/account")
        response = await self._connect_page()
        if DEVICE_COOKIE in request.cookies:
            _clear(response)
        return response

    async def _connect_page(self, *, notice: str | None = None, status: int = 200) -> Response:
        allow_code = await run_in_threadpool(self.accounts.has_pairing)
        return pages.account_page("account-entry.html", status, title="Connect a device", view="connect",
                                  allow_code=allow_code, notice=notice)

    async def connect(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        if await self.principal(request):
            return _redirect("/account")
        values = await _form(request, {"code"})
        if not values or "code" not in values:
            return await self._connect_page(notice="Enter the connection code.", status=400)
        result = await run_in_threadpool(self.accounts.redeem_pairing, values["code"], client=_client_bucket(request))
        if result.status == "limited":
            response = await self._connect_page(notice="Too many tries. Wait a minute and try again.", status=429)
            response.headers["Retry-After"] = "60"
            return response
        if result.status != "connected":
            notice = ("Sign out another device before connecting this one." if result.status == "full"
                      else "The code did not work. Check it or ask your other device for a new one.")
            return await self._connect_page(notice=notice, status=400)
        assert result.device_token
        response = _redirect("/account")
        _remember(response, result.device_token)
        return response

    async def issue_claim(self, request: Request) -> Response:
        values = request.headers.getlist("authorization")
        scheme, _, key = values[0].partition(" ") if len(values) == 1 else ("", "", "")
        if scheme.lower() != "bearer" or not key.strip() or len(key) > 512:
            return error(401, "unauthorized", "missing or unknown agent key")
        try:
            token = await run_in_threadpool(self.accounts.issue_claim, hash_key(key.strip()))
        except ClaimUnavailable:
            return error(409, "agent_already_linked", "this agent already belongs to an account")
        if token is None:
            return error(401, "unauthorized", "missing or unknown agent key")
        return JSONResponse({"code": token, "url": self.origin + "/account/claim", "expires_in": CLAIM_TTL},
                            status_code=201, headers=_HEADERS)

    async def claim(self, request: Request) -> Response:
        if request.method == "POST" and not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if self.google_enabled and principal is None:
            return _redirect("/auth/login?return=/account/claim")
        if request.method in ("GET", "HEAD"):
            return await self._claim_page(request, principal)
        values = await _form(request, {"code", "confirm", "proof"}) or {}
        token = values.get("code", "").strip()
        if values.keys() == {"code"}:
            preview = await run_in_threadpool(self.accounts.claim_preview, token, principal.account_id if principal else None)
            if preview:
                if request.headers.get("accept") == "application/json":
                    return JSONResponse({"agent": preview.agent, "proof": preview.proof}, headers=_HEADERS)
                return await self._claim_page(request, principal, preview=preview, token=token)
            message = ("Use the setup code, not the agent's key." if re.fullmatch(r"aa_[A-Za-z0-9_-]{43}", token)
                       else "The setup code did not work. Ask your agent for a new one.")
            if request.headers.get("accept") == "application/json":
                return JSONResponse({"message": message}, status_code=400, headers=_HEADERS)
            return await self._claim_page(request, principal, notice=message, status=400)
        if values.keys() != {"code", "confirm", "proof"} or values.get("confirm") != "1":
            return await self._claim_page(request, principal, notice="Check the setup code again.", status=400)
        try:
            result = await run_in_threadpool(
                self.accounts.claim_agent, token, device_token=request.cookies.get(DEVICE_COOKIE) if not self.google_enabled else None,
                google_subject=principal.google.subject if principal and principal.google else None,
                proof=values["proof"],
            )
        except ClaimUnavailable:
            return await self._claim_page(request, principal, notice="Could not link the agent. Check the code and account again.", status=409)
        except AccountFull:
            return await self._claim_page(request, principal, notice="This account already has the maximum number of agents.", status=409)
        response = _redirect("/account?linked=1#account-agents")
        if result.device_token:
            _remember(response, result.device_token)
        return response

    async def _claim_page(self, request: Request, principal: Principal | None, *, preview: ClaimPreview | None = None,
                          token: str | None = None, notice: str | None = None, status: int = 200) -> Response:
        if principal:
            return await self._render_account(request, principal, notice=notice, status=status, setup_mode="claim",
                                              claim_preview=preview, claim_code=token)
        return pages.account_page("account-entry.html", status, title="Link an agent", view="claim", account_setup=True,
                                  signed_in=False, claim_preview=preview, claim_code=token, notice=notice)

    async def account(self, request: Request) -> Response:
        principal = await self.principal(request)
        if not principal:
            return _redirect("/auth/login?return=/account" if self.google_enabled else "/")
        notice = ("Restored." if request.query_params.get("restored") == "1" else
                  "Agent linked." if request.query_params.get("linked") == "1" else None)
        return await self._render_account(request, principal, notice=notice)

    async def _render_account(self, request: Request, principal: Principal, *, code: str | None = None,
                              notice: str | None = None, status: int = 200, setup_mode: str | None = None,
                              claim_preview: ClaimPreview | None = None, claim_code: str | None = None,
                              issued_name: str | None = None, setup: str | None = None) -> Response:
        def collect():
            agents = self.accounts.linked_agents(principal.account_id)
            projects, standalone, grouped = [], [], defaultdict(list)
            for agent in agents:
                for a in self.store.list(agent.name):
                    (grouped[a.project] if a.project else standalone).append(a)
                projects.extend(self.store.projects(agent.name))
            has_recovery = False
            if principal.device:
                token = request.cookies[DEVICE_COOKIE]
                devices = [{"id": d.id, "current": d.id == principal.device.id, "since": iso(d.created_at, human=True)}
                           for d in self.accounts.devices(token)]
                has_recovery = self.accounts.has_recovery_code(token)
            else:
                assert self.workspace and principal.google
                devices = [{"id": d.id, "current": d.current, "since": iso(d.created_at, human=True)}
                           for d in self.workspace.devices(request, principal.google.subject)]
            pending = self.store.trash.pending(principal.account_id)
            now = self.store.clock()
            deletions = [(d, int((d.deadline - now) * 1000)) for d in pending if d.can_undo and d.deadline > now]
            return {
                "agents": agents, "projects": [(p, grouped[p.id]) for p in projects],
                "project_scopes": {p.id: project_delete_scope(p.title, (a.id for a in grouped[p.id])).fingerprint
                                   for p in projects},
                "standalone": standalone, "devices": devices, "has_recovery": has_recovery,
                "deletions": deletions, "suggested_name": self.accounts.suggested_agent_name(),
            }

        context = await run_in_threadpool(collect)
        return pages.account_page("account.html", status, title="Your account", pairing=not self.google_enabled,
                                  code=code, notice=notice, email=principal.google.email if principal.google else None,
                                  setup_mode=setup_mode, claim_preview=claim_preview, claim_code=claim_code,
                                  issued_name=issued_name, setup=setup, signed_in=True, copy=True,
                                  origin=self.origin, account_dashboard=True, account_setup=True, **context)

    @staticmethod
    def _binding(request: Request, principal: Principal) -> dict[str, str | None]:
        return {
            "device_token": request.cookies.get(DEVICE_COOKIE) if principal.device else None,
            "google_subject": principal.google.subject if principal.google else None,
        }

    async def delete(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if not principal:
            return _redirect("/")
        values = await _form(request, {"kind", "target", "scope"}) or {}
        kind, target = values.get("kind"), values.get("target", "")
        scope = values.get("scope")
        if kind not in ("file", "project") or not re.fullmatch(ID_RE, target) or (
            scope is not None and (kind != "project" or not re.fullmatch(r"[a-f0-9]{64}", scope))
        ):
            return pages.message(404, "File or project not found.")
        result = await run_in_threadpool(self.store.trash.delete, kind, target, fingerprint=scope,
                                         **self._binding(request, principal))
        if result.status == "missing":
            return pages.message(404, "File or project not found.")
        if result.status == "retained":
            return await self._render_account(request, principal, status=409,
                                              notice="Drive files cannot be deleted here yet.")
        if result.status in ("confirm", "changed"):
            return await self._render_account(request, principal, status=409,
                                              notice="The project changed. Check its files before deleting it.")
        return _redirect(f"/account#account-undo-{result.deletion_id}")

    async def undo(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if not principal:
            return _redirect("/")
        values = await _form(request, {"deletion"}) or {}
        deletion = values.get("deletion", "")
        if not re.fullmatch(ID_RE, deletion):
            return pages.message(404, "That deletion can no longer be undone.")
        result = await run_in_threadpool(self.store.trash.undo, deletion, **self._binding(request, principal))
        if result == "restored":
            return _redirect("/account?restored=1")
        if result == "project_hidden":
            return await self._render_account(request, principal, status=409, notice="Restore the project first.")
        return await self._render_account(request, principal, status=404, notice="That deletion can no longer be undone.")

    async def issue_key(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if not principal:
            return _redirect("/")
        as_json = request.headers.get("accept") == "application/json"

        async def failed(message: str, status: int) -> Response:
            if as_json:
                return JSONResponse({"message": message}, status_code=status, headers=_HEADERS)
            return await self._render_account(request, principal, status=status, notice=message, setup_mode="new")

        values = await _form(request, {"name", "purpose"}) or {}
        name = values.get("name", "").strip()
        purpose = values.get("purpose", "new")
        if purpose not in ("new", "reissue") or not re.fullmatch(NAME_RE, name):
            return await failed("Use 1 to 64 letters, numbers, dots, hyphens or underscores for the agent name.", 400)
        try:
            key = await run_in_threadpool(self.accounts.issue_agent_key, name, reissue=purpose == "reissue",
                                          **self._binding(request, principal))
        except AgentNameUnavailable:
            return await failed("This agent name is not available. Check Agent keys or choose another name.", 409)
        except AccountFull:
            return await failed("This account already has the maximum number of agents.", 409)
        if key is None:
            return _redirect("/")
        setup = pages.key_setup(key, self.origin)
        if as_json:
            return JSONResponse({"name": name, "setup": setup}, status_code=201, headers=_HEADERS)
        return await self._render_account(request, principal, setup_mode="new", issued_name=name, setup=setup)

    async def revoke_key(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if not principal:
            return _redirect("/")
        values = await _form(request, {"name", "confirm"})
        agents = await run_in_threadpool(self.accounts.linked_agents, principal.account_id)
        if not values or not any(a.name == values.get("name") and a.active and not a.admin for a in agents):
            return pages.message(404, "Key not found.")
        name = values["name"]
        if values.get("confirm") != "1":
            return pages.account_page("account-revoke-key.html", 200, title="Revoke this key?", agent=name)
        if not await run_in_threadpool(self.accounts.revoke_agent_key, name, **self._binding(request, principal)):
            return pages.message(404, "Key not found.")
        return _redirect("/account")

    async def issue_recovery(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if not principal or not principal.device:
            return _redirect("/")
        code = await run_in_threadpool(self.accounts.issue_recovery_code, request.cookies[DEVICE_COOKIE])
        if not code:
            return _redirect("/")
        return pages.account_page("account-recovery-code.html", 200, title="Save your recovery code", copy=True, code=code)

    async def recover(self, request: Request) -> Response:
        if request.method == "POST" and not self._same_origin(request):
            return self._forbidden()
        if await self.principal(request):
            return _redirect("/account")
        context = {"title": "Restore access to your account", "view": "recover"}
        if request.method in ("GET", "HEAD"):
            return pages.account_page("account-entry.html", 200, **context)
        values = await _form(request, {"code"})
        code = values.get("code", "").strip() if values else ""
        token = await run_in_threadpool(self.accounts.recover, code)
        if not token:
            return pages.account_page("account-entry.html", 400, **context, notice="The recovery code did not work. Check your saved code.")
        response = _redirect("/account")
        _remember(response, token)
        return response

    async def offer_pairing(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if not principal or not principal.device:
            return _redirect("/")
        code = await run_in_threadpool(self.accounts.offer_pairing, request.cookies[DEVICE_COOKIE])
        if not code:
            return _redirect("/")
        return await self._render_account(request, principal, code=code)

    async def sign_out(self, request: Request) -> Response:
        if not self._same_origin(request):
            return self._forbidden()
        principal = await self.principal(request)
        if not principal:
            return _redirect("/")
        values = await _form(request, {"device"})
        id_ = values.get("device", "") if values else ""
        if not re.fullmatch(ID_RE, id_):
            return pages.message(404, "Device not found.")
        if self.workspace and principal.google:
            current = await run_in_threadpool(self.workspace.revoke_device, request, principal.google.subject, id_)
            if current is None:
                return pages.message(404, "Device not found.")
            if current:
                response = _redirect("/auth/signed-out")
                self.workspace.clear_session(response)
                return response
            return _redirect("/account")
        assert principal.device
        if not await run_in_threadpool(
            self.accounts.revoke_device, request.cookies[DEVICE_COOKIE], id_,
        ):
            return pages.message(404, "Device not found.")
        if id_ == principal.device.id:
            response = _redirect("/")
            _clear(response)
            return response
        return _redirect("/account")
