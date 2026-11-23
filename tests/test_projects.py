import re
import sqlite3
import threading

import pytest
from starlette.testclient import TestClient

from agent_artifacts import store as store_module
from agent_artifacts.api import iso
from agent_artifacts.config import Config
from agent_artifacts.store import Store
from agent_artifacts.web import create_app

TTL = 48 * 3600


@pytest.fixture
def env(tmp_path):
    config = Config(data_dir=tmp_path, public_url="https://art.example.org", max_file_bytes=1000)
    store = Store(tmp_path, max_total_bytes=10**6, ttl_seconds=TTL)
    keys = {name: store.add_agent(name) for name in ("alice", "bob")}
    with TestClient(create_app(config, store)) as client:
        yield client, store, keys


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def new_project(client, key, title="Landing"):
    return client.post("/api/v1/projects", headers=auth(key), json={"title": title})


def upload(client, key, filename="a.txt", data=b"x", artifact_id=None, **fields):
    url = "/api/v1/artifacts" + (f"/{artifact_id}" if artifact_id else "")
    method = client.put if artifact_id else client.post
    return method(url, headers=auth(key), files={"file": (filename, data)}, data=fields)


def patch(client, key, art_id, body):
    return client.patch(f"/api/v1/artifacts/{art_id}", headers=auth(key), json=body)


def later(store, seconds):
    clock = store.clock
    store.clock = lambda: clock() + seconds


def test_project_calls_are_the_owners_only(env):
    client, _, keys = env
    r = new_project(client, keys["alice"], " Landing ")
    assert r.status_code == 201
    p = r.json()
    assert p["url"] == f"https://art.example.org/p/{p['id']}" and len(p["id"]) == 22
    assert (p["title"], p["files"], p["used_bytes"]) == ("Landing", 0, 0)
    upload(client, keys["alice"], "a.txt", b"abc", project=p["id"])
    base = f"/api/v1/projects/{p['id']}"
    got = client.get(base, headers=auth(keys["alice"])).json()
    assert (got["files"], got["used_bytes"], len(got["artifacts"])) == (1, 3, 1)
    listed = client.get("/api/v1/projects", headers=auth(keys["alice"])).json()
    assert [x["id"] for x in listed] == [p["id"]] and "artifacts" not in listed[0]
    renamed = client.patch(base, headers=auth(keys["alice"]), json={"title": "Site"})
    assert renamed.json()["title"] == "Site"
    for bad in ({}, {"title": None}, {"title": " "}, {"title": "x" * 201}, {"title": "a‮b"}):
        assert client.patch(base, headers=auth(keys["alice"]), json=bad).status_code == 400
    assert new_project(client, keys["alice"], "​").status_code == 400
    # Another agent's project is missing for bob, in a path and in a field.
    assert client.get("/api/v1/projects", headers=auth(keys["bob"])).json() == []
    for method, body in (("GET", None), ("PATCH", {"title": "x"}), ("DELETE", None)):
        r = client.request(method, base, headers=auth(keys["bob"]), json=body)
        assert (r.status_code, r.json()["error"]) == (404, "not_found"), method
    assert upload(client, keys["bob"], project=p["id"]).status_code == 404
    bob_file = upload(client, keys["bob"]).json()
    assert patch(client, keys["bob"], bob_file["id"], {"project": p["id"]}).status_code == 404
    assert client.get(base, headers=auth(keys["alice"])).json()["title"] == "Site"


