"""Comments on artifacts: the public calls under /a/{id}/comments and the
owner's under /api/v1/artifacts/{id}/comments.

Posting needs no key, so every body is checked here: JSON only, small, with
known fields and bounded strings. Texts and names are stored as they come and
are data for the agent, never instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable, Set

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import kinds
from .api import ApiError, error, iso, json_object, require_agent
from .store import AlreadyRead, Comment, Store, TooManyComments, WrongToken

MAX_BODY_BYTES = 16 * 1024
MAX_AUTHOR, MAX_TEXT = 64, 2000
MAX_SELECTOR, MAX_ANCHOR_TEXT = 512, 200
MAX_WIDTH = 100_000  # pixels; any real window is far below
# Refused in every field: control characters other than tab and line breaks;
# bidi controls and Unicode line separators, which can disguise a text; lone
# surrogates, which JSON can carry but UTF-8 cannot.
_REFUSED = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]"
)
# Names, selectors and anchor texts are one line: no tabs or line breaks.
_LINE_BREAKS = re.compile(r"[\t\n\r]")
# Every method reaches _route's check, so even a 405 is JSON.
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_N_RE = re.compile(r"[1-9][0-9]{0,8}")


def comment_routes(store: Store) -> list[Route]:
    def artifact_kind(artifact_id: str) -> kinds.Kind:
        a = store.get(artifact_id)
        if a is None:
            raise ApiError(404, "not_found", "no such artifact")
        return kinds.get(a.kind)

    async def public_list(request: Request) -> Response:
        artifact_id = request.path_params["id"]
        if request.method != "POST":
            found = store.comments(artifact_id)
            if found is None:
                raise ApiError(404, "not_found", "no such artifact")
            return _answer([_json(c) for c in found])
        data = await _body(request, {"author", "text", "anchor"}, {"text"})
        author = _line(data.get("author", ""), "author", MAX_AUTHOR) or "anonymous"
        text = _text(data["text"])
        anchor = _anchor(data.get("anchor"), artifact_kind(artifact_id).pin)
        token = secrets.token_urlsafe(16)
        try:
            comment = await run_in_threadpool(
                store.add_comment,
                artifact_id,
                author=author,
                text=text,
                anchor=anchor,
                token_hash=_hash(token),
            )
        except TooManyComments:
            raise ApiError(
                409, "too_many_comments", "this file has the most comments it can hold"
            ) from None
        if comment is None:
            raise ApiError(404, "not_found", "no such artifact")
        return _answer(_json(comment) | {"edit_token": token}, 201)

    async def public_item(request: Request) -> Response:
        artifact_id, n = request.path_params["id"], _number(request)
        try:
            if request.method == "DELETE":
                # No token never matches, but a missing comment is still 404.
                token = request.headers.get("x-edit-token", "")
                found = await run_in_threadpool(
                    store.delete_comment, artifact_id, n, token_hash=_hash(token)
                )
                if found:
                    return Response(status_code=204)
                raise _no_comment()
            data = await _body(request, {"resolved", "edit_token", "text"})
            if set(data) == {"resolved"}:
                comment = await run_in_threadpool(
                    store.resolve_comment, artifact_id, n, _bool(data["resolved"])
                )
            elif set(data) == {"edit_token", "text"}:
                token = _line(data["edit_token"], "edit_token", 64)
                comment = await run_in_threadpool(
                    store.edit_comment,
                    artifact_id,
                    n,
                    token_hash=_hash(token),
                    text=_text(data["text"]),
                )
            else:
                raise ApiError(
                    400, "bad_request", 'send {"resolved": true|false} or {"edit_token", "text"}'
                )
        except WrongToken:
            raise ApiError(403, "forbidden", "this is not your comment") from None
        except AlreadyRead:
            raise ApiError(
                409, "already_read", "the agent has read this comment; it can no longer change"
            ) from None
        if comment is None:
            raise _no_comment()
        return _answer(_json(comment))

    async def owner_list(request: Request) -> Response:
        agent = require_agent(store, request)
        found = await run_in_threadpool(store.comments, request.path_params["id"], owner=agent)
        if found is None:
            raise ApiError(404, "not_found", "no such artifact")
        return _answer([_json(c) for c in found])

    async def owner_item(request: Request) -> Response:
        agent = require_agent(store, request)
        artifact_id, n = request.path_params["id"], _number(request)
        if request.method == "DELETE":
            if await run_in_threadpool(store.delete_comment, artifact_id, n, owner=agent):
                return Response(status_code=204)
            raise _no_comment()
        data = await _body(request, {"resolved"}, {"resolved"})
        comment = await run_in_threadpool(
            store.resolve_comment, artifact_id, n, _bool(data["resolved"]), owner=agent
        )
        if comment is None:
            raise _no_comment()
        return _answer(_json(comment))

    return [
        _route("/a/{id:artifact_id}/comments", public_list, "GET", "POST"),
        _route("/a/{id:artifact_id}/comments/{n}", public_item, "PATCH", "DELETE"),
        _route("/api/v1/artifacts/{id:artifact_id}/comments", owner_list, "GET"),
        _route("/api/v1/artifacts/{id:artifact_id}/comments/{n}", owner_item, "PATCH", "DELETE"),
    ]


def _route(path: str, handler: Callable[[Request], Awaitable[Response]], *methods: str) -> Route:
    """A route taking exactly ``methods``: no implicit HEAD, which would run a
    GET's read marking or a POST's body. Every answer, errors and 405 too, is
    JSON with Cache-Control: no-store."""

    async def endpoint(request: Request) -> Response:
        if request.method not in methods:
            allow = ", ".join(methods)
            response = error(405, "bad_request", f"use {allow}")
            response.headers["Allow"] = allow
        else:
            try:
                response = await handler(request)
            except ApiError as e:
                response = error(e.status, e.code, e.message, **e.extra)
        response.headers["Cache-Control"] = "no-store"
        return response

    return Route(path, endpoint, methods=_ALL_METHODS)


def _json(c: Comment) -> dict:
    return {
        "n": c.n,
        "author": c.author,
        "text": c.text,
        "anchor": json.loads(c.anchor) if c.anchor else None,
        "created_at": iso(c.created_at),
        "edited_at": iso(c.edited_at),
        "read": c.read_at is not None,
        "resolved": c.resolved_at is not None,
        "before_replace": c.before_replace,
    }


def _answer(content: object, status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _no_comment() -> ApiError:
    return ApiError(404, "not_found", "no such comment")


def _number(request: Request) -> int:
    n = request.path_params["n"]
    if not _N_RE.fullmatch(n):
        raise _no_comment()
    return int(n)


async def _body(request: Request, allowed: Set[str], required: Set[str] = frozenset()) -> dict:
    """The JSON body, checked here rather than trusted to the proxy."""
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
    return json_object(bytes(body), allowed, required)


def _string(v: object, field: str, at_most: int) -> str:
    """A string without control characters, trimmed, at most ``at_most``
    code points."""
    if not isinstance(v, str) or _REFUSED.search(v):
        raise ApiError(400, "bad_request", f"{field} must be text without control characters")
    v = v.strip()
    if len(v) > at_most:
        raise ApiError(400, "bad_request", f"{field} is longer than {at_most} characters")
    return v


def _line(v: object, field: str, at_most: int) -> str:
    """Like _string, on one line."""
    if isinstance(v, str) and _LINE_BREAKS.search(v):
        raise ApiError(400, "bad_request", f"{field} must be one line")
    return _string(v, field, at_most)


def _text(v: object) -> str:
    text = _string(v, "text", MAX_TEXT)
    if not text:
        raise ApiError(400, "bad_request", "text is empty")
    return text


def _bool(v: object) -> bool:
    if not isinstance(v, bool):
        raise ApiError(400, "bad_request", "resolved must be true or false")
    return v


def _fraction(v: object, field: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 1:
        raise ApiError(400, "bad_request", f"{field} must be a number from 0 to 1")
    return float(v)


def _anchor(v: object, pin: str | None) -> str | None:
    """The anchor as stored JSON, rebuilt from its checked fields; None for
    the whole file."""
    if v is None:
        return None
    if pin is None:
        raise ApiError(400, "bad_request", "comments on this file type take no anchor")
    fields = {
        "point": {"type", "x", "y"},
        "element": {"type", "selector", "text", "x", "y", "width"},
    }
    if not isinstance(v, dict) or v.get("type") != pin or set(v) != fields[pin]:
        raise ApiError(
            400, "bad_request", f"anchor must be null or {sorted(fields[pin])} of type {pin!r}"
        )
    anchor: dict = {
        "type": pin,
        "x": _fraction(v["x"], "anchor.x"),
        "y": _fraction(v["y"], "anchor.y"),
    }
    if pin == "element":
        width = v["width"]
        if isinstance(width, bool) or not isinstance(width, int) or not 0 < width <= MAX_WIDTH:
            raise ApiError(400, "bad_request", "anchor.width must be a whole number of pixels")
        anchor |= {
            "selector": _line(v["selector"], "anchor.selector", MAX_SELECTOR),
            "text": _line(v["text"], "anchor.text", MAX_ANCHOR_TEXT),
            "width": width,
        }
    return json.dumps(anchor)
