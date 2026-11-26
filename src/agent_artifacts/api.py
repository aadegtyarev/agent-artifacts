"""What the API handlers share: errors, keys, JSON shapes."""

from __future__ import annotations

import json
from collections.abc import Set
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from .store import AgentInfo, Store

MAX_BODY_BYTES = 16 * 1024  # a JSON body, when json_body is strict


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


async def json_body(
    request: Request, allowed: Set[str], required: Set[str] = frozenset(), *, strict: bool = True,
    reject_ambiguous: bool = False,
) -> dict:
    """The request's JSON object, with only ``allowed`` keys and all ``required``.

    Strict, the body is checked here rather than trusted to the proxy: JSON
    Content-Type, a Content-Length, at most MAX_BODY_BYTES counted as it
    arrives. The admin calls pass ``strict=False``: only an admin key reaches
    them and they have always taken any body, so their answers stay as they were.
    """
    if not strict:
        return _json_object(await request.body(), allowed, required, reject_ambiguous=reject_ambiguous)
    ctype = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if ctype != "application/json":
        raise ApiError(400, "bad_request", "Content-Type must be application/json")
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise ApiError(411, "bad_request", "Content-Length is required")
    if int(length) > MAX_BODY_BYTES:
        raise ApiError(413, "too_large", f"limit is {MAX_BODY_BYTES} bytes")
    # Counted as it arrives, whatever the headers claim.
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "too_large", f"limit is {MAX_BODY_BYTES} bytes")
    return _json_object(bytes(body), allowed, required, reject_ambiguous=reject_ambiguous)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError("duplicate object key")
    return data


def _reject_constant(_: str) -> None:
    raise ValueError("nonstandard JSON constant")


def _json_object(body: bytes, allowed: Set[str], required: Set[str], *, reject_ambiguous: bool = False) -> dict:
    """``body`` parsed as a JSON object, with only ``allowed`` keys and all ``required``."""
    try:
        options = {"object_pairs_hook": _unique_json_object, "parse_constant": _reject_constant} if reject_ambiguous else {}
        data = json.loads(body, **options)
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