def test_membership_and_sections(env):
    client, store, keys = env
    p1 = new_project(client, keys["alice"]).json()["id"]
    p2 = new_project(client, keys["alice"], "Other").json()["id"]
    art = upload(client, keys["alice"], project=p1, section=" Mockups ").json()
    assert (art["project"], art["section"]) == (p1, "Mockups")
    # Replace changes only the fields given.
    kept = upload(client, keys["alice"], "b.txt", b"y", art["id"]).json()
    assert (kept["project"], kept["section"]) == (p1, "Mockups")
    moved = upload(client, keys["alice"], "b.txt", b"y", art["id"], section="Docs").json()
    assert (moved["project"], moved["section"]) == (p1, "Docs")
    # Moving keeps the section unless given; out of a project clears it.
    assert patch(client, keys["alice"], art["id"], {"project": p2}).json()["section"] == "Docs"
    r = patch(client, keys["alice"], art["id"], {"project": p1, "section": "Contracts"})
    assert (r.json()["project"], r.json()["section"]) == (p1, "Contracts")
    assert patch(client, keys["alice"], art["id"], {"section": None}).json()["section"] is None
    out = patch(client, keys["alice"], art["id"], {"project": None}).json()
    assert (out["project"], out["section"]) == (None, None)
    # A section needs the resulting project.
    for body in ({"section": "Docs"}, {"project": None, "section": "Docs"}):
        assert patch(client, keys["alice"], art["id"], body).status_code == 400
    assert (
        patch(client, keys["alice"], art["id"], {"project": p1, "section": "x" * 65}).status_code
        == 400
    )
    assert upload(client, keys["alice"], section="Docs").status_code == 400
    # An unknown project stores nothing.
    for missing in ("A" * 22, "short"):
        assert upload(client, keys["alice"], "c.txt", project=missing).status_code == 404
        r = upload(client, keys["alice"], "c.txt", b"zz", art["id"], project=missing)
        assert r.status_code == 404
    assert [a.id for a in store.list("alice")] == [art["id"]]
    assert store.get(art["id"]).size == 1


def test_a_project_file_never_expires_until_taken_out(env):
    client, store, keys = env
    p = new_project(client, keys["alice"]).json()["id"]
    art = upload(client, keys["alice"], project=p).json()
    assert art["expires_at"] is None
    plain = upload(client, keys["alice"]).json()
    assert patch(client, keys["alice"], plain["id"], {"project": p}).json()["expires_at"] is None
    later(store, 10 * TTL)
    assert store.purge_expired() == 0
    store.add_draft(art["id"], "h", author="p", text="hi", anchor=None)
    assert [c.n for c in store.publish(art["id"], "h", author="p", text=None)] == [1]
    for path in ("", "/raw", "/download", "/comments"):
        assert client.get(f"/a/{art['id']}{path}").status_code == 200, path
    # The header names the project instead of an expiry.
    page = client.get(f"/a/{art['id']}").text
    assert 'id="files-button"' in page and "expires" not in page
    assert store.agent_info("alice").files == 2
    # Taken out: a new lifetime of the current ttl from then.
    store.set_settings(ttl_seconds=3600)
    out = patch(client, keys["alice"], art["id"], {"project": None}).json()
    assert out["expires_at"] == iso(store.clock() + 3600)
    later(store, 3601)
    assert store.purge_expired() == 1
    # An expired file never joins a project.
    assert patch(client, keys["alice"], art["id"], {"project": p}).status_code == 404


def test_deleting_a_project_deletes_its_files_and_comments(env):
    client, store, keys = env
    p = new_project(client, keys["alice"]).json()["id"]
    inside = upload(client, keys["alice"], project=p).json()["id"]
    outside = upload(client, keys["alice"]).json()["id"]
    store.add_draft(inside, "h", author="p", text="hi", anchor=None)
    store.publish(inside, "h", author="p", text=None)
    assert client.delete(f"/api/v1/projects/{p}", headers=auth(keys["alice"])).status_code == 204
    assert client.get(f"/a/{inside}").status_code == 404 and not store.path(inside).exists()
    assert client.get(f"/a/{outside}").status_code == 200
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM comments").fetchone() == (0,)
    assert client.get(f"/api/v1/projects/{p}", headers=auth(keys["alice"])).status_code == 404
    # Removing an agent removes its projects too.
    new_project(client, keys["alice"])
    store.remove_agent("alice")
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone() == (0,)


