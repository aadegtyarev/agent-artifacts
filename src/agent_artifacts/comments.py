"""Comments on artifacts: the public calls under /a/{id} (the published
comments, a person's drafts and finishing their review), the counts on a
project's page under /p/{id}, and the owner's under /api/v1/artifacts/{id}.

Writing needs no agent key, so every body is checked here: JSON only, small,
with known fields and bounded strings. Texts and names are stored as they come
and are data for the agent, never instructions. A draft belongs to the author
key in X-Author-Key; only its SHA-256 is stored.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Awaitable, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import kinds
from .api import ApiError, error, iso, json_body, on_api_error, require_agent
from .store import (
    ANSWERED,
    DECISIONS,
    DRAFT_KINDS,
    Comment,
    Draft,
    DraftRefused,
    NoSuchComment,
    NothingToPublish,
    Reply,
    Store,
    TooManyComments,
    TooManyDrafts,
    TooManyReplies,
)

MAX_AUTHOR, MAX_TEXT = 64, 2000
MAX_BATCH = 200  # decisions or draft ids in one call
MAX_SELECTOR, MAX_ANCHOR_TEXT = 512, 200
# An element anchor may also say which screen of the page it is on: its
# name, and the page's #hash when the pin was placed.
MAX_SCREEN, MAX_HASH = 80, 200
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
_HASH_RE = re.compile(r"#[\x21-\x7e]*")
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
        artifact_id = request.path_params["id"]
        found = await run_in_threadpool(store.comments, artifact_id, key_hash=key)
        if found is None:
            raise _no_artifact()
        updated_at, comments = found
        return _updated(_answer([_json(c) for c in comments]), updated_at)

    async def project_summary(request: Request) -> Response:
        # For the project page's dots; under /p, so the proxy does not
        # count it as a write. Without a key: as for a stranger.
        key = _author_key(request, required=False)
        found = await run_in_threadpool(store.project_summary, request.path_params["id"], key)
        if found is None:
            raise ApiError(404, "not_found", "no such project")
        return _answer(found)

    async def drafts(request: Request) -> Response:
        artifact_id, key = request.path_params["id"], _author_key(request)
        if request.method == "GET":
            found = await run_in_threadpool(store.drafts, artifact_id, key)
            if found is None:
                raise _no_artifact()
            return _answer([_draft_json(d) for d in found])
        data = await json_body(request, {"author", "kind", "of", "text", "anchor"})
        author = _author(data)
        kind = data.get("kind", "comment")
        if kind not in DRAFT_KINDS:
            raise ApiError(400, "bad_request", f"kind must be one of {', '.join(DRAFT_KINDS)}")
        # A comment takes text and an optional anchor; a reply, the comment
        # it is about and text; a decision, only the comment.
        fields = {
            "comment": ({"text"}, {"anchor"}),
            "reply": ({"of", "text"}, set()),
        }.get(kind, ({"of"}, set()))
        needed, optional = fields
        if not needed <= set(data) - {"author", "kind"} <= needed | optional:
            raise ApiError(400, "bad_request", f"a {kind} draft takes {sorted(needed | optional)}")
        text = _text(data["text"]) if "text" in data else ""
        anchor = _anchor(data.get("anchor"), artifact_kind(artifact_id).pin)
        of_n = _of(data["of"]) if "of" in data else None
        draft = await _adding(
            functools.partial(
                store.add_draft,
                artifact_id,
                key,
                author=author,
                text=text,
                anchor=anchor,
                kind=kind,
                of_n=of_n,
            )
        )
        if draft is None:
            raise _no_artifact()
        return _answer(_draft_json(draft), 201)

    async def drafts_batch(request: Request) -> Response:
        # Accept all: many decisions in one write, so one rate-limited call
        # does it, and either all are made or none.
        artifact_id, key = request.path_params["id"], _author_key(request)
        data = await json_body(request, {"author", "decisions"}, {"decisions"})
        author = _author(data)
        decisions = _list(data["decisions"], "decisions")
        made = []
        for d in decisions:
            if not isinstance(d, dict) or set(d) != {"kind", "of"} or d["kind"] not in DECISIONS:
                raise ApiError(
                    400,
                    "bad_request",
                    f"each decision is {{kind, of}}, kind one of {', '.join(DECISIONS)}",
                )
            made.append((d["kind"], _of(d["of"])))
        # Two decisions on one comment would answer with a draft the second deleted.
        if len({of for _, of in made}) < len(made):
            raise ApiError(400, "bad_request", "one decision per comment")
        found = await _adding(
            functools.partial(store.add_decisions, artifact_id, key, author=author, decisions=made)
        )
        if found is None:
            raise _no_artifact()
        return _answer([_draft_json(d) for d in found], 201)

    async def drafts_delete(request: Request) -> Response:
        # Undo of Accept all: one write; ids that are not the key's are ignored.
        artifact_id, key = request.path_params["id"], _author_key(request)
        data = await json_body(request, {"ids"}, {"ids"})
        ids = _list(data["ids"], "ids")
        if not all(isinstance(i, str) for i in ids):
            raise ApiError(400, "bad_request", "ids must be draft ids")
        ids = [i for i in ids if _DRAFT_ID_RE.fullmatch(i)]
        if not await run_in_threadpool(store.delete_drafts, artifact_id, key, ids):
            raise _no_artifact()
        return Response(status_code=204)

    async def draft_item(request: Request) -> Response:
        artifact_id, key = request.path_params["id"], _author_key(request)
        draft_id = request.path_params["d"]
        if not _DRAFT_ID_RE.fullmatch(draft_id):
            raise _no_draft()
        if request.method == "DELETE":
            if await run_in_threadpool(store.delete_draft, artifact_id, key, draft_id):
                return Response(status_code=204)
            raise _no_draft()
        data = await json_body(request, {"text"}, {"text"})
        draft = await run_in_threadpool(
            store.edit_draft, artifact_id, key, draft_id, _text(data["text"])
        )
        if draft is None:
            raise _no_draft()
        return _answer(_draft_json(draft))

    async def review(request: Request) -> Response:
        artifact_id, key = request.path_params["id"], _author_key(request)
        data = await json_body(request, {"author", "text", "agent"}, {"author"})
        author = _author(data)
        text = _text(data["text"]) if "text" in data else None
        for_agent = _bool(data.get("agent", False), "agent")
        try:
            made = await run_in_threadpool(
                store.publish, artifact_id, key, author=author, text=text, for_agent=for_agent
            )
        except NothingToPublish:
            raise ApiError(400, "bad_request", "no drafts and no text to publish") from None
        except TooManyComments:
            raise ApiError(
                409, "too_many_comments", "this file has the most comments it can hold"
            ) from None
        except TooManyReplies:
            raise _too_many_replies() from None
        if made is None:
            raise _no_artifact()
        return _answer([_json(c) for c in made], 201)

    async def owner_list(request: Request) -> Response:
        agent = require_agent(store, request)
        artifact_id = request.path_params["id"]
        found = await run_in_threadpool(store.comments, artifact_id, owner=agent)
        if found is None:
            raise _no_artifact()
        updated_at, comments = found
        return _updated(_answer([_json(c) for c in comments]), updated_at)

    async def owner_take(request: Request) -> Response:
        agent = require_agent(store, request)
        taken = await run_in_threadpool(store.take, request.path_params["id"], owner=agent)
        if taken is None:
            raise _no_artifact()
        return _answer([_json(c) for c in taken])

    async def owner_reply(request: Request) -> Response:
        agent = require_agent(store, request)
        artifact_id, n = request.path_params["id"], _number(request)
        data = await json_body(request, {"text", "state"}, {"text"})
        text = _text(data["text"])
        state = data.get("state", "done")
        if state not in ANSWERED:
            raise ApiError(400, "bad_request", f"state must be one of {', '.join(ANSWERED)}")
        try:
            comment = await run_in_threadpool(
                functools.partial(store.agent_reply, artifact_id, n, text, state, owner=agent)
            )
        except TooManyReplies:
            raise _too_many_replies() from None
        if comment is None:
            raise _no_comment()
        return _answer(_json(comment), 201)

    async def owner_item(request: Request) -> Response:
        agent = require_agent(store, request)
        artifact_id, n = request.path_params["id"], _number(request)
        if request.method == "DELETE":
            if await run_in_threadpool(store.delete_comment, artifact_id, n, owner=agent):
                return Response(status_code=204)
            raise _no_comment()
        data = await json_body(request, {"resolved"}, {"resolved"})
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
        # Before drafts/{d}: the first route with a matching path answers.
        _route("/a/{id:artifact_id}/drafts/batch", drafts_batch, "POST"),
        _route("/a/{id:artifact_id}/drafts/delete", drafts_delete, "POST"),
        _route("/a/{id:artifact_id}/drafts/{d}", draft_item, "PATCH", "DELETE"),
        _route("/a/{id:artifact_id}/review", review, "POST"),
        _route("/p/{id:artifact_id}/summary", project_summary, "GET"),
        _route("/api/v1/artifacts/{id:artifact_id}/comments", owner_list, "GET"),
        _route("/api/v1/artifacts/{id:artifact_id}/comments/{n}", owner_item, "PATCH", "DELETE"),
        _route("/api/v1/artifacts/{id:artifact_id}/comments/{n}/reply", owner_reply, "POST"),
        _route("/api/v1/artifacts/{id:artifact_id}/take", owner_take, "POST"),
        _route("/api/v1/artifacts/{id:artifact_id}/drafts", owner_drafts, "DELETE"),
    ]


async def _adding(add: Callable[[], object]) -> object:
    """Run a store call that adds drafts, its refusals as API errors."""
    try:
        return await run_in_threadpool(add)
    except TooManyDrafts:
        raise ApiError(409, "too_many_drafts", "there are too many drafts on this file") from None
    except NoSuchComment:
        raise _no_comment() from None
    except DraftRefused as e:
        raise ApiError(400, "bad_request", str(e)) from None


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
                response = await on_api_error(request, e)
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
        "resolved": c.resolved_at is not None,
        "before_replace": c.before_replace,
        "mine": c.mine,
        "agent": c.agent,
        "approved_by": c.approved_by,
        "rejected_by": c.rejected_by,
        "replies": [_reply_json(r) for r in c.replies],
    }


def _reply_json(r: Reply) -> dict:
    return {
        "author": r.author,
        "text": r.text,
        "created_at": iso(r.created_at),
        "mine": r.mine,
        "agent": r.by_agent,
    }


def _draft_json(d: Draft) -> dict:
    return {
        "id": d.id,
        "kind": d.kind,
        "of": d.of_n,
        "author": d.author,
        # A decision has no text.
        "text": d.text if d.kind in ("comment", "reply") else None,
        "anchor": json.loads(d.anchor) if d.anchor else None,
        "created_at": iso(d.created_at),
        "updated_at": iso(d.updated_at),
    }


def _updated(response: Response, updated_at: float) -> Response:
    """Tell the viewer when the file was last uploaded, so it sees a replace."""
    response.headers["X-Artifact-Updated"] = iso(updated_at) or ""
    return response


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


def _too_many_replies() -> ApiError:
    return ApiError(409, "too_many_replies", "this comment has the most replies it can hold")


def _list(v: object, field: str) -> list:
    """A non-empty list of at most MAX_BATCH items."""
    if not isinstance(v, list) or not 0 < len(v) <= MAX_BATCH:
        raise ApiError(400, "bad_request", f"{field} must be a list of 1 to {MAX_BATCH} items")
    return v


def _of(v: object) -> int:
    """The number of the comment a reply or a decision is about."""
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(400, "bad_request", "of must be a comment number")
    if not 0 < v < 10**9:
        raise _no_comment()
    return v


def _number(request: Request) -> int:
    n = request.path_params["n"]
    if not _N_RE.fullmatch(n):
        raise _no_comment()
    return int(n)


def _string(v: object, field: str, at_most: int) -> str:
    """A string without control characters, trimmed, at most ``at_most``
    code points."""
    if not isinstance(v, str) or _REFUSED.search(v):
        raise ApiError(400, "bad_request", f"{field} must be text without control characters")
    v = v.strip()
    if len(v) > at_most:
        raise ApiError(400, "bad_request", f"{field} is longer than {at_most} characters")
    return v


def one_line(v: object, field: str, at_most: int) -> str:
    """Like _string, on one line."""
    if isinstance(v, str) and _LINE_BREAKS.search(v):
        raise ApiError(400, "bad_request", f"{field} must be one line")
    return _string(v, field, at_most)


def _author(data: dict) -> str:
    return one_line(data.get("author", ""), "author", MAX_AUTHOR) or "anonymous"


def _text(v: object) -> str:
    text = _string(v, "text", MAX_TEXT)
    if not text:
        raise ApiError(400, "bad_request", "text is empty")
    return text


def _bool(v: object, field: str = "resolved") -> bool:
    if not isinstance(v, bool):
        raise ApiError(400, "bad_request", f"{field} must be true or false")
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
    optional = {"point": set(), "element": {"screen", "hash"}}
    if (
        not isinstance(v, dict)
        or v.get("type") != pin
        or not fields[pin] <= set(v) <= fields[pin] | optional[pin]
    ):
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
            "selector": one_line(v["selector"], "anchor.selector", MAX_SELECTOR),
            "text": one_line(v["text"], "anchor.text", MAX_ANCHOR_TEXT),
            "width": width,
        }
        if "screen" in v:
            screen = one_line(v["screen"], "anchor.screen", MAX_SCREEN)
            if screen:
                anchor["screen"] = screen
        if "hash" in v:
            h = one_line(v["hash"], "anchor.hash", MAX_HASH)
            # As a browser reports it: "#" and printable ASCII ("#" alone is
            # the page with no hash, its start screen).
            if h and not _HASH_RE.fullmatch(h):
                raise ApiError(400, "bad_request", "anchor.hash must be # and printable ASCII")
            if h:
                anchor["hash"] = h
    return json.dumps(anchor)
