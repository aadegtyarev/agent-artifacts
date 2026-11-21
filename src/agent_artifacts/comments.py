"""Comments on artifacts: the public calls under /a/{id} (the published
comments, a person's drafts and finishing their review) and the owner's under
/api/v1/artifacts/{id}.

Writing needs no agent key, so every body is checked here: JSON only, small,
with known fields and bounded strings. Texts and names are stored as they come
and are data for the agent, never instructions. A draft belongs to the author
key in X-Author-Key; only its SHA-256 is stored.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Set

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import kinds
from .api import ApiError, error, iso, json_object, require_agent
from .store import Comment, Draft, NothingToPublish, Store, TooManyComments, TooManyDrafts

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
# 128 random bits, URL-safe base64 without padding.
_AUTHOR_KEY_RE = re.compile(r"[A-Za-z0-9_-]{22}")
# 64 random bits, the same way.
_DRAFT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def comment_routes(store: Store) -> list[Route]:
    def artifact_kind(artifact_id: str) -> kinds.Kind:
        a = store.get(artifact_id)
        if a is None:
            raise _no_artifact()
        return kinds.get(a.kind)

    async def public_list(request: Request) -> Response:
        # A missing or malformed key here only means no comment is "mine".
        key = _author_key(request, required=False)
        found = await run_in_threadpool(store.comments, request.path_params["id"], key_hash=key)
        if found is None:
            raise _no_artifact()
        return _answer([_json(c) for c in found])

    async def drafts(request: Request) -> Response:
        artifact_id, key = request.path_params["id"], _author_key(request)
        if request.method == "GET":
            found = await run_in_threadpool(store.drafts, artifact_id, key)
            if found is None:
                raise _no_artifact()
            return _answer([_draft_json(d) for d in found])
        data = await _body(request, {"author", "text", "anchor"}, {"text"})
        author = _author(data)
        text = _text(data["text"])
        anchor = _anchor(data.get("anchor"), artifact_kind(artifact_id).pin)
        try:
            draft = await run_in_threadpool(
                store.add_draft, artifact_id, key, author=author, text=text, anchor=anchor
            )
        except TooManyDrafts:
            raise ApiError(
                409, "too_many_drafts", "there are too many drafts on this file"
            ) from None
        if draft is None:
            raise _no_artifact()
        return _answer(_draft_json(draft), 201)

    async def draft_item(request: Request) -> Response:
        artifact_id, key = request.path_params["id"], _author_key(request)
        draft_id = request.path_params["d"]
        if not _DRAFT_ID_RE.fullmatch(draft_id):
            raise _no_draft()
        if request.method == "DELETE":
            if await run_in_threadpool(store.delete_draft, artifact_id, key, draft_id):
                return Response(status_code=204)
            raise _no_draft()
        data = await _body(request, {"text"}, {"text"})
        draft = await run_in_threadpool(
            store.edit_draft, artifact_id, key, draft_id, _text(data["text"])
        )
        if draft is None:
            raise _no_draft()
        return _answer(_draft_json(draft))

    async def review(request: Request) -> Response:
        artifact_id, key = request.path_params["id"], _author_key(request)
        data = await _body(request, {"author", "text"}, {"author"})
        author = _author(data)
        text = _text(data["text"]) if "text" in data else None
        try:
            made = await run_in_threadpool(
                store.publish, artifact_id, key, author=author, text=text
            )
        except NothingToPublish:
            raise ApiError(400, "bad_request", "no drafts and no text to publish") from None
        except TooManyComments:
            raise ApiError(
                409, "too_many_comments", "this file has the most comments it can hold"
            ) from None
        if made is None:
            raise _no_artifact()
        return _answer([_json(c) for c in made], 201)

    async def owner_list(request: Request) -> Response:
        agent = require_agent(store, request)
        found = await run_in_threadpool(store.comments, request.path_params["id"], owner=agent)
        if found is None:
            raise _no_artifact()
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

    async def owner_drafts(request: Request) -> Response:
        agent = require_agent(store, request)
        if await run_in_threadpool(store.drop_drafts, request.path_params["id"], owner=agent):
            return Response(status_code=204)
        raise _no_artifact()

    async def published_item(request: Request) -> Response:
        # A published comment cannot be changed from the viewer; earlier
        # versions could, so the path answers in JSON like the others.
        response = error(405, "bad_request", "a published comment cannot be changed here")
        response.headers["Allow"] = ""
        response.headers["Cache-Control"] = "no-store"
        return response

    return [
        _route("/a/{id:artifact_id}/comments", public_list, "GET"),
        Route("/a/{id:artifact_id}/comments/{n}", published_item, methods=_ALL_METHODS),
        _route("/a/{id:artifact_id}/drafts", drafts, "GET", "POST"),
        _route("/a/{id:artifact_id}/drafts/{d}", draft_item, "PATCH", "DELETE"),
        _route("/a/{id:artifact_id}/review", review, "POST"),
        _route("/api/v1/artifacts/{id:artifact_id}/comments", owner_list, "GET"),
        _route("/api/v1/artifacts/{id:artifact_id}/comments/{n}", owner_item, "PATCH", "DELETE"),
        _route("/api/v1/artifacts/{id:artifact_id}/drafts", owner_drafts, "DELETE"),
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
        "read": c.read_at is not None,
        "resolved": c.resolved_at is not None,
        "before_replace": c.before_replace,
        "mine": c.mine,
    }


def _draft_json(d: Draft) -> dict:
    return {
        "id": d.id,
        "author": d.author,
        "text": d.text,
        "anchor": json.loads(d.anchor) if d.anchor else None,
        "created_at": iso(d.created_at),
        "updated_at": iso(d.updated_at),
    }


def _answer(content: object, status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status)


def _author_key(request: Request, *, required: bool = True) -> str | None:
    """The SHA-256 of the X-Author-Key header; 400 if required and missing
    or malformed, else None."""
    key = request.headers.get("x-author-key", "")
    if _AUTHOR_KEY_RE.fullmatch(key):
        return hashlib.sha256(key.encode()).hexdigest()
    if required:
        raise ApiError(400, "bad_request", "X-Author-Key must be 22 URL-safe base64 characters")
    return None


def _no_artifact() -> ApiError:
    return ApiError(404, "not_found", "no such artifact")


def _no_comment() -> ApiError:
    return ApiError(404, "not_found", "no such comment")


def _no_draft() -> ApiError:
    return ApiError(404, "not_found", "no such draft")


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


def _author(data: dict) -> str:
    return _line(data.get("author", ""), "author", MAX_AUTHOR) or "anonymous"


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