def test_a_file_lost_from_disk_is_404_then_purged_with_its_comments(env, caplog):
    # A database restored without its files: the rows are there, the files not.
    client, store, keys = env
    p = new_project(client, keys["alice"]).json()["id"]
    art = upload(client, keys["alice"], data=b"12345", project=p).json()
    store.add_draft(art["id"], "h", author="p", text="hi", anchor=None)
    store.publish(art["id"], "h", author="p", text=None)
    store.path(art["id"]).unlink()
    for path in ("", "/raw", "/download"):
        assert client.get(f"/a/{art['id']}{path}").status_code == 404, path
    assert "file of alice's 'a.txt' is missing" in caplog.text
    assert store.agent_info("alice").used_bytes == 5
    assert store.purge_expired() == 1
    assert "removed alice's 'a.txt': its file is missing" in caplog.text
    assert art["id"] not in caplog.text
    assert store.get(art["id"]) is None
    assert store.agent_info("alice").used_bytes == 0
    with sqlite3.connect(store._db) as db:
        assert db.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def test_many_files_missing_at_once_or_unreadable_are_kept(env, caplog, monkeypatch):
    # A files folder missing or half restored is not lost files: nothing goes.
    client, store, keys = env
    p = new_project(client, keys["alice"]).json()["id"]
    ids = [
        upload(client, keys["alice"], project=p).json()["id"]
        for _ in range(store_module.LOST_AT_ONCE + 1)
    ]
    for i in ids:
        store.path(i).unlink()
    assert store.purge_expired() == 0
    assert "files are missing; none removed" in caplog.text
    assert all(store.get(i) for i in ids)
    # A file that cannot be looked at (permissions, I/O) is not lost either.
    real = store_module.os.stat

    def stat(path, *a, **k):
        if str(path).endswith(ids[0]):
            raise PermissionError(13, "denied")
        return real(path, *a, **k)

    monkeypatch.setattr(store_module.os, "stat", stat)
    assert store._lost(ids[0]) is False


def test_a_file_being_uploaded_is_not_purged(env, monkeypatch):
    # The purge runs while an upload holds its row but has not yet put its
    # file in place: it waits for the upload and leaves the file be.
    client, store, keys = env
    p = new_project(client, keys["alice"]).json()["id"]
    purge, purged = None, []
    replace = store_module.os.replace

    def slow_replace(src, dst):
        nonlocal purge
        purge = threading.Thread(target=lambda: purged.append(store.purge_expired()))
        purge.start()
        purge.join(0.3)
        assert purge.is_alive()  # waiting for the upload's write lock
        replace(src, dst)

    monkeypatch.setattr(store_module.os, "replace", slow_replace)
    art = upload(client, keys["alice"], project=p).json()
    purge.join()
    assert purged == [0]
    assert client.get(f"/a/{art['id']}/raw").status_code == 200


def test_limits_answer_too_many(env, monkeypatch):
    client, _, keys = env
    for name, value in (("MAX_PROJECTS", 2), ("MAX_PROJECT_FILES", 2), ("MAX_SECTIONS", 1)):
        monkeypatch.setattr(store_module, name, value)
    p = new_project(client, keys["alice"]).json()["id"]
    new_project(client, keys["alice"])
    r = new_project(client, keys["alice"])
    assert (r.status_code, r.json()["error"]) == (409, "too_many")
    a = upload(client, keys["alice"], project=p, section="One").json()["id"]
    # The same section again is no new one; another is.
    b = upload(client, keys["alice"], project=p, section="One").json()["id"]
    assert upload(client, keys["alice"], project=p).status_code == 409
    assert patch(client, keys["alice"], b, {"section": "Two"}).status_code == 409
    assert patch(client, keys["alice"], b, {"section": None}).status_code == 200
    # A file already in counts once: replacing it stays within the limit.
    assert upload(client, keys["alice"], "a.txt", b"y", a, section="One").status_code == 200


