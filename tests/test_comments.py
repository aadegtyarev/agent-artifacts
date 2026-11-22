"""Comments: drafts by author key, finishing a review, the owner's calls."""

import io
import sqlite3
import time

import pytest
from starlette.testclient import TestClient

from agent_artifacts.config import Config
from agent_artifacts.store import DRAFT_TTL_SECONDS, Store
from agent_artifacts.web import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
# Author keys: 22 URL-safe base64 characters, as the viewer makes them.
KEY, OTHER = "k" * 22, "m" * 22
ELEMENT = {"type": "element", "selector": "main > p", "text": "Hi", "x": 0.5, "y": 1, "width": 1280}
POINT = {"type": "point", "x": 0.25, "y": 0.75}


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def env(tmp_path):
    config = Config(
        data_dir=tmp_path,
        public_url="https://art.example.org",
        max_file_bytes=1000,
        max_total_bytes=3000,
    )
    clock = Clock()
    # Files live 30 days here, so drafts can outlive their 7.
    store = Store(
        tmp_path, max_total_bytes=config.max_total_bytes, ttl_seconds=30 * 86400, clock=clock
    )
    keys = {name: store.add_agent(name) for name in ("alice", "bob")}
    with TestClient(create_app(config, store)) as client:
        client.clock = clock
        yield client, store, keys


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def as_author(key=KEY):
    return {"X-Author-Key": key}


def upload(client, key, filename, data, artifact_id=None):
    url = "/api/v1/artifacts" + (f"/{artifact_id}" if artifact_id else "")
    method = client.put if artifact_id else client.post
    return method(url, headers=auth(key), files={"file": (filename, data)}).json()


def add_draft(client, art_id, body=None, key=KEY):
    body = {"text": "hi"} if body is None else body
    return client.post(f"/a/{art_id}/drafts", json=body, headers=as_author(key))


def review(client, art_id, body=None, key=KEY):
    body = {"author": "Petrov"} if body is None else body
    return client.post(f"/a/{art_id}/review", json=body, headers=as_author(key))


def comment(client, art_id, text="hi", key=KEY):
    """One published comment: a draft and a review at once."""
    assert add_draft(client, art_id, {"text": text}, key).status_code == 201
    r = review(client, art_id, key=key)
    assert r.status_code == 201
    return r.json()[-1]


def drafts(client, art_id, key=KEY):
    return client.get(f"/a/{art_id}/drafts", headers=as_author(key))


def owner_comments(client, key, art_id):
    return client.get(f"/api/v1/artifacts/{art_id}/comments", headers=auth(key))


def draft_rows(store):
    with store._conn() as db:
        return db.execute("SELECT artifact_id FROM drafts").fetchall()


# Drafts


