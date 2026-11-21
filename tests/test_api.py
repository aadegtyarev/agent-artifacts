import io
import json
import os
import time

import pytest
from starlette.testclient import TestClient

from agent_artifacts.config import Config, check_public_url
from agent_artifacts.kinds import KindError, clean_name, detect
from agent_artifacts.pages import SANDBOX_CSP
from agent_artifacts.store import Store
from agent_artifacts.web import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def env(tmp_path):
    config = Config(
        data_dir=tmp_path,
        public_url="https://art.example.org",
        max_file_bytes=1000,
        max_total_bytes=3000,
    )
    store = Store(tmp_path, max_total_bytes=config.max_total_bytes)
    keys = {name: store.add_agent(name) for name in ("alice", "bob")}
    with TestClient(create_app(config, store)) as client:
        yield client, store, keys


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def upload(client, key, filename, data, artifact_id=None, **fields):
    url = "/api/v1/artifacts" + (f"/{artifact_id}" if artifact_id else "")
    method = client.put if artifact_id else client.post
    return method(url, headers=auth(key), files={"file": (filename, data)}, data=fields)


def test_upload_view_download(env):
    client, _, keys = env
    r = upload(client, keys["alice"], "report.html", b"<h1 id=x>Hi</h1>")
    assert r.status_code == 201
    art = r.json()
    assert art["url"] == f"https://art.example.org/a/{art['id']}"
    assert (art["name"], art["type"], art["size"]) == ("report.html", "html", 16)

    page = client.get(f"/a/{art['id']}")
    assert page.status_code == 200
    assert f'src="/a/{art["id"]}/raw"' in page.text and "sandbox=" in page.text
    assert 'aria-label="Download"' in page.text and 'aria-label="Copy link"' in page.text

    raw = client.get(f"/a/{art['id']}/raw")
    assert raw.content == b"<h1 id=x>Hi</h1>"
    assert raw.headers["content-security-policy"] == SANDBOX_CSP

    dl = client.get(f"/a/{art['id']}/download")
    assert dl.content == b"<h1 id=x>Hi</h1>"
    assert 'filename="report.html"' in dl.headers["content-disposition"]


def test_name_field_overrides_the_file_name(env):
    client, _, keys = env
    r = upload(client, keys["alice"], "x.txt", b"hello", name="../Отчёт.md")
    assert r.json()["name"] == "Отчёт.md"
    dl = client.get(f"/a/{r.json()['id']}/download")
    assert "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82.md" in dl.headers["content-disposition"]


def test_markdown_is_sent_for_the_browser_to_render(env):
    client, _, keys = env
    text = "# Top\n\n</script><script>x()</script> [ext](https://example.org)\n"
    art = upload(client, keys["alice"], "n.md", text.encode()).json()
    raw = client.get(f"/a/{art['id']}/raw")
    assert raw.headers["content-security-policy"] == SANDBOX_CSP
    assert "marked v18" in raw.text
    # The source sits in a <script> as JSON with "<" escaped: it cannot end it.
    assert "</script><script>x()" not in raw.text
    assert json.dumps(text).replace("<", "\\u003c") in raw.text


def test_image_is_shown_inline_archive_is_download_only(env):
    client, _, keys = env
    img = upload(client, keys["alice"], "a.png", PNG).json()
    assert client.get(f"/a/{img['id']}/raw").headers["content-type"] == "image/png"
    zipped = upload(client, keys["alice"], "a.zip", b"PK\x03\x04rest").json()
    raw = client.get(f"/a/{zipped['id']}/raw")
    assert raw.headers["content-disposition"].startswith("attachment")


def test_replace_keeps_the_link_and_resets_expiry(env):
    client, store, keys = env
    first = upload(client, keys["alice"], "a.txt", b"one").json()
    time.sleep(1.1)
    second = upload(client, keys["alice"], "a.html", b"two", artifact_id=first["id"])
    assert second.status_code == 200
    body = second.json()
    assert body["id"] == first["id"] and body["type"] == "html"
    assert body["expires_at"] > first["expires_at"]
    assert client.get(f"/a/{first['id']}/raw").content == b"two"


