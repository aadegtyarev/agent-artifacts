"""Admin API: people, their space, the service settings. Admin keys only.

Sizes are bytes and times are seconds, as everywhere in the API.
"""

from __future__ import annotations

import re
import shutil

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .api import ApiError, iso, json_body, require_admin, user_json
from .config import MB
from .store import NAME_RE, Invite, NameTaken, Store

HOUR = 3600
DAY = 24 * HOUR
# Lower bounds, so no setting rounds down to nothing; upper bounds on time.
MIN_BYTES, MIN_SECONDS = MB, HOUR
MAX_BYTES = 2**50  # a petabyte; SQLite integers end at 2**63
MAX_TTL_SECONDS = 30 * DAY
INVITE_TTL_SECONDS = 7 * DAY
# Left free on the disk for the database and its journal: on a full disk even
# the cleanup could not write.
RESERVE_BYTES = 256 * MB


def admin_routes(store: Store, public_url: str) -> list[Route]:
    def common_quota() -> int | None:
        return store.settings()["user_quota_bytes"]

    def person(name: str) -> dict:
        info = store.agent_info(name) if re.fullmatch(NAME_RE, name) else None
        if info is None:
            raise ApiError(404, "not_found", "no such user")
        return user_json(info, common_quota())

    async def users(request: Request) -> Response:
        require_admin(store, request)
        common = common_quota()
        infos = await run_in_threadpool(store.agent_infos)
        return JSONResponse([user_json(i, common) for i in infos])

    async def user(request: Request) -> Response:
        admin = require_admin(store, request)
        name = request.path_params["name"]
        await run_in_threadpool(person, name)  # 404 for an unknown name
        if request.method == "DELETE":
            if name == admin:
                raise ApiError(400, "bad_request", "an admin cannot remove its own key")
            await run_in_threadpool(store.remove_agent, name)
            return Response(status_code=204)
        data = await json_body(request, {"quota_bytes"}, {"quota_bytes"}, strict=False)
        quota = data["quota_bytes"]
        if quota is not None:
            quota = _int(quota, "quota_bytes", MIN_BYTES, MAX_BYTES)
        await run_in_threadpool(store.set_quota, name, quota)
        return JSONResponse(await run_in_threadpool(person, name))

    def disk_free() -> int:
        return shutil.disk_usage(store.files).free

    async def stats(request: Request) -> Response:
        require_admin(store, request)
        infos = await run_in_threadpool(store.agent_infos)
        return JSONResponse(
            {
                "users": len(infos),
                "files": sum(i.files for i in infos),
                "used_bytes": sum(i.used_bytes for i in infos),
                "disk_free_bytes": await run_in_threadpool(disk_free),
            }
        )

    async def settings(request: Request) -> Response:
        require_admin(store, request)
        if request.method == "PATCH":
            data = await json_body(
                request, {"user_quota_bytes", "max_total_bytes", "ttl_seconds"}, strict=False
            )
            values = {}
            if "user_quota_bytes" in data:
                values["user_quota_bytes"] = _int(
                    data["user_quota_bytes"], "user_quota_bytes", MIN_BYTES, MAX_BYTES
                )
            if "max_total_bytes" in data:
                total = _int(data["max_total_bytes"], "max_total_bytes", MIN_BYTES, MAX_BYTES)
                infos = await run_in_threadpool(store.agent_infos)
                free = await run_in_threadpool(disk_free)
                room = sum(i.used_bytes for i in infos) + free - max(RESERVE_BYTES, free // 20)
                if total > room:
                    raise ApiError(
                        400, "bad_request", f"the disk has room for {room} bytes at most"
                    )
                values["max_total_bytes"] = total
            if "ttl_seconds" in data:
                values["ttl_seconds"] = _int(
                    data["ttl_seconds"], "ttl_seconds", MIN_SECONDS, MAX_TTL_SECONDS
                )
            await run_in_threadpool(lambda: store.set_settings(**values))
        return JSONResponse(await run_in_threadpool(store.settings))

    async def invites(request: Request) -> Response:
        admin = require_admin(store, request)
        if request.method == "GET":
            found = await run_in_threadpool(store.invites)
            return JSONResponse([_invite_json(i) for i in found])
        data = await json_body(request, {"name", "ttl_seconds"}, {"name"}, strict=False)
        name = data["name"]
        if not isinstance(name, str) or not re.fullmatch(NAME_RE, name):
            raise ApiError(
                400,
                "bad_request",
                "name may use letters, digits, '.', '_', '-', and not start with '.'",
            )
        ttl = _int(
            data.get("ttl_seconds", INVITE_TTL_SECONDS), "ttl_seconds", MIN_SECONDS, MAX_TTL_SECONDS
        )
        try:
            invite, token = await run_in_threadpool(store.add_invite, name, by=admin, ttl=ttl)
        except NameTaken:
            raise ApiError(409, "name_taken", f"{name!r} already has a key or an invite") from None
        return JSONResponse(
            _invite_json(invite) | {"url": f"{public_url}/join/{token}"},
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    async def invite(request: Request) -> Response:
        require_admin(store, request)
        if not await run_in_threadpool(store.cancel_invite, request.path_params["id"]):
            raise ApiError(404, "not_found", "no such invite")
        return Response(status_code=204)

    return [
        Route("/api/v1/admin/users", users),
        Route("/api/v1/admin/users/{name}", user, methods=["PATCH", "DELETE"]),
        Route("/api/v1/admin/stats", stats),
        Route("/api/v1/admin/settings", settings, methods=["GET", "PATCH"]),
        Route("/api/v1/admin/invites", invites, methods=["GET", "POST"]),
        Route("/api/v1/admin/invites/{id}", invite, methods=["DELETE"]),
    ]


def _invite_json(i: Invite) -> dict:
    return {"id": i.id, "name": i.name, "created_by": i.created_by, "expires_at": iso(i.expires_at)}


def _int(v: object, field: str, low: int, high: int | None = None) -> int:
    """A whole number, at least ``low`` and at most ``high``, or 400."""
    if isinstance(v, bool) or not isinstance(v, int) or v < low or (high and v > high):
        upto = f" and at most {high}" if high else ""
        raise ApiError(400, "bad_request", f"{field} must be a whole number, at least {low}{upto}")
    return v