def test_project_files_in_the_page_order(env):
    client, store, keys = env
    p = new_project(client, keys["alice"]).json()["id"]

    def add(title, section=None):
        later(store, 1)
        fields = {"title": title, "project": p} | ({"section": section} if section else {})
        return upload(client, keys["alice"], **fields).json()["id"]

    add("s1")
    add("A1", "Apps")
    add("plain")
    add("D1", "Docs")
    a2 = add("A2", "Apps")
    add("p2")
    # Moving the earliest Apps file away makes Apps later than Docs.
    first = [a for a in store.list("alice") if a.title == "A1"][0].id
    patch(client, keys["alice"], first, {"project": None})
    later(store, 1)
    patch(client, keys["alice"], first, {"project": p, "section": "Apps"})
    got = client.get(f"/api/v1/projects/{p}", headers=auth(keys["alice"])).json()
    assert [a["title"] for a in got["artifacts"]] == ["s1", "plain", "p2", "D1", "A2", "A1"]
    assert got["artifacts"][4]["id"] == a2
    listed = client.get("/api/v1/artifacts", headers=auth(keys["alice"])).json()
    assert {a["project"] for a in listed} == {p}


def test_quota_counts_project_files(env):
    client, store, keys = env
    store.set_quota("alice", 5)
    p = new_project(client, keys["alice"]).json()["id"]
    upload(client, keys["alice"], data=b"abcd", project=p)
    later(store, 10 * TTL)
    r = upload(client, keys["alice"], data=b"xy")
    assert (r.status_code, r.json()["error"], r.json()["used_bytes"]) == (507, "quota_exceeded", 4)
    assert client.get("/api/v1/me", headers=auth(keys["alice"])).json()["used_bytes"] == 4


def test_version_4_keeps_files_and_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_MIGRATIONS", store_module._MIGRATIONS[:3])
    old = Store(tmp_path, max_total_bytes=10**6)
    old.add_agent("old")
    with old._conn() as db:
        db.execute(
            "INSERT INTO artifacts (id, owner, name, title, kind, size, created_at, expires_at, "
            "comment_seq) VALUES (?, 'old', 'a.txt', 'A', 'txt', 1, 0, 9e9, 0)",
            ("a" * 22,),
        )
        db.execute(
            "INSERT INTO comments (artifact_id, n, author, text, author_key_hash, created_at) "
            "VALUES (?, 1, 'p', 'kept', 'h', 5)",
            ("a" * 22,),
        )
        db.execute(
            "INSERT INTO drafts (artifact_id, id, author_key_hash, author, text, created_at, "
            "updated_at) VALUES (?, 'd', 'h', 'p', 'draft', 1e10, 1e10)",
            ("a" * 22,),
        )
    monkeypatch.undo()
    store = Store(tmp_path, max_total_bytes=10**6)
    Store(tmp_path / "fresh", max_total_bytes=10**6)

    def schema(path):
        db = sqlite3.connect(path / "meta.sqlite")
        try:
            return (
                db.execute("PRAGMA user_version").fetchone(),
                sorted((r[0], r[1]) for r in db.execute("SELECT type, name FROM sqlite_master")),
                db.execute("PRAGMA table_info(artifacts)").fetchall(),
            )
        finally:
            db.close()

    assert schema(tmp_path) == schema(tmp_path / "fresh")
    a = store.get("a" * 22)
    assert (a.title, a.expires_at, a.project, a.section, a.comments) == ("A", 9e9, None, None, 1)
    assert len(store.drafts("a" * 22, "h")) == 1
    with store._conn() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        # The rebuilt table is still what the comments and drafts point at:
        # deleting the file takes them along.
        assert db.execute("PRAGMA foreign_key_list(comments)").fetchall()[0][2] == "artifacts"
        db.execute("DELETE FROM artifacts WHERE id = ?", ("a" * 22,))
        assert db.execute("SELECT count(*) FROM comments").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM drafts").fetchone() == (0,)