def test_other_agents_cannot_touch_an_artifact(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"mine").json()
    assert upload(client, keys["bob"], "a.txt", b"x", artifact_id=art["id"]).status_code == 404
    assert (
        client.delete(f"/api/v1/artifacts/{art['id']}", headers=auth(keys["bob"])).status_code
        == 404
    )
    assert client.get("/api/v1/artifacts", headers=auth(keys["bob"])).json() == []
    assert [
        a["id"] for a in client.get("/api/v1/artifacts", headers=auth(keys["alice"])).json()
    ] == [art["id"]]


def test_delete(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    assert (
        client.delete(f"/api/v1/artifacts/{art['id']}", headers=auth(keys["alice"])).status_code
        == 204
    )
    assert client.get(f"/a/{art['id']}").status_code == 404
    assert not store.path(art["id"]).exists()


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}]
)
def test_api_needs_a_known_key(env, headers):
    client, _, _ = env
    assert client.get("/api/v1/artifacts", headers=headers).status_code == 401
    r = client.post("/api/v1/artifacts", headers=headers, files={"file": ("a.txt", b"x")})
    assert r.status_code == 401


def test_limits_and_type_checks(env):
    client, _, keys = env
    key = keys["alice"]
    assert upload(client, key, "big.txt", b"x" * 1001).json()["error"] == "too_large"
    assert upload(client, key, "a.exe", b"MZ").json()["error"] == "unsupported_type"
    assert upload(client, key, "a.png", b"<html>").json()["error"] == "type_mismatch"
    assert upload(client, key, "a.html", b"\x00\x01binary").json()["error"] == "type_mismatch"
    for i in range(3):
        assert upload(client, key, f"{i}.txt", b"x" * 1000).status_code == 201
    r = upload(client, key, "more.txt", b"x")
    assert (r.status_code, r.json()["error"]) == (507, "storage_full")


def test_expired_artifacts_are_gone_and_purged(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    with store._conn() as db:
        db.execute("UPDATE artifacts SET expires_at = 0")
    assert client.get(f"/a/{art['id']}").status_code == 404
    assert client.get(f"/a/{art['id']}/download").status_code == 404
    assert store.purge_expired() == 1
    assert not store.path(art["id"]).exists()


def test_removing_an_agent_deletes_its_files(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    assert store.remove_agent("alice")
    assert client.get(f"/a/{art['id']}").status_code == 404
    assert not store.path(art["id"]).exists()
    assert client.get("/api/v1/artifacts", headers=auth(keys["alice"])).status_code == 401


def test_viewer_page_escapes_the_name(env):
    client, _, keys = env
    art = upload(
        client, keys["alice"], "x.txt", b"x", name="<img src=x onerror=alert(1)>.txt"
    ).json()
    page = client.get(f"/a/{art['id']}").text
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(1)&gt;.txt" in page


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("a/b/c.txt", "c.txt"),
        ("..\\..\\x.md", "x.md"),
        (".hidden.txt", "hidden.txt"),
        ('a\x00b".txt', "ab.txt"),
        ("n" * 300 + ".tar.gz", "n" * 193 + ".tar.gz"),
    ],
)
def test_clean_name(raw, clean):
    assert clean_name(raw) == clean


def test_detect_tar_gz_and_cut_utf8():
    assert detect("x.tar.gz", b"\x1f\x8b\x08").name == "tgz"
    assert detect("x.txt", "привет".encode()[:-1]).name == "txt"
    with pytest.raises(KindError):
        detect(".txt", b"x")


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://art.example.org/", True),
        ("http://127.0.0.1:8090", True),
        ("http://192.168.1.5", True),
        ("http://box.local", True),
        ("http://art.example.org", False),
        ("http://8.8.8.8", False),
        ("ftp://x", False),
    ],
)
def test_public_url_must_be_https_on_public_hosts(url, ok):
    if ok:
        assert not check_public_url(url).endswith("/")
    else:
        with pytest.raises(ValueError):
            check_public_url(url)


