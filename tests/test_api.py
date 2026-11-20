import io
import json
import os
import time

import pytest
from starlette.testclient import TestClient

from agent_artifacts.config import Config, check_public_url
from agent_artifacts.kinds import KindError, clean_name, detect
from agent_artifacts.store import Store
from agent_artifacts.web import SANDBOX_CSP, create_app

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
            ttl=60,
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
        assert me == {"name": "alice", "used_bytes": 1400, "quota_bytes": 1500, "files": 1}
        assert client.get("/api/v1/me").status_code == 401