def test_the_project_page_lists_files_by_section(env):
    client, store, keys = env
    p = new_project(client, keys["alice"], "Landing <b>").json()["id"]
    ids = {}
    for title, section in (
        ("Brief", None),
        ("Contract", "Contracts"),
        ("Estimate", "Contracts"),
        ("Main", "App <mockups>"),
        ("Notes", None),
    ):
        later(store, 1)
        fields = {"title": title, "project": p} | ({"section": section} if section else {})
        ids[title] = upload(client, keys["alice"], "a.md", b"# x", **fields).json()["id"]
    upload(client, keys["alice"], "other.txt", b"x", title="Elsewhere")
    # Public by its link, like a file: no agent key asked.
    r = client.get(f"/p/{p}")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp
    assert "connect-src 'self'" in csp and "unsafe-inline" not in csp
    assert (r.headers["x-robots-tag"], r.headers["referrer-policy"]) == ("noindex", "no-referrer")
    assert r.headers["cache-control"] == "no-store"
    page = r.text
    assert "Landing &lt;b&gt;" in page and "<b>" not in page
    assert "5 files · kept until deleted" in page and "Elsewhere" not in page
    order = [
        page.index(x)
        for x in (
            ids["Brief"],
            ids["Notes"],
            "Contracts</h2>",
            ids["Contract"],
            ids["Estimate"],
            "App &lt;mockups&gt;</h2>",
            ids["Main"],
        )
    ]
    assert order == sorted(order)
    assert page.count('class="sec"') == 2 and page.count('class="pf"') == 5
    assert f'href="/a/{ids["Main"]}"' in page and ">MD<" in page and "3 B · " in page
    # The viewer's menu: Share and Settings; no file to download.
    assert 'data-act="share"' in page and 'data-act="settings"' in page
    assert "Copy link" in page and "Download file" not in page and "/download" not in page


def test_the_project_page_of_a_missing_or_deleted_project_is_404(env):
    client, _, keys = env
    p = new_project(client, keys["alice"]).json()["id"]
    empty = client.get(f"/p/{p}")
    assert empty.status_code == 200 and "No files yet" in empty.text and "0 files" in empty.text
    client.delete(f"/api/v1/projects/{p}", headers=auth(keys["alice"]))
    for path in (f"/p/{p}", f"/p/{'x' * 22}"):
        r = client.get(path)
        assert r.status_code == 404 and "expired or never existed" in r.text, path
        r = client.get(f"{path}/summary")
        assert (r.status_code, r.json()["error"]) == (404, "not_found")


ME, MARIA = "k" * 22, "m" * 22