def test_draft_lifecycle(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>Hi</p>")
    r = add_draft(
        client, art["id"], {"author": "  ", "text": " Make it green\n", "anchor": ELEMENT}
    )
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert len(d["id"]) == 11
    assert (d["author"], d["text"], d["anchor"]) == ("anonymous", "Make it green", ELEMENT)
    assert d["created_at"] == d["updated_at"]
    listed = drafts(client, art["id"])
    assert listed.headers["cache-control"] == "no-store" and listed.json() == [d]

    client.clock.now += 120
    url = f"/a/{art['id']}/drafts/{d['id']}"
    edited = client.patch(url, json={"text": "Blue"}, headers=as_author()).json()
    assert edited["text"] == "Blue" and edited["updated_at"] > d["updated_at"]
    assert edited["anchor"] == ELEMENT and edited["created_at"] == d["created_at"]
    assert client.delete(url, headers=as_author()).status_code == 204
    assert client.delete(url, headers=as_author()).json()["error"] == "not_found"
    assert drafts(client, art["id"]).json() == []


def test_drafts_are_seen_by_their_key_only(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    d = add_draft(client, art["id"]).json()
    url = f"/a/{art['id']}/drafts/{d['id']}"
    assert drafts(client, art["id"], OTHER).json() == []
    r = client.patch(url, json={"text": "mine now"}, headers=as_author(OTHER))
    assert (r.status_code, r.json()["error"]) == (404, "not_found")
    assert client.delete(url, headers=as_author(OTHER)).status_code == 404
    # Nobody else sees a draft: not the public list, the owner or the counts.
    assert client.get(f"/a/{art['id']}/comments").json() == []
    assert owner_comments(client, keys["alice"], art["id"]).json() == []
    listed = client.get("/api/v1/artifacts", headers=auth(keys["alice"])).json()
    assert (listed[0]["comments"], listed[0]["open_comments"]) == (0, 0)
    # The viewer page counts no comments: its sheet waits, hidden.
    assert '<span id="strip-text" class="strip-text"></span>' in client.get(f"/a/{art['id']}").text
    assert drafts(client, art["id"]).json() == [d]
    # Only the hash is stored.
    with store._conn() as db:
        assert KEY not in db.execute("SELECT author_key_hash FROM drafts").fetchone()[0]


@pytest.mark.parametrize(
    "headers",
    [{}, as_author(""), as_author("k" * 21), as_author("k" * 23), as_author("k" * 21 + "+")],
)
def test_draft_and_review_calls_need_a_well_formed_key(env, headers):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    d = add_draft(client, art["id"]).json()
    base = f"/a/{art['id']}"
    calls = [
        client.get(f"{base}/drafts", headers=headers),
        client.post(f"{base}/drafts", json={"text": "x"}, headers=headers),
        client.patch(f"{base}/drafts/{d['id']}", json={"text": "x"}, headers=headers),
        client.delete(f"{base}/drafts/{d['id']}", headers=headers),
        client.post(f"{base}/review", json={"author": "x"}, headers=headers),
    ]
    for r in calls:
        assert (r.status_code, r.json()["error"]) == (400, "bad_request")
        assert r.headers["cache-control"] == "no-store"
    # The comment list takes a bad key as no key.
    assert client.get(f"{base}/comments", headers=headers).status_code == 200
    assert drafts(client, art["id"]).json() == [d]


def test_draft_limits_per_key_and_per_file(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    other_file = upload(client, keys["alice"], "b.txt", b"x")
    for _ in range(50):
        store.add_draft(art["id"], "first", author="a", text="t", anchor=None)
    store.add_draft(other_file["id"], "first", author="a", text="t", anchor=None)
    # The API hashes KEY, so its 50 are apart from the store's "first".
    for _ in range(50):
        assert add_draft(client, art["id"]).status_code == 201
    r = add_draft(client, art["id"])
    assert (r.status_code, r.json()["error"]) == (409, "too_many_drafts")
    assert r.headers["cache-control"] == "no-store"
    for key in ("second", "third"):
        for _ in range(50):
            store.add_draft(art["id"], key, author="a", text="t", anchor=None)
    # 200 on the file: another key is refused too, another file is not.
    r = add_draft(client, art["id"], key=OTHER)
    assert (r.status_code, r.json()["error"]) == (409, "too_many_drafts")
    assert add_draft(client, other_file["id"]).status_code == 201
    # A deleted draft makes room.
    d = drafts(client, art["id"]).json()[0]
    client.delete(f"/a/{art['id']}/drafts/{d['id']}", headers=as_author())
    assert add_draft(client, art["id"], key=OTHER).status_code == 201


def test_drafts_expire_after_seven_days_unchanged(env, monkeypatch):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    old = add_draft(client, art["id"], {"text": "old"}).json()
    kept = add_draft(client, art["id"], {"text": "kept"}).json()
    client.clock.now += 6 * 24 * 3600
    url = f"/a/{art['id']}/drafts/{kept['id']}"
    assert client.patch(url, json={"text": "kept, edited"}, headers=as_author()).status_code == 200
    client.clock.now += 24 * 3600 - 1
    assert [d["text"] for d in drafts(client, art["id"]).json()] == ["old", "kept, edited"]
    client.clock.now += 1
    assert [d["text"] for d in drafts(client, art["id"]).json()] == ["kept, edited"]
    old_url = f"/a/{art['id']}/drafts/{old['id']}"
    assert client.patch(old_url, json={"text": "x"}, headers=as_author()).status_code == 404
    assert client.delete(old_url, headers=as_author()).status_code == 404
    # An expired draft no longer counts toward the limit.
    monkeypatch.setattr("agent_artifacts.store.MAX_DRAFTS_PER_KEY", 2)
    assert add_draft(client, art["id"]).status_code == 201
    # The cleanup deletes it from the database; publishing skips it.
    client.clock.now += DRAFT_TTL_SECONDS
    store.purge_expired()
    assert draft_rows(store) == []
    assert review(client, art["id"]).status_code == 400


def test_drafts_of_a_missing_artifact_or_draft_are_404(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    missing = "a" * 22
    assert drafts(client, missing).json()["error"] == "not_found"
    assert add_draft(client, missing).json()["error"] == "not_found"
    assert review(client, missing).json()["error"] == "not_found"
    for draft_id in ("b" * 11, "short", "b" * 12):
        url = f"/a/{art['id']}/drafts/{draft_id}"
        r = client.patch(url, json={"text": "x"}, headers=as_author())
        assert (r.status_code, r.json()["error"]) == (404, "not_found")


# Finishing a review


def test_review_publishes_drafts_in_order_and_the_general_comment_last(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>Hi</p>")
    add_draft(client, art["id"], {"author": "Ivan", "text": "first", "anchor": ELEMENT})
    second = add_draft(client, art["id"], {"text": "second"}).json()
    add_draft(client, art["id"], {"text": "someone else's"}, key=OTHER)
    client.clock.now += 60
    # Editing the first-made draft later does not move it.
    client.patch(f"/a/{art['id']}/drafts/{second['id']}", json={"text": "2nd"}, headers=as_author())
    r = review(client, art["id"], {"author": "Petrov", "text": "Overall fine"})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    made = r.json()
    assert [(c["n"], c["author"], c["text"], c["anchor"]) for c in made] == [
        (1, "Ivan", "first", ELEMENT),
        (2, "anonymous", "2nd", None),
        (3, "Petrov", "Overall fine", None),
    ]
    assert all(c["mine"] and not c["read"] and not c["resolved"] for c in made)
    assert all(c["created_at"] for c in made)
    assert drafts(client, art["id"]).json() == []
    assert len(drafts(client, art["id"], OTHER).json()) == 1

    base = f"/a/{art['id']}/comments"
    assert [c["mine"] for c in client.get(base, headers=as_author()).json()] == [True] * 3
    assert [c["mine"] for c in client.get(base, headers=as_author(OTHER)).json()] == [False] * 3
    assert [c["mine"] for c in client.get(base).json()] == [False] * 3
    owner = owner_comments(client, keys["alice"], art["id"]).json()
    assert [(c["n"], c["mine"], c["read"]) for c in owner] == [(1, False, True)] + [
        (n, False, True) for n in (2, 3)
    ]
    # The public list never marks read, and its answer has no edit fields.
    assert set(made[0]) == {
        "n",
        "author",
        "text",
        "anchor",
        "created_at",
        "read",
        "resolved",
        "before_replace",
        "mine",
        "for_agent",
        "agent",
        "approved_by",
        "rejected_by",
        "replies",
    }
    assert not any(c["for_agent"] for c in made)
    # Only the general comment, no drafts: fine too.
    r = review(client, art["id"], {"author": "", "text": "One more"})
    assert [(c["n"], c["author"]) for c in r.json()] == [(4, "anonymous")]
    listed = client.get("/api/v1/artifacts", headers=auth(keys["alice"])).json()
    assert (listed[0]["comments"], listed[0]["open_comments"]) == (4, 4)


def test_a_review_sent_to_the_agent_marks_its_comments(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>Hi</p>")
    add_draft(client, art["id"], {"text": "for the agent"})
    sent = review(client, art["id"], {"author": "Ivan", "agent": True}).json()
    published = review(client, art["id"], {"author": "Ivan", "text": "just a note"}).json()
    assert [c["for_agent"] for c in sent + published] == [True, False]
    owner = owner_comments(client, keys["alice"], art["id"]).json()
    assert [(c["n"], c["for_agent"]) for c in owner] == [(1, True), (2, False)]
    r = review(client, art["id"], {"author": "Ivan", "text": "x", "agent": "yes"})
    assert r.status_code == 400


def test_review_with_nothing_to_publish(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    add_draft(client, art["id"], key=OTHER)  # another key's draft is not ours
    r = review(client, art["id"])
    assert (r.status_code, r.json()["error"]) == (400, "bad_request")
    for body in ({"author": "x", "text": ""}, {"author": "x", "text": "  "}, {"text": "x"}, {}):
        assert review(client, art["id"], body).status_code == 400, body
    assert client.get(f"/a/{art['id']}/comments").json() == []


def test_review_is_all_or_nothing_under_the_comment_limit(env, monkeypatch):
    client, store, keys = env
    monkeypatch.setattr("agent_artifacts.store.MAX_COMMENTS", 3)
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"])
    add_draft(client, art["id"], {"text": "a"})
    add_draft(client, art["id"], {"text": "b"})
    r = review(client, art["id"], {"author": "x", "text": "general"})
    assert (r.status_code, r.json()["error"]) == (409, "too_many_comments")
    assert [c["n"] for c in client.get(f"/a/{art['id']}/comments").json()] == [1]
    assert len(drafts(client, art["id"]).json()) == 2
    assert [c["n"] for c in review(client, art["id"]).json()] == [2, 3]
    # A deleted number is never handed out again.
    url = f"/api/v1/artifacts/{art['id']}/comments/3"
    assert client.delete(url, headers=auth(keys["alice"])).status_code == 204
    assert comment(client, art["id"])["n"] == 4


def test_before_replace_counts_from_when_the_draft_was_made(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>")
    comment(client, art["id"], "published before")
    add_draft(client, art["id"], {"text": "drafted before"})
    client.clock.now += 10
    upload(client, keys["alice"], "a.html", b"<p>new", artifact_id=art["id"])
    client.clock.now += 10
    add_draft(client, art["id"], {"text": "drafted after"})
    review(client, art["id"], {"author": "x", "text": "general after"})
    found = client.get(f"/a/{art['id']}/comments").json()
    assert [(c["text"], c["before_replace"]) for c in found] == [
        ("published before", True),
        ("drafted before", True),
        ("drafted after", False),
        ("general after", False),
    ]


@pytest.mark.parametrize(
    ("first", "anchor", "then", "kept"),
    [
        (("a.html", b"<p>"), ELEMENT, ("a.png", PNG), False),
        (("a.png", PNG), POINT, ("a.html", b"<p>"), False),
        (("a.png", PNG), POINT, ("a.pdf", b"%PDF-1.4"), False),
        (("a.html", b"<p>"), ELEMENT, ("a.md", b"# Hi"), True),
        (("a.png", PNG), POINT, ("b.png", PNG), True),
    ],
)
def test_an_anchor_that_no_longer_fits_becomes_the_whole_file(env, first, anchor, then, kept):
    client, _, keys = env
    art = upload(client, keys["alice"], *first)
    add_draft(client, art["id"], {"text": "x", "anchor": anchor})
    upload(client, keys["alice"], *then, artifact_id=art["id"])
    # The draft itself keeps what was sent until it is published.
    assert drafts(client, art["id"]).json()[0]["anchor"] == anchor
    [c] = review(client, art["id"]).json()
    assert c["anchor"] == (anchor if kept else None)


def test_public_list_leaves_resolved_comments_out(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"], "one")
    comment(client, art["id"], "two")
    url = f"/api/v1/artifacts/{art['id']}/comments/1"
    r = client.patch(url, json={"resolved": True}, headers=auth(keys["alice"]))
    assert r.json()["resolved"] is True and r.json()["mine"] is False
    assert [c["n"] for c in client.get(f"/a/{art['id']}/comments").json()] == [2]
    assert [c["n"] for c in owner_comments(client, keys["alice"], art["id"]).json()] == [1, 2]


# What a draft takes


@pytest.mark.parametrize(
    ("filename", "data", "anchor", "ok"),
    [
        ("a.png", PNG, {"type": "point", "x": 0, "y": 1}, True),
        ("a.png", PNG, {"type": "point", "x": 1.5, "y": 0}, False),
        ("a.png", PNG, {"type": "point", "x": -0.1, "y": 0}, False),
        ("a.png", PNG, {"type": "point", "x": True, "y": 0}, False),
        ("a.png", PNG, {"type": "point", "x": 0.1, "y": 0.1, "z": 0}, False),
        ("a.png", PNG, ELEMENT, False),
        ("a.md", b"# Hi", ELEMENT, True),
        ("a.html", b"<p>", ELEMENT, True),
        ("a.txt", b"x", ELEMENT, True),
        ("a.md", b"# Hi", ELEMENT | {"selector": "x" * 513}, False),
        ("a.md", b"# Hi", ELEMENT | {"text": "x" * 201}, False),
        ("a.md", b"# Hi", ELEMENT | {"width": "1280"}, False),
        ("a.md", b"# Hi", ELEMENT | {"width": 0}, False),
        ("a.html", b"<p>", {"type": "point", "x": 0, "y": 0}, False),
        ("a.html", b"<p>", "main > p", False),
        ("a.pdf", b"%PDF-1.4", {"type": "point", "x": 0, "y": 0}, False),
        ("a.svg", b"<svg/>", ELEMENT, False),
        ("a.zip", b"PK\x03\x04", ELEMENT, False),
        ("a.zip", b"PK\x03\x04", None, True),
        ("a.html", b"<p>", ELEMENT | {"screen": "Note", "hash": "#note"}, True),
        ("a.html", b"<p>", ELEMENT | {"screen": "x" * 81}, False),
        ("a.html", b"<p>", ELEMENT | {"hash": "note"}, False),
        ("a.html", b"<p>", ELEMENT | {"hash": "#"}, True),
        ("a.html", b"<p>", ELEMENT | {"hash": "#%D0%B7"}, True),
        ("a.html", b"<p>", ELEMENT | {"hash": "#заметка"}, False),
        ("a.html", b"<p>", ELEMENT | {"hash": "#a b"}, False),
        ("a.html", b"<p>", ELEMENT | {"hash": "#" + "x" * 200}, False),
        ("a.html", b"<p>", ELEMENT | {"screen": "two\nlines"}, False),
        ("a.png", PNG, {"type": "point", "x": 0, "y": 0, "screen": "x"}, False),
    ],
)
def test_draft_anchor_follows_the_file_type(env, filename, data, anchor, ok):
    client, _, keys = env
    art = upload(client, keys["alice"], filename, data)
    r = add_draft(client, art["id"], {"text": "x", "anchor": anchor})
    assert r.status_code == (201 if ok else 400)
    if not ok:
        assert r.json()["error"] == "bad_request"


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 2001},
        {"text": "ok", "author": "a" * 65},
        {"text": "bell\x07"},
        {"text": 5},
        {"text": "ok", "extra": 1},
        {"text": "x", "author": "two\nlines"},
        {"text": "x", "author": "tab\there"},
        {"text": "x", "anchor": ELEMENT | {"selector": "p\n"}},
        {"text": "x", "anchor": ELEMENT | {"text": "a\tb"}},
        {"text": "price ‮1001"},
        {"text": "a b"},
        {"text": "x", "author": "⁦name"},
        [],
    ],
)
def test_draft_fields_are_checked(env, body):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>")
    assert add_draft(client, art["id"], body).json()["error"] == "bad_request"
    d = add_draft(client, art["id"]).json()
    if isinstance(body, dict) and set(body) == {"text"}:
        url = f"/a/{art['id']}/drafts/{d['id']}"
        assert client.patch(url, json=body, headers=as_author()).status_code == 400
    if isinstance(body, dict) and set(body) <= {"text", "author"} and "anchor" not in body:
        assert review(client, art["id"], {"author": "x"} | body).status_code == 400
    assert client.get(f"/a/{art['id']}/comments").json() == []


def test_draft_limits_count_code_points_and_keep_line_breaks(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    body = {"author": "Ж" * 64, "text": "ё" * 1996 + "\r\n\tx"}
    assert add_draft(client, art["id"], body).status_code == 201
    [c] = review(client, art["id"], {"author": "Ж" * 64}).json()
    assert c["text"].endswith("\r\n\tx")


@pytest.mark.parametrize("path", ["drafts", "review"])
def test_body_rules(env, path):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    url = f"/a/{art['id']}/{path}"
    json_type = as_author() | {"Content-Type": "application/json"}
    r = client.post(
        url, content=b'{"text": "x"}', headers=as_author() | {"Content-Type": "text/plain"}
    )
    assert (r.status_code, r.json()["error"]) == (400, "bad_request")
    r = client.post(url, content=b"{" * 16385, headers=json_type)
    assert (r.status_code, r.json()["error"]) == (413, "too_large")
    r = client.post(url, content=iter([b'{"text": "x"}']), headers=json_type)
    assert (r.status_code, r.json()["error"]) == (411, "bad_request")
    lone = b'{"text": "lone \\ud800"}'  # valid JSON, but not encodable as UTF-8
    assert client.post(url, content=lone, headers=json_type).status_code == 400
    r = client.post(url, content=b"[" * 16000, headers=json_type)
    assert r.status_code == 400  # nested too deep for the parser
    # Counted as it arrives, whatever Content-Length claims.
    big = b'{"text": "' + b"x" * 17000 + b'"}'
    r = client.post(url, content=iter([big]), headers=json_type | {"Content-Length": "20"})
    assert (r.status_code, r.json()["error"]) == (413, "too_large")
    assert drafts(client, art["id"]).json() == []


def test_head_old_calls_and_other_methods_never_write(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    base = f"/a/{art['id']}"
    body = b'{"text": "sneaky"}'
    json_type = as_author() | {"Content-Type": "application/json"}
    r = client.request("HEAD", f"{base}/drafts", content=body, headers=json_type)
    assert r.status_code == 405 and r.headers["allow"] == "GET, POST"
    r = client.request("HEAD", f"{base}/review", content=body, headers=json_type)
    assert r.status_code == 405 and r.headers["allow"] == "POST"
    # The earlier public calls are gone: no posting straight to the list,
    # no edit, delete or resolve of a published comment from the viewer.
    r = client.post(f"{base}/comments", json={"text": "x"}, headers=as_author())
    assert (r.status_code, r.json()["error"]) == (405, "bad_request")
    assert r.headers["cache-control"] == "no-store"
    comment(client, art["id"])
    for method in ("GET", "PATCH", "DELETE"):
        r = client.request(method, f"{base}/comments/1", json={"resolved": True})
        assert (r.status_code, r.json()["error"]) == (405, "bad_request"), method
        assert r.headers["cache-control"] == "no-store"
    [c] = client.get(f"{base}/comments").json()
    assert not c["resolved"] and drafts(client, art["id"]).json() == []
    r = client.head(f"/api/v1/artifacts/{art['id']}/comments", headers=auth(keys["alice"]))
    assert r.status_code == 405
    assert client.get(f"{base}/comments").json()[0]["read"] is False


# The owner's calls


def test_owner_comment_and_draft_calls_are_for_the_owner_only(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"])
    add_draft(client, art["id"])
    base = f"/api/v1/artifacts/{art['id']}"
    for method, path in (("GET", "comments"), ("DELETE", "drafts")):
        r = client.request(method, f"{base}/{path}")
        assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
        r = client.request(method, f"{base}/{path}", headers=auth(keys["bob"]))
        assert (r.status_code, r.json()["error"]) == (404, "not_found")
    r = client.patch(f"{base}/comments/1", json={"resolved": True}, headers=auth(keys["bob"]))
    assert r.status_code == 404
    assert client.delete(f"{base}/comments/1", headers=auth(keys["bob"])).status_code == 404
    # Bob's look did not mark it read, nor drop the draft.
    assert client.get(f"/a/{art['id']}/comments").json()[0]["read"] is False
    assert len(drafts(client, art["id"]).json()) == 1


def test_owner_drops_every_draft_unread(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    other = upload(client, keys["alice"], "b.txt", b"x")
    comment(client, art["id"])
    for key in (KEY, OTHER):
        add_draft(client, art["id"], key=key)
    add_draft(client, other["id"])
    r = client.delete(f"/api/v1/artifacts/{art['id']}/drafts", headers=auth(keys["alice"]))
    assert r.status_code == 204 and r.headers["cache-control"] == "no-store"
    assert drafts(client, art["id"]).json() == drafts(client, art["id"], OTHER).json() == []
    assert len(drafts(client, other["id"]).json()) == 1
    assert len(client.get(f"/a/{art['id']}/comments").json()) == 1
    r = client.delete(f"/api/v1/artifacts/{'a' * 22}/drafts", headers=auth(keys["alice"]))
    assert r.status_code == 404


def test_comments_and_drafts_go_with_the_artifact(env):
    client, store, keys = env
    arts = [upload(client, keys["alice"], f"{i}.txt", b"x") for i in range(3)]
    for a in arts:
        comment(client, a["id"])
        add_draft(client, a["id"])
    client.delete(f"/api/v1/artifacts/{arts[0]['id']}", headers=auth(keys["alice"]))
    with store._conn() as db:
        db.execute("UPDATE artifacts SET expires_at = 0 WHERE id = ?", (arts[1]["id"],))
    assert client.get(f"/a/{arts[1]['id']}/comments").json()["error"] == "not_found"
    assert drafts(client, arts[1]["id"]).json()["error"] == "not_found"
    store.purge_expired()

    def left():
        with store._conn() as db:
            comments = [r[0] for r in db.execute("SELECT artifact_id FROM comments")]
        return comments, [r[0] for r in draft_rows(store)]

    assert left() == ([arts[2]["id"]], [arts[2]["id"]])
    store.remove_agent("alice")
    assert left() == ([], [])


# Databases made by earlier versions

# The schema as released in a255ff7 (in production before comments).
SCHEMA_A255FF7 = """
CREATE TABLE agents (
    name TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    admin INTEGER NOT NULL DEFAULT 0,
    quota_bytes INTEGER,
    last_upload_at REAL
);
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX artifacts_owner ON artifacts(owner);
CREATE INDEX artifacts_expires ON artifacts(expires_at);
CREATE TABLE invites (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

# The schema of b249ca8 (main before drafts): comments with edit tokens.
SCHEMA_B249CA8 = """
CREATE TABLE agents (
    name TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    admin INTEGER NOT NULL DEFAULT 0,
    quota_bytes INTEGER,
    last_upload_at REAL
);
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    comment_seq INTEGER NOT NULL DEFAULT 0,
    replaced_at REAL
);
CREATE INDEX artifacts_owner ON artifacts(owner);
CREATE INDEX artifacts_expires ON artifacts(expires_at);
CREATE TABLE invites (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE comments (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    anchor TEXT,
    edit_token_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    edited_at REAL,
    read_at REAL,
    resolved_at REAL,
    PRIMARY KEY (artifact_id, n)
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

ART = "a" * 22


def make_old_database(tmp_path, schema, comment_seq):
    db = sqlite3.connect(tmp_path / "meta.sqlite")
    db.executescript(schema)
    db.execute("INSERT INTO agents (name, key_hash) VALUES ('old', 'h')")
    columns = "id, owner, name, kind, size, created_at, expires_at"
    db.execute(
        f"INSERT INTO artifacts ({columns}) VALUES (?, 'old', 'a.html', 'html', 3, 0, 9e9)",  # noqa: S608
        (ART,),
    )
    if comment_seq:
        db.execute("UPDATE artifacts SET comment_seq = ?, replaced_at = 100 WHERE id = ?", (2, ART))
        db.execute(
            "INSERT INTO comments (artifact_id, n, author, text, anchor, edit_token_hash, "
            "created_at, edited_at) VALUES (?, 2, 'p', 'old one', NULL, 'tokenhash', 50, 60)",
            (ART,),
        )
    db.commit()
    db.close()
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / ART).write_bytes(b"<p>")


@pytest.mark.parametrize("schema", ["a255ff7", "b249ca8"])
def test_earlier_databases_open_and_work(tmp_path, schema):
    with_comments = schema == "b249ca8"
    make_old_database(tmp_path, SCHEMA_B249CA8 if with_comments else SCHEMA_A255FF7, with_comments)
    store = Store(tmp_path, max_total_bytes=10**6)
    Store(tmp_path, max_total_bytes=10**6)  # opening again changes nothing
    with store._conn() as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(comments)")}
    assert {"author_key_hash", "written_at"} <= columns and "edit_token_hash" not in columns
    config = Config(data_dir=tmp_path, public_url="https://art.example.org")
    with TestClient(create_app(config, store)) as client:
        before = client.get(f"/a/{ART}/comments", headers=as_author()).json()
        if with_comments:
            # An edit token's hash never passes for an author key.
            assert [(c["n"], c["mine"], c["before_replace"]) for c in before] == [(2, False, True)]
        else:
            assert before == []
        assert add_draft(client, ART, {"text": "new", "anchor": ELEMENT}).status_code == 201
        made = review(client, ART).json()
        assert [(c["n"], c["mine"], c["before_replace"]) for c in made] == [
            (3 if with_comments else 1, True, False)
        ]
    assert store.get(ART).comments == (2 if with_comments else 1)
    # Replies and decisions work on what was there before.
    n = made[0]["n"]
    store.add_draft(ART, "h", author="x", text="", anchor=None, kind="approve", of_n=n)
    [c] = store.publish(ART, "h", author="x", text=None, for_agent=True)
    assert (c.approved_by, c.agent) == ("x", "waiting")
    assert store.agent_reply(ART, n, "done", "done", owner="old").replies[0].by_agent
    assert store.drop_drafts(ART, owner="old")


def test_a_database_from_before_chats_keeps_its_drafts(tmp_path):
    # Made by the current code, then the chat columns and table taken away:
    # the schema of f21924c, with a draft and a comment in it.
    store = Store(tmp_path, max_total_bytes=10**6)
    store.add_agent("old")
    art = store.put(io.BytesIO(b"x"), owner="old", name="a.txt", kind="txt", size=1)
    store.add_draft(art.id, "h", author="p", text="published", anchor=None)
    store.publish(art.id, "h", author="p", text=None)
    for text in ("sent", "sent, resolved"):  # 2 and 3, sent to the agent
        store.add_draft(art.id, "h", author="p", text=text, anchor=None)
    store.publish(art.id, "h", author="p", text=None, for_agent=True)
    store.resolve_comment(art.id, 3, True, owner="old")
    store.add_draft(art.id, "h", author="p", text="pending", anchor=None)
    with store._conn() as db:
        db.execute("DROP TABLE replies")
        for table, column in (
            ("drafts", "kind"),
            ("drafts", "of_n"),
            ("comments", "approved_by"),
            ("comments", "rejected_by"),
            ("comments", "agent"),
        ):
            db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    store = Store(tmp_path, max_total_bytes=10**6)
    # What was sent to the agent and is still open waits for it.
    assert [(c.n, c.agent) for c in store.comments(art.id, owner="old")] == [
        (1, None),
        (2, "waiting"),
        (3, None),
    ]
    Store(tmp_path, max_total_bytes=10**6)  # only when the column is added
    store.take(art.id, owner="old")
    assert store.comments(art.id, owner="old")[1].agent == "working"
    Store(tmp_path, max_total_bytes=10**6)
    assert store.comments(art.id, owner="old")[1].agent == "working"
    [d] = store.drafts(art.id, "h")
    assert (d.kind, d.of_n, d.text) == ("comment", None, "pending")
    store.add_draft(art.id, "k", author="q", text="", anchor=None, kind="approve", of_n=1)
    [c] = store.publish(art.id, "k", author="q", text=None)
    assert (c.n, c.approved_by) == (1, "q")
    assert [c.n for c in store.publish(art.id, "h", author="p", text=None)] == [4]


def test_a_counter_added_behind_existing_comments_starts_after_them(tmp_path):
    # Comments in a database whose artifacts have no counter yet: the counter
    # added on opening starts after the highest number, not at 0.
    schema = SCHEMA_B249CA8.replace(
        "    expires_at REAL NOT NULL,\n    comment_seq INTEGER NOT NULL DEFAULT 0,\n"
        "    replaced_at REAL\n",
        "    expires_at REAL NOT NULL\n",
    )
    assert "comment_seq" not in schema
    db = sqlite3.connect(tmp_path / "meta.sqlite")
    db.executescript(schema)
    db.execute("INSERT INTO agents (name, key_hash) VALUES ('old', 'h')")
    for art_id in (ART, "b" * 22):
        db.execute("INSERT INTO artifacts VALUES (?, 'old', 'a.txt', 'txt', 1, 0, 9e9)", (art_id,))
    for n in (2, 5):
        db.execute(
            "INSERT INTO comments (artifact_id, n, author, text, edit_token_hash, created_at) "
            "VALUES (?, ?, 'p', 'old', 'tokenhash', 50)",
            (ART, n),
        )
    db.commit()
    db.close()
    store = Store(tmp_path, max_total_bytes=10**6)
    for art_id, expected in ((ART, 6), ("b" * 22, 1)):
        store.add_draft(art_id, "h", author="x", text="new", anchor=None)
        assert [c.n for c in store.publish(art_id, "h", author="x", text=None)] == [expected]


def test_an_element_anchor_keeps_its_screen_and_hash(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>")
    anchor = ELEMENT | {"screen": "Shopping list", "hash": "#note"}
    r = add_draft(client, art["id"], {"text": "x", "anchor": anchor})
    assert r.status_code == 201
    assert r.json()["anchor"] == anchor
    # Empty ones are dropped, not stored.
    r = add_draft(client, art["id"], {"text": "y", "anchor": ELEMENT | {"screen": "", "hash": ""}})
    assert r.json()["anchor"] == ELEMENT


# Chats, approvals and the agent's work


def decide(client, art_id, kind, n, key=OTHER, author="Olga"):
    return add_draft(client, art_id, {"author": author, "kind": kind, "of": n}, key)


def reply_draft(client, art_id, n, text, key=OTHER):
    body = {"author": "Olga", "kind": "reply", "of": n, "text": text}
    return add_draft(client, art_id, body, key)


def agent_reply(client, key, art_id, n, body):
    url = f"/api/v1/artifacts/{art_id}/comments/{n}/reply"
    return client.post(url, json=body, headers=auth(key))


def public(client, art_id, key=KEY):
    return {c["n"]: c for c in client.get(f"/a/{art_id}/comments", headers=as_author(key)).json()}


def test_draft_kinds_are_checked(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>")
    comment(client, art["id"])  # 1, by KEY
    url = f"/a/{art['id']}/drafts"
    r = reply_draft(client, art["id"], 1, " thanks ")
    assert r.status_code == 201
    assert {k: r.json()[k] for k in ("kind", "of", "text", "anchor")} == {
        "kind": "reply",
        "of": 1,
        "text": "thanks",
        "anchor": None,
    }
    d = decide(client, art["id"], "approve", 1).json()
    assert (d["kind"], d["of"], d["text"]) == ("approve", 1, None)
    assert add_draft(client, art["id"], {"text": "x"}).json()["kind"] == "comment"
    assert add_draft(client, art["id"], {"text": "x"}).json()["of"] is None
    bad = [
        {"kind": "nope", "of": 1},
        {"kind": "reply", "of": 1},  # no text
        {"kind": "reply", "of": 1, "text": " "},
        {"kind": "reply", "of": 1, "text": "x", "anchor": ELEMENT},
        {"kind": "reply", "text": "x"},  # no comment
        {"kind": "approve", "of": 1, "text": "x"},
        {"kind": "approve", "of": "1"},
        {"kind": "approve", "of": True},
        {"kind": "comment", "of": 1, "text": "x"},
    ]
    for body in bad:
        r = client.post(url, json=body, headers=as_author(OTHER))
        assert (r.status_code, r.json()["error"]) == (400, "bad_request"), body
    # Not an open comment of this file.
    for n in (0, 2, 10**12):
        r = decide(client, art["id"], "reject", n)
        assert (r.status_code, r.json()["error"]) == (404, "not_found"), n
    # Not one's own comment; not accepted before the agent answers.
    r = decide(client, art["id"], "approve", 1, key=KEY)
    assert (r.status_code, r.json()["error"]) == (400, "bad_request")
    r = decide(client, art["id"], "accept", 1, key=KEY)
    assert (r.status_code, r.json()["error"]) == (400, "bad_request")
    # A decision has no text to edit; a reply has.
    r = client.patch(f"{url}/{d['id']}", json={"text": "x"}, headers=as_author(OTHER))
    assert r.status_code == 404
    reply = drafts(client, art["id"], OTHER).json()[0]
    r = client.patch(f"{url}/{reply['id']}", json={"text": "edited"}, headers=as_author(OTHER))
    assert r.json()["text"] == "edited"


def test_a_new_decision_replaces_the_old_one(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"])
    comment(client, art["id"])
    decide(client, art["id"], "approve", 1)
    decide(client, art["id"], "approve", 2)
    reply_draft(client, art["id"], 1, "a")
    reply_draft(client, art["id"], 1, "b")
    decide(client, art["id"], "reject", 1)
    left = [(d["kind"], d["of"]) for d in drafts(client, art["id"], OTHER).json()]
    assert left == [("approve", 2), ("reply", 1), ("reply", 1), ("reject", 1)]
    # Undo is deleting the draft.
    [rejection] = [d for d in drafts(client, art["id"], OTHER).json() if d["kind"] == "reject"]
    url = f"/a/{art['id']}/drafts/{rejection['id']}"
    assert client.delete(url, headers=as_author(OTHER)).status_code == 204


def test_a_review_sent_to_the_agent_makes_new_replied_and_approved_comments_wait(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    for _ in range(4):
        comment(client, art["id"])  # 1..4 by KEY, only published
    reply_draft(client, art["id"], 1, "me too")
    decide(client, art["id"], "approve", 2)
    decide(client, art["id"], "reject", 3)
    add_draft(client, art["id"], {"text": "new"}, OTHER)
    made = review(client, art["id"], {"author": "Olga", "agent": True}, key=OTHER).json()
    state = {c["n"]: (c["for_agent"], c["agent"], c["approved_by"], c["rejected_by"]) for c in made}
    assert state == {
        1: (True, "waiting", None, None),
        2: (True, "waiting", "Olga", None),
        3: (False, None, None, "Olga"),
        5: (True, "waiting", None, None),
    }
    [r] = public(client, art["id"])[1]["replies"]
    assert (r["author"], r["text"], r["mine"], r["agent"]) == ("Olga", "me too", False, False)
    assert public(client, art["id"], OTHER)[1]["replies"][0]["mine"] is True
    # A rejected comment stays listed; comment 4 was not touched.
    assert sorted(public(client, art["id"])) == [1, 2, 3, 4, 5]
    assert public(client, art["id"])[4]["agent"] is None


def test_a_review_only_published_changes_no_agent_state(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"])
    comment(client, art["id"])
    reply_draft(client, art["id"], 1, "note")
    decide(client, art["id"], "approve", 2)
    made = review(client, art["id"], {"author": "Olga"}, key=OTHER).json()
    assert [(c["n"], c["for_agent"], c["agent"], c["approved_by"]) for c in made] == [
        (1, False, None, None),
        (2, False, None, "Olga"),
    ]
    assert len(made[0]["replies"]) == 1
    # A later rejection replaces the approval.
    decide(client, art["id"], "reject", 2, key="r" * 22, author="Ivan")
    [c] = review(client, art["id"], {"author": "Ivan"}, key="r" * 22).json()
    assert (c["approved_by"], c["rejected_by"]) == (None, "Ivan")


def test_take_and_the_agent_reply_and_accept(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    add_draft(client, art["id"], {"text": "for the agent"})
    review(client, art["id"], {"author": "Ivan", "agent": True})  # 1, waiting
    comment(client, art["id"])  # 2, only published
    base = f"/api/v1/artifacts/{art['id']}"
    r = client.post(f"{base}/take", headers=auth(keys["alice"]))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert [(c["n"], c["agent"]) for c in r.json()] == [(1, "working")]
    assert client.post(f"{base}/take", headers=auth(keys["alice"])).json() == []
    assert public(client, art["id"])[2]["agent"] is None
    assert client.post(f"{base}/take", headers=auth(keys["bob"])).status_code == 404
    assert client.post(f"{base}/take").status_code == 401

    r = agent_reply(client, keys["alice"], art["id"], 1, {"text": "Made it green"})
    assert r.status_code == 201
    c = r.json()
    assert c["agent"] == "done"
    assert [(x["author"], x["text"], x["agent"], x["mine"]) for x in c["replies"]] == [
        ("agent", "Made it green", True, False)
    ]
    r = agent_reply(client, keys["alice"], art["id"], 1, {"text": "Which green?", "state": "asked"})
    assert r.json()["agent"] == "asked"
    for body in ({"text": "x", "state": "working"}, {"text": ""}, {"state": "done"}):
        assert agent_reply(client, keys["alice"], art["id"], 1, body).status_code == 400, body
    assert agent_reply(client, keys["alice"], art["id"], 9, {"text": "x"}).status_code == 404
    assert agent_reply(client, keys["bob"], art["id"], 1, {"text": "x"}).status_code == 404

    # Accepting closes the comment: it leaves the viewer, the owner keeps it.
    assert decide(client, art["id"], "accept", 1, key=KEY).status_code == 201
    assert review(client, art["id"], {"author": "Ivan"}).json() == []
    assert sorted(public(client, art["id"])) == [2]
    owner = owner_comments(client, keys["alice"], art["id"]).json()
    assert [(c["n"], c["resolved"]) for c in owner] == [(1, True), (2, False)]
    assert len(owner[0]["replies"]) == 2


def test_replies_and_decisions_on_a_closed_comment_are_dropped(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"])
    comment(client, art["id"])
    reply_draft(client, art["id"], 1, "late")
    decide(client, art["id"], "approve", 2)
    base = f"/api/v1/artifacts/{art['id']}/comments"
    client.patch(f"{base}/1", json={"resolved": True}, headers=auth(keys["alice"]))
    client.delete(f"{base}/2", headers=auth(keys["alice"]))
    r = review(client, art["id"], {"author": "Olga", "agent": True}, key=OTHER)
    assert (r.status_code, r.json()) == (201, [])
    assert drafts(client, art["id"], OTHER).json() == []
    [c] = owner_comments(client, keys["alice"], art["id"]).json()
    assert (c["replies"], c["agent"]) == ([], None)


def test_too_many_replies_refuses_the_whole_review(env, monkeypatch):
    client, _, keys = env
    monkeypatch.setattr("agent_artifacts.store.MAX_REPLIES", 2)
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"])
    assert agent_reply(client, keys["alice"], art["id"], 1, {"text": "one"}).status_code == 201
    reply_draft(client, art["id"], 1, "two")
    reply_draft(client, art["id"], 1, "three")
    add_draft(client, art["id"], {"text": "new"}, OTHER)
    r = review(client, art["id"], {"author": "Olga"}, key=OTHER)
    assert (r.status_code, r.json()["error"]) == (409, "too_many_replies")
    assert len(public(client, art["id"])) == 1 and len(drafts(client, art["id"], OTHER).json()) == 3
    assert agent_reply(client, keys["alice"], art["id"], 1, {"text": "two"}).status_code == 201
    r = agent_reply(client, keys["alice"], art["id"], 1, {"text": "three"})
    assert (r.status_code, r.json()["error"]) == (409, "too_many_replies")


def test_a_file_holds_a_bounded_number_of_replies(env, monkeypatch):
    client, _, keys = env
    monkeypatch.setattr("agent_artifacts.store.MAX_FILE_REPLIES", 3)
    art = upload(client, keys["alice"], "a.txt", b"x")
    for _ in range(3):
        comment(client, art["id"])
    for n in (1, 2):
        assert agent_reply(client, keys["alice"], art["id"], n, {"text": "ok"}).status_code == 201
    reply_draft(client, art["id"], 3, "a")
    reply_draft(client, art["id"], 3, "b")
    r = review(client, art["id"], {"author": "Olga"}, key=OTHER)
    assert (r.status_code, r.json()["error"]) == (409, "too_many_replies")
    assert agent_reply(client, keys["alice"], art["id"], 3, {"text": "ok"}).status_code == 201
    r = agent_reply(client, keys["alice"], art["id"], 3, {"text": "more"})
    assert (r.status_code, r.json()["error"]) == (409, "too_many_replies")


def answered(client, keys, text="x"):
    """A file with comment 1 sent to the agent and answered: done."""
    art = upload(client, keys["alice"], "a.txt", b"x")
    add_draft(client, art["id"], {"text": text})
    review(client, art["id"], {"author": "Ivan", "agent": True})
    agent_reply(client, keys["alice"], art["id"], 1, {"text": "done"})
    return art


def test_an_accept_after_someone_sent_the_comment_back_is_dropped(env):
    client, _, keys = env
    art = answered(client, keys)
    assert decide(client, art["id"], "accept", 1, key=KEY).status_code == 201
    # Someone else asks for more and sends it to the agent first.
    reply_draft(client, art["id"], 1, "not yet")
    review(client, art["id"], {"author": "Olga", "agent": True}, key=OTHER)
    assert review(client, art["id"], {"author": "Ivan"}).json() == []
    c = public(client, art["id"])[1]
    assert (c["resolved"], c["agent"]) == (False, "waiting")
    assert drafts(client, art["id"]).json() == []


def test_a_reply_in_the_same_review_cancels_the_accept(env):
    client, _, keys = env
    art = answered(client, keys)
    decide(client, art["id"], "accept", 1, key=KEY)
    reply_draft(client, art["id"], 1, "one more thing", key=KEY)
    [c] = review(client, art["id"], {"author": "Ivan", "agent": True}).json()
    assert (c["n"], c["resolved"], c["agent"]) == (1, False, "waiting")
    assert [r["text"] for r in c["replies"]] == ["done", "one more thing"]


def test_a_rejected_comment_is_no_work_for_the_agent(env):
    client, _, keys = env
    art = answered(client, keys)
    add_draft(client, art["id"], {"text": "second"})
    review(client, art["id"], {"author": "Ivan", "agent": True})  # 2, waiting
    decide(client, art["id"], "reject", 2)
    [c] = review(client, art["id"], {"author": "Olga", "agent": True}, key=OTHER).json()
    assert (c["n"], c["rejected_by"], c["agent"]) == (2, "Olga", None)
    taken = client.post(f"/api/v1/artifacts/{art['id']}/take", headers=auth(keys["alice"]))
    assert taken.json() == []


def test_comment_lists_say_when_the_file_was_last_uploaded(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    first = client.get(f"/a/{art['id']}/comments").headers["x-artifact-updated"]
    assert first == art["created_at"]
    client.clock.now += 60
    upload(client, keys["alice"], "a.txt", b"y", artifact_id=art["id"])
    later = client.get(f"/a/{art['id']}/comments").headers["x-artifact-updated"]
    assert later > first
    assert owner_comments(client, keys["alice"], art["id"]).headers["x-artifact-updated"] == later


def test_replies_go_with_their_comment(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x")
    comment(client, art["id"])
    agent_reply(client, keys["alice"], art["id"], 1, {"text": "ok"})
    client.delete(f"/api/v1/artifacts/{art['id']}/comments/1", headers=auth(keys["alice"]))
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM replies").fetchone() == (0,)
