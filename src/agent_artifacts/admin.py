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
from .store import NAME_RE, Store

HOUR = 3600
MAX_TTL_HOURS = 30 * 24


def admin_routes(
    store: Store,
    agent_of: Callable[[Request], str | None],
    error: Callable[..., JSONResponse],
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
        if not store.set_quota(name, None if quota is None else int(quota * MB)):
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
            room = current["used_bytes"] + current["disk_free_bytes"]
            if total > room:
                return error(400, "bad_request", f"the disk has room for {room // MB} MB at most")
            values["max_total_bytes"] = total
        if "ttl_hours" in data:
            if data["ttl_hours"] > MAX_TTL_HOURS:
                return error(400, "bad_request", f"ttl_hours is at most {MAX_TTL_HOURS}")
            values["ttl_seconds"] = int(data["ttl_hours"] * HOUR)
        store.set_limits(**values)
        return JSONResponse((await run_in_threadpool(stats))["limits"])

    return [
        Route("/api/v1/admin/users", users),
        Route("/api/v1/admin/users/{name}", user, methods=["PATCH", "DELETE"]),
        Route("/api/v1/admin/stats", get_stats),
        Route("/api/v1/admin/settings", settings, methods=["PATCH"]),
    ]


def _positive(v: object) -> bool:
    """At least 1 (MB or hour), so no limit rounds down to nothing."""
    return isinstance(v, int | float) and not isinstance(v, bool) and 1 <= v < 1e9


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
