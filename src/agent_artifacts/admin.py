"""Admin API: people, their space, the common limits. Admin keys only."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .config import MB
from .store import NAME_RE, Invite, NameTaken, Store

HOUR = 3600
DAY = 24 * HOUR
MAX_TTL_HOURS = 30 * 24
INVITE_DAYS = 7


def admin_routes(
    store: Store,
    agent_of: Callable[[Request], str | None],
    error: Callable[..., JSONResponse],
    public_url: str,
) -> list[Route]:
    def admin_of(request: Request) -> tuple[str | None, Response | None]:
        agent = agent_of(request)
        if agent is None:
            return None, error(401, "unauthorized", "missing or unknown agent key")
        if not store.is_admin(agent):
            return None, error(403, "forbidden", "this needs an admin key")
        return agent, None

    async def body(request: Request) -> dict | None:
        try:
            data = await request.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def stats() -> dict:
        people = store.agent_infos()
        limits = store.limits()
        used = sum(p.used_bytes for p in people)
        quota = limits["user_quota_bytes"]
        return {
            "users": len(people),
            "files": sum(p.files for p in people),
            "used_bytes": used,
            "disk_free_bytes": shutil.disk_usage(store.files).free,
            "limits": {
                "user_quota_mb": None if quota is None else quota / MB,
                "max_total_mb": limits["max_total_bytes"] / MB,
                "ttl_hours": limits["ttl_seconds"] / HOUR,
            },
        }

    async def users(request: Request) -> Response:
        _, denied = admin_of(request)
        if denied:
            return denied
        common = store.limits()["user_quota_bytes"]
        return JSONResponse(
            [
                {
                    "name": p.name,
                    "admin": p.admin,
                    "used_bytes": p.used_bytes,
                    "files": p.files,
                    "quota_bytes": common if p.quota_bytes is None else p.quota_bytes,
                    "own_quota": p.quota_bytes is not None,
                    "last_upload_at": _iso(p.last_upload_at),
                }
                for p in store.agent_infos()
            ]
        )

    async def user(request: Request) -> Response:
        agent, denied = admin_of(request)
        if denied:
            return denied
        name = request.path_params["name"]
        if not re.fullmatch(NAME_RE, name):
            return error(404, "not_found", "no such user")
        if request.method == "DELETE":
            if name == agent:
                return error(400, "bad_request", "an admin cannot remove its own key")
            if not await run_in_threadpool(store.remove_agent, name):
                return error(404, "not_found", "no such user")
            return Response(status_code=204)
        data = await body(request)
        if data is None or set(data) != {"quota_mb"}:
            return error(400, "bad_request", 'expected {"quota_mb": <MB> or null}')
        quota = data["quota_mb"]
        if quota is not None and not _positive(quota):
            return error(400, "bad_request", "quota_mb must be a number of MB, at least 1, or null")
        quota_bytes = None if quota is None else int(quota * MB)
        if not await run_in_threadpool(store.set_quota, name, quota_bytes):
            return error(404, "not_found", "no such user")
        return Response(status_code=204)

    async def get_stats(request: Request) -> Response:
        _, denied = admin_of(request)
        return denied or JSONResponse(await run_in_threadpool(stats))

    async def settings(request: Request) -> Response:
        _, denied = admin_of(request)
        if denied:
            return denied
        data = await body(request)
        allowed = {"user_quota_mb", "max_total_mb", "ttl_hours"}
        if not data or not set(data) <= allowed or not all(map(_positive, data.values())):
            return error(400, "bad_request", f"expected numbers, at least 1, for {sorted(allowed)}")
        values = {}
        if "user_quota_mb" in data:
            values["user_quota_bytes"] = int(data["user_quota_mb"] * MB)
        if "max_total_mb" in data:
            total = int(data["max_total_mb"] * MB)
            current = await run_in_threadpool(stats)
            # Leave the database and its journal room to write, or a full
            # disk would stop even the cleanup.
            free = current["disk_free_bytes"]
            room = current["used_bytes"] + free - max(256 * MB, free // 20)
            if total > room:
                return error(400, "bad_request", f"the disk has room for {room // MB} MB at most")
            values["max_total_bytes"] = total
        if "ttl_hours" in data:
            if data["ttl_hours"] > MAX_TTL_HOURS:
                return error(400, "bad_request", f"ttl_hours is at most {MAX_TTL_HOURS}")
            values["ttl_seconds"] = int(data["ttl_hours"] * HOUR)
        await run_in_threadpool(lambda: store.set_limits(**values))
        return JSONResponse((await run_in_threadpool(stats))["limits"])

    async def invites(request: Request) -> Response:
        agent, denied = admin_of(request)
        if denied:
            return denied
        if request.method == "GET":
            return JSONResponse([_invite_json(i) for i in store.invites()])
        data = await body(request)
        if data is None or not {"name"} <= set(data) <= {"name", "days"}:
            return error(400, "bad_request", 'expected {"name": "<name>", "days": <1-30>}')
        name, days = data["name"], data.get("days", INVITE_DAYS)
        if not isinstance(name, str) or not re.fullmatch(NAME_RE, name):
            return error(
                400,
                "bad_request",
                "name may use letters, digits, '.', '_', '-', and not start with '.'",
            )
        if not _positive(days) or days > 30:
            return error(400, "bad_request", "days must be from 1 to 30")
        try:
            invite, token = await run_in_threadpool(
                store.add_invite, name, by=agent, ttl=int(days * DAY)
            )
        except NameTaken:
            return error(409, "name_taken", f"{name!r} already has a key or an invite")
        return JSONResponse(
            _invite_json(invite) | {"url": f"{public_url}/join/{token}"},
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    async def invite(request: Request) -> Response:
        _, denied = admin_of(request)
        if denied:
            return denied
        if not await run_in_threadpool(store.cancel_invite, request.path_params["id"]):
            return error(404, "not_found", "no such invite")
        return Response(status_code=204)

    return [
        Route("/api/v1/admin/invites", invites, methods=["GET", "POST"]),
        Route("/api/v1/admin/invites/{id}", invite, methods=["DELETE"]),
        Route("/api/v1/admin/users", users),
        Route("/api/v1/admin/users/{name}", user, methods=["PATCH", "DELETE"]),
        Route("/api/v1/admin/stats", get_stats),
        Route("/api/v1/admin/settings", settings, methods=["PATCH"]),
    ]


def _invite_json(i: Invite) -> dict:
    return {
        "id": i.id,
        "name": i.name,
        "created_by": i.created_by,
        "expires_at": _iso(i.expires_at),
    }


def _positive(v: object) -> bool:
    """At least 1 (MB or hour), so no limit rounds down to nothing."""
    return isinstance(v, int | float) and not isinstance(v, bool) and 1 <= v < 1e9


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