def test_the_summary_counts_what_waits_for_this_person(env):
    client, store, keys = env
    p = new_project(client, keys["alice"]).json()["id"]
    other = new_project(client, keys["alice"], "Other").json()["id"]
    a = upload(client, keys["alice"], "a.md", b"# a", project=p).json()["id"]
    b = upload(client, keys["alice"], "b.md", b"# b", project=p).json()["id"]
    quiet = upload(client, keys["alice"], "c.md", b"# c", project=p).json()["id"]
    elsewhere = upload(client, keys["alice"], "d.md", b"# d", project=other).json()["id"]

    def publish(art, key, n, agent):
        for i in range(n):
            client.post(f"/a/{art}/drafts", headers={"X-Author-Key": key}, json={"text": f"c{i}"})
        client.post(
            f"/a/{art}/review", headers={"X-Author-Key": key}, json={"author": "x", "agent": agent}
        )

    publish(a, ME, 3, True)  # with the agent: 1-3
    client.post(f"/api/v1/artifacts/{a}/take", headers=auth(keys["alice"]))
    client.post(
        f"/api/v1/artifacts/{a}/comments/1/reply",
        headers=auth(keys["alice"]),
        json={"text": "done", "state": "done"},
    )
    client.patch(
        f"/api/v1/artifacts/{a}/comments/3", headers=auth(keys["alice"]), json={"resolved": True}
    )
    publish(b, MARIA, 2, False)  # Maria's, awaiting approval
    publish(b, ME, 1, False)  # mine, nothing to approve
    publish(elsewhere, MARIA, 1, False)

    def summary(key=None):
        r = client.get(f"/p/{p}/summary", headers={"X-Author-Key": key} if key else {})
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        return r.json()

    zero = {"waiting_you": 0, "with_agent": 0}
    # 1 done, 2 working, 3 resolved; on b, Maria's two wait for me.
    assert summary(ME) == {
        a: {"waiting_you": 1, "with_agent": 1},
        b: {"waiting_you": 2, "with_agent": 0},
        quiet: zero,
    }
    # A stranger: my comment on b awaits approval too.
    assert summary()[b] == {"waiting_you": 3, "with_agent": 0}
    assert summary("bad")[b] == summary()[b]
    # Decided or replied to in my drafts: no longer waiting for me.
    client.post(f"/a/{a}/drafts", headers={"X-Author-Key": ME}, json={"kind": "accept", "of": 1})
    client.post(
        f"/a/{b}/drafts",
        headers={"X-Author-Key": ME},
        json={"kind": "reply", "of": 1, "text": "ok"},
    )
    got = summary(ME)
    assert (got[a]["waiting_you"], got[b]["waiting_you"]) == (0, 1)
    assert summary(MARIA)[a]["waiting_you"] == 1
    assert elsewhere not in got


def test_a_project_files_header_lists_the_project(env):
    client, store, keys = env
    p = new_project(client, keys["alice"], "Landing <b>").json()["id"]
    ids = {}
    for title, section in (
        ("Brief <i>", None),
        ("Contract", "Contracts"),
        ("Main", "App <mockups>"),
    ):
        later(store, 1)
        fields = {"title": title, "project": p} | ({"section": section} if section else {})
        ids[title] = upload(client, keys["alice"], "a.md", b"# x", **fields).json()["id"]
    # Another project of the same agent and a loose file stay out of the list.
    other = new_project(client, keys["alice"], "Other").json()["id"]
    upload(client, keys["alice"], "b.md", b"# y", title="Elsewhere", project=other)
    upload(client, keys["alice"], "c.md", b"# z", title="Loose")
    # Public by the file's link: no key asked.
    r = client.get(f"/a/{ids['Contract']}")
    assert r.status_code == 200 and "connect-src 'self'" in r.headers["content-security-policy"]
    page = r.text
    header = page[page.index("<header>") : page.index("</header>")]
    # The second line: the project's title as the list's button, no expiry.
    assert 'aria-controls="files-drop"' in header and 'aria-expanded="false"' in header
    assert ">Landing &lt;b&gt;</span>" in header and "<b>" not in page and "<i>" not in page
    assert "expires" not in header and "kept until deleted" not in header
    drop = page[page.index('id="files-drop"') : page.index('<div class="stage">')]
    order = [
        drop.index(x)
        for x in (
            f'href="/a/{ids["Brief <i>"]}"',
            "Contracts</h2>",
            f'href="/a/{ids["Contract"]}"',
            "App &lt;mockups&gt;</h2>",
            f'href="/a/{ids["Main"]}"',
            f'href="/p/{p}"',
        )
    ]
    assert order == sorted(order)
    assert drop.count('class="pf"') == 3 and "Brief &lt;i&gt;" in drop
    assert "Elsewhere" not in page and "Loose" not in page
    # Only this file is marked.
    assert drop.count('aria-current="page"') == 1
    assert f'data-id="{ids["Contract"]}" aria-current="page"' in drop
    # Full screen's menu offers the list; the counters' script is there.
    assert 'data-act="files"' in page and "/static/project.js" in page


