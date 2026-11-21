"""What the API handlers share: errors, keys, JSON shapes."""

from __future__ import annotations

import json
from collections.abc import Set
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from .store import AgentInfo, Store


class ApiError(Exception):
    """Raised by a handler, answered as {"error": code, "message": ...}."""

    def __init__(self, status: int, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def error(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status)


async def on_api_error(request: Request, e: Exception) -> JSONResponse:
    assert isinstance(e, ApiError)
    return error(e.status, e.code, e.message, **e.extra)


def require_agent(store: Store, request: Request) -> str:
    """The calling agent's name, or 401."""
    scheme, _, key = request.headers.get("authorization", "").partition(" ")
    agent = store.agent_for_key(key.strip()) if scheme.lower() == "bearer" and key else None
    if agent is None:
        raise ApiError(401, "unauthorized", "missing or unknown agent key")
    return agent


def require_admin(store: Store, request: Request) -> str:
    agent = require_agent(store, request)
    if not store.is_admin(agent):
        raise ApiError(403, "forbidden", "this needs an admin key")
    return agent


async def json_body(request: Request, allowed: Set[str], required: Set[str] = frozenset()) -> dict:
    """The request's JSON object, with only ``allowed`` keys and all ``required``."""
    return json_object(await request.body(), allowed, required)


def json_object(body: bytes, allowed: Set[str], required: Set[str] = frozenset()) -> dict:
    """``body`` parsed as a JSON object, with only ``allowed`` keys and all ``required``."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):  # RecursionError: nested too deep
        data = None
    if not isinstance(data, dict) or not required <= set(data) <= allowed or not data:
        raise ApiError(400, "bad_request", f"expected a JSON object with {sorted(allowed)}")
    return data


def iso(ts: float | None, *, human: bool = False) -> str | None:
    if ts is None:
        return None
    dt = datetime.fromtimestamp(ts, UTC)
    return dt.strftime("%Y-%m-%d %H:%M UTC") if human else dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def user_json(info: AgentInfo, common_quota: int | None) -> dict:
    """One person, the same shape in /me and the admin calls."""
    return {
        "name": info.name,
        "admin": info.admin,
        "used_bytes": info.used_bytes,
        "files": info.files,
        "quota_bytes": common_quota if info.quota_bytes is None else info.quota_bytes,
        "own_quota": info.quota_bytes is not None,
        "last_upload_at": iso(info.last_upload_at),
    }