def test_raw_opened_as_a_page_goes_to_the_viewer(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>x</p>").json()
    r = client.get(
        f"/a/{art['id']}/raw", headers={"Sec-Fetch-Dest": "document"}, follow_redirects=False
    )
    assert (r.status_code, r.headers["location"]) == (303, f"/a/{art['id']}")
    framed = client.get(f"/a/{art['id']}/raw", headers={"Sec-Fetch-Dest": "iframe"})
    assert framed.content == b"<p>x</p>"


def test_replace_of_an_expired_artifact_is_not_found(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    with store._conn() as db:
        db.execute("UPDATE artifacts SET expires_at = 0")
    assert upload(client, keys["alice"], "a.txt", b"y", artifact_id=art["id"]).status_code == 404
    assert (
        store.put(
            io.BytesIO(b"y"),
            owner="bob",
            name="a.txt",
            kind="txt",
            size=1,
            artifact_id=art["id"],
        )
        is None
    )


def test_crash_leftovers_are_purged(env):
    _, store, _ = env
    leftover = store.files / ".abc.tmp"
    leftover.write_bytes(b"x")
    os.utime(leftover, (0, 0))
    store.purge_expired()
    assert not leftover.exists()


def test_markdown_cap_and_raw_framing_headers(env, monkeypatch):
    client, _, keys = env
    monkeypatch.setattr("agent_artifacts.kinds.MAX_TEXT_BYTES", 10)
    assert upload(client, keys["alice"], "a.md", b"x" * 11).json()["error"] == "too_large"
    img = upload(client, keys["alice"], "a.png", PNG).json()
    assert client.get(f"/a/{img['id']}/raw").headers["content-security-policy"] == (
        "frame-ancestors 'self'"
    )
    page = client.get(f"/a/{img['id']}")
    assert page.headers["cross-origin-opener-policy"] == "same-origin"


def test_replaced_markdown_is_rendered_again(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.md", b"# One").json()
    assert "One" in client.get(f"/a/{art['id']}/raw").text
    time.sleep(0.01)
    upload(client, keys["alice"], "a.md", b"# Two", artifact_id=art["id"])
    assert "Two" in client.get(f"/a/{art['id']}/raw").text


def test_text_is_shown_with_fixed_colours_and_escaped(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", "<b>привет</b>".encode()).json()
    raw = client.get(f"/a/{art['id']}/raw")
    assert raw.headers["content-type"].startswith("text/html")
    assert raw.headers["content-security-policy"] == SANDBOX_CSP
    assert "<pre>&lt;b&gt;привет&lt;/b&gt;</pre>" in raw.text
    assert "color:#1d1d1f;background:#fff" in raw.text


def test_image_opens_full_size_on_its_own(env):
    client, _, keys = env
    img = upload(client, keys["alice"], "a.png", PNG).json()
    page = client.get(f"/a/{img['id']}").text
    assert f'<a href="/a/{img["id"]}/raw" target="_blank" rel="noopener">' in page
    r = client.get(f"/a/{img['id']}/raw", headers={"Sec-Fetch-Dest": "document"})
    assert (r.status_code, r.headers["content-type"]) == (200, "image/png")


def test_quota_per_agent_and_me(tmp_path):
    config = Config(data_dir=tmp_path, public_url="https://art.example.org", max_file_bytes=2000)
    store = Store(tmp_path, max_total_bytes=10_000, user_quota_bytes=1500)
    keys = {name: store.add_agent(name) for name in ("alice", "bob")}
    with TestClient(create_app(config, store)) as client:
        first = upload(client, keys["alice"], "a.txt", b"x" * 1000).json()
        r = upload(client, keys["alice"], "b.txt", b"x" * 600)
        assert r.status_code == 507
        assert (r.json()["error"], r.json()["used_bytes"], r.json()["quota_bytes"]) == (
            "quota_exceeded",
            1000,
            1500,
        )
        # Replacing a file counts only the new size; other agents have their own quota.
        assert upload(client, keys["alice"], "a.txt", b"x" * 1400, first["id"]).status_code == 200
        assert upload(client, keys["bob"], "b.txt", b"x" * 1000).status_code == 201
        me = client.get("/api/v1/me", headers=auth(keys["alice"])).json()
        assert me == {
            "name": "alice",
            "used_bytes": 1400,
            "quota_bytes": 1500,
            "files": 1,
            "admin": False,
            "own_quota": False,
            "last_upload_at": me["last_upload_at"],
        }
        assert client.get("/api/v1/me").status_code == 401


def test_new_key_replaces_the_old_one(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"hi").json()
    r = client.post("/api/v1/me/key", headers=auth(keys["alice"]))
    assert r.status_code == 200 and r.json()["name"] == "alice"
    assert r.headers["cache-control"] == "no-store"
    new = r.json()["key"]
    assert client.get("/api/v1/me", headers=auth(keys["alice"])).status_code == 401
    assert [a["id"] for a in client.get("/api/v1/artifacts", headers=auth(new)).json()] == [
        art["id"]
    ]
    assert client.post("/api/v1/me/key").status_code == 401


def test_purge_skips_a_file_that_vanishes_meanwhile(env, monkeypatch):
    _, store, _ = env
    gone = store.files / ".x.tmp"
    real_iterdir = type(store.files).iterdir
    monkeypatch.setattr(type(store.files), "iterdir", lambda self: [*real_iterdir(self), gone])
    assert store.purge_expired() == 0


def test_admin_api(env):
    client, store, keys = env
    root = store.add_agent("root", admin=True)
    upload(client, keys["alice"], "a.txt", b"x" * 100)
    for path in ("/api/v1/admin/users", "/api/v1/admin/stats"):
        assert client.get(path, headers=auth(keys["alice"])).status_code == 403
        assert client.get(path).status_code == 401
    people = client.get("/api/v1/admin/users", headers=auth(root)).json()
    alice = next(p for p in people if p["name"] == "alice")
    assert (alice["used_bytes"], alice["files"], alice["own_quota"]) == (100, 1, False)
    assert alice["last_upload_at"].endswith("Z")
    stats = client.get("/api/v1/admin/stats", headers=auth(root)).json()
    assert (stats["users"], stats["files"], stats["used_bytes"]) == (3, 1, 100)

    # Her own quota, then back to the common one.
    mb = 1024 * 1024
    r = client.patch("/api/v1/admin/users/alice", headers=auth(root), json={"quota_bytes": 2 * mb})
    assert r.status_code == 200
    assert (r.json()["quota_bytes"], r.json()["own_quota"]) == (2 * mb, True)
    r = client.patch("/api/v1/admin/users/alice", headers=auth(root), json={"quota_bytes": None})
    assert r.json()["own_quota"] is False
    for bad in (
        {"quota_bytes": mb - 1},
        {"quota_bytes": 1.5 * mb},
        {"quota_bytes": True},
        {"quota_bytes": 10**30},
        {"quota_bytes": mb, "admin": True},
        [1],
    ):
        r = client.patch("/api/v1/admin/users/alice", headers=auth(root), json=bad)
        assert r.status_code == 400, bad
    r = client.patch("/api/v1/admin/users/alice", headers=auth(root), content=b"{not json")
    assert r.status_code == 400
    assert (
        client.patch(
            "/api/v1/admin/users/nobody", headers=auth(root), json={"quota_bytes": mb}
        ).status_code
        == 404
    )
    for method, path in (
        ("PATCH", "/api/v1/admin/users/bob"),
        ("DELETE", "/api/v1/admin/users/bob"),
        ("PATCH", "/api/v1/admin/settings"),
    ):
        for headers in (auth(keys["alice"]), {}):
            r = client.request(method, path, headers=headers, json={"quota_bytes": mb})
            assert r.status_code in (401, 403), (method, path)

    r = client.patch("/api/v1/admin/settings", headers=auth(root), json={"ttl_seconds": 86400})
    assert r.json()["ttl_seconds"] == 86400
    assert client.get("/api/v1/admin/settings", headers=auth(root)).json() == r.json()
    art = upload(client, keys["bob"], "c.txt", b"x").json()
    assert store.get(art["id"]).expires_at - store.get(art["id"]).created_at == 24 * 3600
    for bad in (
        {"ttl_seconds": 60},
        {"ttl_seconds": 10**7},
        {"max_total_bytes": 10**15},
        {"user_quota_bytes": 10**30},
        {"x": 1},
        {},
    ):
        r = client.patch("/api/v1/admin/settings", headers=auth(root), json=bad)
        assert r.status_code == 400, bad

    assert client.delete("/api/v1/admin/users/root", headers=auth(root)).status_code == 400
    assert client.delete("/api/v1/admin/users/bob", headers=auth(root)).status_code == 204
    assert client.get(art["url"].removeprefix("https://art.example.org")).status_code == 404
    assert client.delete("/api/v1/admin/users/bob", headers=auth(root)).status_code == 404


def test_old_database_gets_the_new_columns(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "meta.sqlite")
    db.execute("CREATE TABLE agents (name TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE)")
    db.execute("INSERT INTO agents VALUES ('old', 'h')")
    db.execute(
        "CREATE TABLE artifacts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, "
        "kind TEXT NOT NULL, size INTEGER NOT NULL, created_at REAL, expires_at REAL)"
    )
    db.execute("INSERT INTO artifacts VALUES (?, 'old', 'a.txt', 'txt', 1, 0, 9e9)", ("a" * 22,))
    db.commit()
    db.close()
    store = Store(tmp_path, max_total_bytes=1000)
    assert not store.is_admin("old") and store.set_admin("old", True) and store.is_admin("old")
    c = store.add_comment("a" * 22, author="x", text="y", anchor=None, token_hash="h")  # noqa: S106
    assert c.n == 1 and store.get("a" * 22).comments == 1


def test_cli_admin_role(tmp_path, monkeypatch, capsys):
    from agent_artifacts.cli import main

    monkeypatch.setenv("AA_DATA_DIR", str(tmp_path))
    assert main(["agent", "add", "me", "--admin"]) == 0
    assert main(["agent", "add", "you"]) == 0
    assert main(["agent", "admin", "me", "--revoke"]) == 0
    assert main(["agent", "admin", "you"]) == 0
    capsys.readouterr()
    assert main(["agent", "list"]) == 0
    assert capsys.readouterr().out.split("\n")[:2] == ["me", "you (admin)"]


def test_invite_gives_one_key_once(env):
    client, store, keys = env
    root = store.add_agent("root", admin=True)
    path = "/api/v1/admin/invites"
    assert client.post(path, headers=auth(keys["alice"]), json={"name": "carol"}).status_code == 403
    r = client.post(path, headers=auth(root), json={"name": "carol", "ttl_seconds": 2 * 86400})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    invite = r.json()
    assert invite["url"].startswith("https://art.example.org/join/")
    for name in ("carol", "alice", "bad name", "..", ".hidden"):
        assert client.post(path, headers=auth(root), json={"name": name}).status_code in (409, 400)
    assert [i["name"] for i in client.get(path, headers=auth(root)).json()] == ["carol"]

    join = invite["url"].removeprefix("https://art.example.org")
    assert client.head(join).status_code == 200  # a link checker uses nothing either
    page = client.get(join)  # a link preview: shows the button, uses nothing
    assert page.status_code == 200 and "Get my key" in page.text
    assert "form-action 'self'" in page.headers["content-security-policy"]
    assert client.get(join).status_code == 200

    got = client.post(join)
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    key = got.text.split("AGENT_ARTIFACTS_KEY=")[1].split("<")[0]
    assert client.get("/api/v1/me", headers=auth(key)).json()["name"] == "carol"
    assert client.post(join).status_code == 404
    assert client.get(join).status_code == 404
    assert client.get(path, headers=auth(root)).json() == []


def test_invite_cancel_and_expiry(env):
    client, store, _ = env
    root = store.add_agent("root", admin=True)
    path = "/api/v1/admin/invites"
    first = client.post(path, headers=auth(root), json={"name": "dave"}).json()
    assert client.delete(f"{path}/{first['id']}", headers=auth(root)).status_code == 204
    assert client.post(first["url"].removeprefix("https://art.example.org")).status_code == 404
    second = client.post(path, headers=auth(root), json={"name": "dave"}).json()
    with store._conn() as db:
        db.execute("UPDATE invites SET expires_at = 0")
    assert client.post(second["url"].removeprefix("https://art.example.org")).status_code == 404
    # A name taken by hand after the invite was made: the invite stays unused.
    third = client.post(path, headers=auth(root), json={"name": "erin"}).json()
    store.add_agent("erin")
    assert client.post(third["url"].removeprefix("https://art.example.org")).status_code == 409
    for ttl in (0, 31 * 86400, True):
        r = client.post(path, headers=auth(root), json={"name": "x", "ttl_seconds": ttl})
        assert r.status_code == 400, ttl


def test_two_presses_get_one_key(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = Store(tmp_path, max_total_bytes=1000)
    _, token = store.add_invite("frank", by="root", ttl=60)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: store.claim_invite(token), range(8)))
    assert sum(r is not None for r in results) == 1


def test_own_pages_escape_values_and_load_only_static_code(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "<i x=1>&.txt", b"hi").json()
    page = client.get(f"/a/{art['id']}")
    assert "<i x" not in page.text and "&lt;i x=1&gt;&amp;.txt" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "sha256" not in csp
    assert "<script>" not in page.text and "<style>" not in page.text
    assert client.get("/static/copy.js").status_code == 200
    for path in ("/static/../store.py", "/static/%2e%2e/store.py", "/static/..%2fstore.py"):
        assert client.get(path).status_code == 404, path


def test_markdown_page_stays_about_the_size_of_the_file(env):
    client, _, keys = env
    text = "Привет, мир. " * 30 + "</script>\u2028"
    art = upload(client, keys["alice"], "ru.md", text.encode()).json()
    page = client.get(f"/a/{art['id']}/raw").text
    source = page.split("const SOURCE = ")[1].split(";\n")[0]
    assert json.loads(source) == text
    assert "</script>" not in source and "\u2028" not in source
    assert len(source.encode()) < len(text.encode()) * 1.2


def test_sandboxed_forms_submit_nowhere():
    assert "form-action 'none'" in SANDBOX_CSP


def comment(client, art_id, body=None, **kw):
    return client.post(f"/a/{art_id}/comments", json={"text": "hi"} if body is None else body, **kw)


def owner_comments(client, key, art_id):
    return client.get(f"/api/v1/artifacts/{art_id}/comments", headers=auth(key))


ELEMENT = {"type": "element", "selector": "main > p", "text": "Hi", "x": 0.5, "y": 1, "width": 1280}


def test_comment_edit_until_the_owner_reads_it(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>Hi</p>").json()
    r = comment(client, art["id"], {"author": "  ", "text": " Make it green\n", "anchor": ELEMENT})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    c = r.json()
    token = c.pop("edit_token")
    assert (c["n"], c["author"], c["text"], c["anchor"]) == (
        1,
        "anonymous",
        "Make it green",
        ELEMENT,
    )
    assert (c["read"], c["resolved"], c["before_replace"], c["edited_at"]) == (
        False,
        False,
        False,
        None,
    )

    url = f"/a/{art['id']}/comments/1"
    edited = client.patch(url, json={"edit_token": token, "text": "Blue"}).json()
    assert edited["text"] == "Blue" and edited["edited_at"] and "edit_token" not in edited
    public = client.get(f"/a/{art['id']}/comments")
    assert public.headers["cache-control"] == "no-store" and public.json()[0]["read"] is False

    listed = client.get("/api/v1/artifacts", headers=auth(keys["alice"])).json()
    assert (listed[0]["comments"], listed[0]["open_comments"]) == (1, 1)
    assert owner_comments(client, keys["alice"], art["id"]).json()[0]["read"] is True
    # The token is checked first: a wrong one is 403 whether read or not.
    r = client.patch(url, json={"edit_token": "wrong", "text": "x"})
    assert (r.status_code, r.json()["error"]) == (403, "forbidden")
    r = client.patch(url, json={"edit_token": token, "text": "Red"})
    assert (r.status_code, r.json()["error"]) == (409, "already_read")
    r = client.delete(url, headers={"X-Edit-Token": token})
    assert (r.status_code, r.json()["error"]) == (409, "already_read")

    # Resolving is not an edit: anyone, any time, and no edited_at change.
    resolved = client.patch(url, json={"resolved": True}).json()
    assert resolved["resolved"] and resolved["edited_at"] == edited["edited_at"]
    listed = client.get("/api/v1/artifacts", headers=auth(keys["alice"])).json()
    assert (listed[0]["comments"], listed[0]["open_comments"]) == (1, 0)


def test_comment_delete_by_token_before_read(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    token = comment(client, art["id"]).json()["edit_token"]
    url = f"/a/{art['id']}/comments/1"
    assert client.delete(url).status_code == 403
    assert client.delete(url, headers={"X-Edit-Token": "nope"}).status_code == 403
    assert client.delete(url, headers={"X-Edit-Token": token}).status_code == 204
    assert client.delete(url, headers={"X-Edit-Token": token}).json()["error"] == "not_found"


@pytest.mark.parametrize(
    ("filename", "data", "anchor", "ok"),
    [
        ("a.png", PNG, {"type": "point", "x": 0, "y": 1}, True),
        ("a.png", PNG, {"type": "point", "x": 1.5, "y": 0}, False),
        ("a.png", PNG, {"type": "point", "x": 0.1, "y": 0.1, "z": 0}, False),
        ("a.png", PNG, ELEMENT, False),
        ("a.md", b"# Hi", ELEMENT, True),
        ("a.md", b"# Hi", ELEMENT | {"selector": "x" * 513}, False),
        ("a.md", b"# Hi", ELEMENT | {"width": "1280"}, False),
        ("a.html", b"<p>", {"type": "point", "x": 0, "y": 0}, False),
        ("a.pdf", b"%PDF-1.4", {"type": "point", "x": 0, "y": 0}, False),
        ("a.svg", b"<svg/>", ELEMENT, False),
        ("a.zip", b"PK\x03\x04", ELEMENT, False),
        ("a.zip", b"PK\x03\x04", None, True),
    ],
)
def test_comment_anchor_follows_the_file_type(env, filename, data, anchor, ok):
    client, _, keys = env
    art = upload(client, keys["alice"], filename, data).json()
    r = comment(client, art["id"], {"text": "x", "anchor": anchor})
    assert r.status_code == (201 if ok else 400)


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
        [],
    ],
)
def test_comment_fields_are_checked(env, body):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    assert comment(client, art["id"], body).json()["error"] == "bad_request"


def test_comment_limits_count_code_points_and_keep_line_breaks(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    body = {"author": "Ж" * 64, "text": "ё" * 1996 + "\r\n\tx"}
    assert comment(client, art["id"], body).status_code == 201


def test_comment_body_rules(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    url = f"/a/{art['id']}/comments"
    r = client.post(url, content=b'{"text": "x"}', headers={"Content-Type": "text/plain"})
    assert (r.status_code, r.json()["error"]) == (400, "bad_request")
    r = client.post(url, content=b"{" * 16385, headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()["error"]) == (413, "too_large")
    r = client.post(
        url, content=iter([b'{"text": "x"}']), headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 411
    lone = b'{"text": "lone \\ud800"}'  # valid JSON, but not encodable as UTF-8
    r = client.post(url, content=lone, headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = client.post(url, content=b"[" * 16000, headers={"Content-Type": "application/json"})
    assert r.status_code == 400  # nested too deep for the parser
    comment(client, art["id"])
    r = client.patch(f"{url}/1", json={"resolved": True, "text": "x", "edit_token": "t"})
    assert r.status_code == 400
    assert client.patch(f"{url}/1", json={"resolved": "yes"}).status_code == 400
    assert client.patch(f"{url}/2", json={"resolved": True}).json()["error"] == "not_found"
    assert client.patch(f"{url}/99999999999", json={"resolved": True}).status_code == 404


def test_comment_numbers_are_never_reused_and_capped(env, monkeypatch):
    client, store, keys = env
    monkeypatch.setattr("agent_artifacts.store.MAX_COMMENTS", 3)
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    for _ in range(3):
        comment(client, art["id"])
    r = comment(client, art["id"])
    assert (r.status_code, r.json()["error"]) == (409, "too_many_comments")
    url = f"/api/v1/artifacts/{art['id']}/comments/3"
    assert client.delete(url, headers=auth(keys["alice"])).status_code == 204
    assert comment(client, art["id"]).json()["n"] == 4
    assert [c["n"] for c in owner_comments(client, keys["alice"], art["id"]).json()] == [1, 2, 4]


def test_replace_keeps_comments_marked_before_replace(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>").json()
    comment(client, art["id"])
    upload(client, keys["alice"], "a.png", PNG, artifact_id=art["id"])
    comment(client, art["id"])
    found = client.get(f"/a/{art['id']}/comments").json()
    assert [c["before_replace"] for c in found] == [True, False]


def test_comments_go_with_the_artifact(env):
    client, store, keys = env
    arts = [upload(client, keys["alice"], f"{i}.txt", b"x").json() for i in range(3)]
    for a in arts:
        comment(client, a["id"])
    client.delete(f"/api/v1/artifacts/{arts[0]['id']}", headers=auth(keys["alice"]))
    with store._conn() as db:
        db.execute("UPDATE artifacts SET expires_at = 0 WHERE id = ?", (arts[1]["id"],))
    assert client.get(f"/a/{arts[1]['id']}/comments").json()["error"] == "not_found"
    store.purge_expired()

    def left():
        with store._conn() as db:
            return [r[0] for r in db.execute("SELECT artifact_id FROM comments")]

    assert left() == [arts[2]["id"]]
    store.remove_agent("alice")
    assert left() == []


def test_owner_comment_calls_are_for_the_owner_only(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    comment(client, art["id"])
    base = f"/api/v1/artifacts/{art['id']}/comments"
    assert client.get(base).status_code == 401
    assert owner_comments(client, keys["bob"], art["id"]).json()["error"] == "not_found"
    assert (
        client.patch(f"{base}/1", json={"resolved": True}, headers=auth(keys["bob"])).status_code
        == 404
    )
    assert client.delete(f"{base}/1", headers=auth(keys["bob"])).status_code == 404
    # Bob's look did not mark it read.
    assert client.get(f"/a/{art['id']}/comments").json()[0]["read"] is False
    r = client.patch(f"{base}/1", json={"resolved": True}, headers=auth(keys["alice"]))
    assert r.json()["resolved"] is True


def test_head_and_other_methods_never_write(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    url = f"/a/{art['id']}/comments"
    body = b'{"text": "sneaky"}'
    r = client.request("HEAD", url, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 405 and r.headers["allow"] == "GET, POST"
    assert client.get(url).json() == []
    comment(client, art["id"])
    r = client.head(f"/api/v1/artifacts/{art['id']}/comments", headers=auth(keys["alice"]))
    assert r.status_code == 405
    assert client.get(url).json()[0]["read"] is False  # HEAD did not mark it read
    r = client.put(url, json={"text": "x"})
    assert (r.status_code, r.json()["error"]) == (405, "bad_request")
    assert r.headers["cache-control"] == "no-store"


def test_comment_body_is_counted_whatever_the_headers_say(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    big = b'{"text": "' + b"x" * 17000 + b'"}'
    r = client.post(
        f"/a/{art['id']}/comments",
        content=iter([big]),
        headers={"Content-Type": "application/json", "Content-Length": "20"},
    )
    assert (r.status_code, r.json()["error"]) == (413, "too_large")
    assert client.get(f"/a/{art['id']}/comments").json() == []


def test_comment_errors_are_404_first_and_not_cached(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()
    r = client.delete(f"/a/{art['id']}/comments/1")  # no token, no such comment
    assert (r.status_code, r.json()["error"]) == (404, "not_found")
    assert r.headers["cache-control"] == "no-store"
    r = client.get(f"/api/v1/artifacts/{art['id']}/comments")
    assert r.status_code == 401 and r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [
        {"text": "x", "author": "two\nlines"},
        {"text": "x", "author": "tab\there"},
        {"text": "x", "anchor": ELEMENT | {"selector": "p\n"}},
        {"text": "x", "anchor": ELEMENT | {"text": "a\tb"}},
        {"text": "price ‮1001"},
        {"text": "a b"},
        {"text": "x", "author": "⁦name"},
    ],
)
def test_comment_one_line_fields_and_bidi_controls(env, body):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>").json()
    assert comment(client, art["id"], body).json()["error"] == "bad_request"