def test_a_file_outside_a_project_has_no_list(env):
    client, _, keys = env
    art = upload(client, keys["bob"], "a.md", b"# x", title="Solo").json()["id"]
    page = client.get(f"/a/{art}").text
    assert "files-button" not in page and "files-drop" not in page
    assert 'data-act="files"' not in page and "project.js" not in page
    assert "expires " in page


def _menu(page):
    """The ≡ menu's items top to bottom, as (act, shown only in full screen)."""
    fan = re.search(r'<div id="fan-list".*?</div>', page, re.S).group(0)
    return [
        (act, when == "full")
        for act, when in re.findall(r'data-act="([\w-]+)"(?: data-when="(\w+)")?', fan)
    ]


def test_the_menu_holds_what_the_header_does_not(tmp_path):
    config = Config(
        data_dir=tmp_path, public_url="https://art.example.org", raw_url="https://raw.example.net"
    )
    store = Store(tmp_path, max_total_bytes=10**6, ttl_seconds=TTL)
    key = store.add_agent("alice")
    with TestClient(create_app(config, store)) as client:
        p = new_project(client, key).json()["id"]
        plain = upload(client, key, "a.html", b"<p>x").json()["id"]
        inside = upload(client, key, "b.html", b"<p>y", project=p).json()["id"]
        pages = {
            "plain": client.get(f"/a/{plain}").text,
            "inside": client.get(f"/a/{inside}").text,
            "project": client.get(f"/p/{p}").text,
        }
    # Normal: what the header lacks, and the project's files, whose title in
    # the header is easy to miss. Full screen: the header's buttons in its
    # order on top (bubble, full screen).
    tail = [("separate", False), ("share", False), ("settings", False)]
    assert _menu(pages["plain"]) == [("comment", True), ("exit", True), *tail]
    assert _menu(pages["inside"]) == [("comment", True), ("exit", True), ("files", False), *tail]
    assert _menu(pages["project"]) == [("exit", True), ("share", False), ("settings", False)]
    for name, page in pages.items():
        # The header keeps its buttons; the old items are gone from the menu.
        assert 'id="full-button"' in page and 'id="menu-button"' in page
        for gone in ("copy", "download", "theme", "text-size"):
            assert f'data-act="{gone}"' not in page, (name, gone)
        # Share: the page's link to copy, the file to download on a file only,
        # and the empty place for another device's code.
        share = re.search(r'<div id="share-dialog".*?</div>\s*</div>\s*</div>', page, re.S).group(0)
        assert 'role="dialog" aria-modal="true" aria-labelledby="share-title"' in share
        own = {"plain": f"a/{plain}", "inside": f"a/{inside}", "project": f"p/{p}"}[name]
        link = re.search(r'id="share-link" type="text" readonly value="([^"]+)"', share).group(1)
        assert link == f"https://art.example.org/{own}"
        assert ">Copy link<" in share and 'id="share-device" class="share-device" hidden' in share
        assert ("Download file" in share) is (name != "project")
        # Settings: the name and four choices, each a radio group.
        settings = re.search(r'<div id="settings-dialog".*?</form>', page, re.S).group(0)
        assert 'role="dialog" aria-modal="true" aria-labelledby="settings-title"' in settings
        assert 'id="settings-name"' in settings and 'placeholder="No name"' in settings
        names = re.findall(r'role="radiogroup" aria-labelledby="set-(\w+)-label"', settings)
        groups = {g: re.findall(rf'name="set-{g}" value="(\w+)"', settings) for g in names}
        assert groups == {
            "theme": ["auto", "light", "dark"],
            "text": ["normal", "large", "larger"],
            "pins": ["normal", "large"],
            "shapes": ["color", "shape"],
        }
        assert "Saved in this browser." in settings
        assert "/static/menu-dialogs.js" in page
