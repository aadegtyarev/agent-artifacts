import html
import io
import json
import os
import re
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_artifacts import pages
from agent_artifacts.config import Config, check_public_url, check_raw_url
from agent_artifacts.kinds import KindError, clean_name, detect
from agent_artifacts.pages import PINS_SCRIPT, SANDBOX_CSP, STATIC_DIR
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
    assert f'href="/a/{art["id"]}/download"' in page.text and "Copy link" in page.text
    assert "Download file" in page.text

    raw = client.get(f"/a/{art['id']}/raw")
    assert raw.content == b"<h1 id=x>Hi</h1>" + PINS_SCRIPT
    assert raw.headers["content-security-policy"] == SANDBOX_CSP
    assert raw.headers["content-type"] == "text/html; charset=utf-8"

    dl = client.get(f"/a/{art['id']}/download")
    assert dl.content == b"<h1 id=x>Hi</h1>"
    assert 'filename="report.html"' in dl.headers["content-disposition"]


def test_name_field_overrides_the_file_name(env):
    client, _, keys = env
    r = upload(client, keys["alice"], "x.txt", b"hello", name="../Отчёт.md")
    assert r.json()["name"] == "Отчёт.md"
    dl = client.get(f"/a/{r.json()['id']}/download")
    assert "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82.md" in dl.headers["content-disposition"]


def test_title_is_the_name_or_the_field_and_a_replace_keeps_it(env):
    client, store, keys = env
    plain = upload(client, keys["alice"], "a.txt", b"x").json()
    assert plain["title"] == "a.txt" and plain["updated_at"] == plain["created_at"]
    art = upload(client, keys["alice"], "<b>.txt", b"x", title="  Q3 <report>  ").json()
    assert (art["name"], art["title"]) == ("<b>.txt", "Q3 <report>")
    page = client.get(f"/a/{art['id']}").text
    assert "<title>Q3 &lt;report&gt;</title>" in page
    assert '<h1 title="Q3 &lt;report&gt;">Q3 &lt;report&gt;</h1>' in page

    clock = store.clock
    store.clock = lambda: clock() + 60
    kept = upload(client, keys["alice"], "b.txt", b"y", artifact_id=art["id"]).json()
    assert (kept["name"], kept["title"]) == ("b.txt", "Q3 <report>")
    assert kept["updated_at"] > art["updated_at"] and kept["created_at"] == art["created_at"]
    changed = upload(client, keys["alice"], "b.txt", b"y", artifact_id=art["id"], title="Final")
    assert changed.json()["title"] == "Final"
    listed = client.get("/api/v1/artifacts", headers=auth(keys["alice"])).json()
    assert [a["title"] for a in listed] == ["a.txt", "Final"]
    assert upload(client, keys["alice"], "c.txt", b"z", title=" ").status_code == 400


def test_patch_sets_the_title(env):
    client, store, keys = env
    art = upload(client, keys["alice"], "a.txt", b"x").json()

    def patch(body, key="alice", art_id=art["id"]):
        return client.patch(f"/api/v1/artifacts/{art_id}", headers=auth(keys[key]), json=body)

    r = patch({"title": " Notes "})
    assert r.status_code == 200
    assert (r.json()["title"], r.json()["name"]) == ("Notes", "a.txt")
    assert r.json()["updated_at"] == art["updated_at"]
    assert patch({"title": "x" * 200}).status_code == 200
    # Stored composed, like file names.
    assert patch({"title": "e\u0301"}).json()["title"] == "\u00e9"
    for bad in (
        {},
        {"title": None},
        {"title": "  "},
        {"title": "x" * 201},
        {"title": "a\nb"},
        {"title": "a\u202eb"},
        {"title": "a\u200fb"},
        {"title": "\u200b"},
        {"title": "ok", "colour": "red"},
    ):
        assert patch(bad).status_code == 400, bad
    assert patch({"title": "mine"}, key="bob").status_code == 404
    with store._conn() as db:
        db.execute("UPDATE artifacts SET expires_at = 0")
    assert patch({"title": "late"}).status_code == 404


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


@pytest.mark.parametrize(
    ("filename", "data"),
    [("a.html", b"<p>Hi"), ("a.md", b"# Hi"), ("a.txt", b"Hi")],
)
def test_pages_get_the_pin_script_the_download_stays_the_file(env, filename, data):
    client, _, keys = env
    art = upload(client, keys["alice"], filename, data).json()
    raw = client.get(f"/a/{art['id']}/raw").text.rstrip()
    script = PINS_SCRIPT.decode()
    assert raw.count(script) == 1
    # The badge marks it shares with the viewer come first, in its block.
    assert script.startswith("<script>{\n// What a pin holds")
    # So are the pin styles, as a string the script puts in its shadow root.
    assert "const pinCss = \"/* Pins and their badges" in script
    assert "</" not in script.split("const pinCss = ", 1)[1].split(";\n", 1)[0]
    assert raw.endswith(script if filename == "a.html" else script + "</body></html>")
    assert client.get(f"/a/{art['id']}/download").content == data


def test_pages_tell_links_to_other_files_by_one_copy_of_links_js():
    links = (STATIC_DIR / "links.js").read_text()
    script = PINS_SCRIPT.decode()
    # In the pin script's block, before the script that uses it.
    assert script.index(links) < script.index("// Pins on a page.")
    assert "pageLinks.target(link.getAttribute(\"href\"), location.href)" in script


def test_a_pinch_zooms_the_file_not_the_viewer(env):
    client, _, keys = env
    # The pages and images zoom.js zooms: the browser does not zoom the viewer.
    no_zoom = "initial-scale=1, maximum-scale=1, user-scalable=no, interactive"
    for name, data, zooms in (
        ("a.html", b"<p>x", True),
        ("b.png", PNG, True),
        ("c.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", False),
        ("d.zip", b"PK\x03\x04", False),
    ):
        art = upload(client, keys["alice"], name, data).json()
        page = client.get(f"/a/{art['id']}").text
        assert (no_zoom in page) is zooms, name
        assert ('<div class="stage zooms">' in page) is zooms, name
    # In the frame, zoom.js comes in the pin script's block before pins.js.
    script = PINS_SCRIPT.decode()
    zoom = (STATIC_DIR / "zoom.js").read_text()
    assert script.index(zoom) < script.index("// Pins on a page.")
    assert "pageZoom.attach(window, {" in script


def test_markdown_keeps_links_to_other_files_in_its_tab(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "n.md", b"[next](/a/x)").json()
    page = client.get(f"/a/{art['id']}/raw").text
    links = (STATIC_DIR / "links.js").read_text()
    # Its own copy, before the renderer that decides which links get a new
    # tab, and the pin script's after it.
    assert page.index(f"<script>{links}</script>") < page.index("const SOURCE = ")
    assert '!pageLinks.target(href, location.href)) { a.target = "_blank"' in page
    assert page.index("const SOURCE = ") < page.index(PINS_SCRIPT.decode())


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
    assert client.get(f"/a/{first['id']}/raw").content == b"two" + PINS_SCRIPT


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
    assert framed.content == b"<p>x</p>" + PINS_SCRIPT


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
    assert "color:#1c1c1e;background:#fff" in raw.text


def test_image_opens_full_size_on_its_own(env):
    client, _, keys = env
    img = upload(client, keys["alice"], "a.png", PNG).json()
    page = client.get(f"/a/{img['id']}").text
    assert f'<a id="image-link" href="/a/{img["id"]}/raw" target="_blank" rel="noopener">' in page
    r = client.get(f"/a/{img['id']}/raw", headers={"Sec-Fetch-Dest": "document"})
    assert (r.status_code, r.headers["content-type"]) == (200, "image/png")


@pytest.mark.parametrize(
    ("url", "public", "result"),
    [
        ("", "https://art.example.org", ""),
        ("  ", "https://art.example.org", ""),
        ("https://raw.example.net/", "https://art.example.org", "https://raw.example.net"),
        (
            "https://raw.art.example.org:8443",
            "https://art.example.org",
            "https://raw.art.example.org:8443",
        ),
        ("http://localhost:8090", "http://127.0.0.1:8090", "http://localhost:8090"),
        ("https://raw.example.net/files", "https://art.example.org", None),
        ("https://raw.example.net/?a=1", "https://art.example.org", None),
        ("https://art.example.org", "https://art.example.org", None),
        ("https://ART.example.org:8443", "https://art.example.org", None),
        ("http://raw.example.net", "https://art.example.org", None),
        ("raw.example.net", "https://art.example.org", None),
        # Kept as scheme://host[:port], the host as it is compared.
        ("https://Raw.Example.NET.:443/", "https://art.example.org", "https://raw.example.net:443"),
        ("https://art.example.org.", "https://art.example.org", None),
        ("https://raw.example.net", "https://ART.example.org.", "https://raw.example.net"),
        ("https://rаw.example.net", "https://art.example.org", None),  # Cyrillic "а"
        (
            "https://xn--rw-8kc.example.net",
            "https://art.example.org",
            "https://xn--rw-8kc.example.net",
        ),
        ("https://raw.example.net#", "https://art.example.org", None),
        ("https://raw.example.net?", "https://art.example.org", None),
        ("https://raw.example.net:abc", "https://art.example.org", None),
        ("https://raw.example.net:", "https://art.example.org", None),
        ("https://raw.example.net:99999", "https://art.example.org", None),
        ('https://"x', "https://art.example.org", None),
        ("https://user@raw.example.net", "https://art.example.org", None),
        ("https://raw_x.example.net", "https://art.example.org", None),
    ],
)
def test_raw_url_checks(url, public, result):
    if result is None:
        with pytest.raises(ValueError):
            check_raw_url(url, public)
    else:
        assert check_raw_url(url, public) == result


@pytest.fixture
def raw_env(tmp_path):
    config = Config(
        data_dir=tmp_path, public_url="https://art.example.org", raw_url="https://raw.example.net"
    )
    store = Store(tmp_path, max_total_bytes=config.max_total_bytes)
    key = store.add_agent("alice")
    with TestClient(create_app(config, store), base_url="https://art.example.org") as client:
        raw = TestClient(client.app, base_url="https://raw.example.net")
        yield client, raw, key


def test_open_separately_links_to_the_raw_origin(raw_env):
    client, _, key = raw_env
    art = upload(client, key, "a.html", b"<p>x</p>").json()
    zipped = upload(client, key, "a.zip", b"PK\x03\x04" + b"\x00" * 26).json()
    link = f'href="https://raw.example.net/a/{art["id"]}/raw" target="_blank" rel="noopener"'
    assert link in client.get(f"/a/{art['id']}").text
    assert "Open separately" not in client.get(f"/a/{zipped['id']}").text


def test_no_raw_origin_no_open_separately(env):
    client, _, keys = env
    art = upload(client, keys["alice"], "a.html", b"<p>x</p>").json()
    page = client.get(f"/a/{art['id']}").text
    assert "Open separately" not in page and "Copy link" in page
    # Without a raw origin, no Host is treated as one.
    r = client.get(f"/a/{art['id']}/raw", headers={"Host": "raw.example.net"})
    assert r.status_code == 200


def test_raw_origin_serves_only_raw(raw_env):
    client, raw, key = raw_env
    art = upload(client, key, "a.html", b"<p>x</p>").json()
    img = upload(client, key, "a.png", PNG).json()
    zipped = upload(client, key, "a.zip", b"PK\x03\x04" + b"\x00" * 26).json()
    # Opened as a page it shows the content itself, with the sandbox.
    r = raw.get(f"/a/{art['id']}/raw", headers={"Sec-Fetch-Dest": "document"})
    assert (r.status_code, r.content) == (200, b"<p>x</p>" + PINS_SCRIPT)
    assert r.headers["content-security-policy"] == SANDBOX_CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in r.headers
    assert raw.head(f"/a/{art['id']}/raw").status_code == 200
    assert raw.get(f"/a/{img['id']}/raw").headers["content-type"] == "image/png"
    # Host names are case-insensitive and may carry the port and the root's
    # trailing dot: every spelling of the raw host gets only the raw origin.
    for host in ("RAW.example.net:443", "raw.example.net.", "raw.example.net.:443"):
        r = raw.get(f"/a/{art['id']}/raw", headers={"Host": host})
        assert (host, r.status_code) == (host, 200)
        assert raw.get("/health", headers={"Host": host}).status_code == 404, host
    for method, path in [
        ("GET", f"/a/{zipped['id']}/raw"),
        ("GET", "/a/AAAAAAAAAAAAAAAAAAAAAA/raw"),
        ("POST", f"/a/{art['id']}/raw"),
        ("GET", f"/a/{art['id']}"),
        ("GET", f"/a/{art['id']}/download"),
        ("GET", f"/a/{art['id']}/comments"),
        ("GET", "/api/v1/artifacts"),
        ("GET", "/static/site.css"),
        ("GET", "/health"),
        ("GET", "/"),
    ]:
        r = raw.request(method, path, headers={"Authorization": f"Bearer {key}"})
        assert (method, path, r.status_code) == (method, path, 404)
        assert "set-cookie" not in r.headers
    # The main origin is unchanged: a page opened on its own goes to the viewer.
    r = client.get(
        f"/a/{art['id']}/raw", headers={"Sec-Fetch-Dest": "document"}, follow_redirects=False
    )
    assert r.status_code == 303


def test_viewer_page_has_every_element_its_scripts_use(env):
    client, _, keys = env
    static = Path(__file__).parent.parent / "src" / "agent_artifacts" / "static"
    used = {
        name: set(re.findall(r'\$\("([\w-]+)"\)', (static / name).read_text()))
        for name in ("comments.js", "viewer.js", "menu-dialogs.js")
    }
    # The data the comments script reads from the sheet.
    datasets = set(re.findall(r"sheet\.dataset\.(\w+)", (static / "comments.js").read_text()))
    assert datasets == {"artifact", "count", "kind", "pin", "pos"}
    # Shown only for images, or only for pages in a frame.
    image_only = {"image", "image-box", "image-link", "pins"}
    for filename, data, missing in (
        ("a.html", b"<p>x", image_only),
        ("b.png", PNG, {"frame"}),
        ("c.zip", b"PK\x03\x04", image_only | {"frame"}),
    ):
        art = upload(client, keys["alice"], filename, data).json()
        page = client.get(f"/a/{art['id']}").text
        ids = set(re.findall(r'\bid="([\w-]+)"', page))
        for name, wanted in used.items():
            assert wanted - missing - ids == set(), (filename, name)
        assert {
            "comments",
            "comments-toggle",
            "sheet-strip",
            "strip-text",
            "sheet-grip",
            "sheet-body",
            "sheet-foot",
            "name-dialog",
            "name-form",
            "name-input",
            "name-skip",
            "toast",
            "toast-text",
            "toast-undo",
        } <= ids, filename
        assert f'data-artifact="{art["id"]}"' in page
        # The theme is applied in <head>, before the body is drawn.
        head = page.split("</head>", 1)[0]
        assert re.search(r'<script src="/static/theme\.js\?v=\w+"></script>', head)
        assert re.search(r'role="menuitemcheckbox"[^>]*data-act="comment"', page)
        link = f"https://art.example.org/a/{art['id']}"
        assert f'id="share-link" type="text" readonly value="{link}"' in page


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
            "limits": {
                "ttl_seconds": 48 * 3600,
                "max_file_bytes": 2000,
                "max_text_bytes": 2 * 1024 * 1024,
                "types": me["limits"]["types"],
            },
        }
        types = me["limits"]["types"]
        assert ".html" in types and ".tar.gz" in types and ".exe" not in types
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
    me = client.get("/api/v1/me", headers=auth(keys["bob"])).json()
    assert me["limits"]["ttl_seconds"] == 86400
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
    store.add_draft("a" * 22, "h", author="x", text="y", anchor=None)
    [c] = store.publish("a" * 22, "h", author="x", text=None)
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


def test_viewer_page_has_comments_on_every_type(env):
    client, store, keys = env
    for name, data, pin in (
        ("a.html", b"<p>x", "element"),
        ("b.png", PNG, "point"),
        ("c.zip", b"PK\x03\x04", ""),
    ):
        art = upload(client, keys["alice"], name, data).json()
        page = client.get(f"/a/{art['id']}")
        # Before the viewer boots the server renders an empty folded sheet.
        assert '<aside id="comments" class="sheet" data-pos="strip"' in page.text, name
        assert f'data-pin="{pin}"' in page.text, name
        assert re.search(r'<aside id="comments"[^>]* hidden>', page.text), name
        for _ in range(2):
            store.add_draft(art["id"], "h", author="x", text="y", anchor=None)
        store.publish(art["id"], "h", author="x", text=None)
        store.resolve_comment(art["id"], 1, True, owner="alice")
        page = client.get(f"/a/{art['id']}")
        assert re.search(r'id="comments-toggle"[^>]*aria-expanded="false"', page.text), name
        # The strip counts the comments the viewer shows: open ones.
        assert '<span id="strip-text" class="strip-text">1 comment</span>' in page.text, name
        assert not re.search(r'<aside id="comments"[^>]* hidden>', page.text), name
        assert 'id="name-dialog" class="scrim" hidden' in page.text, name
        assert 'id="toast" class="toast" role="status"' in page.text, name
        assert re.search(
            r'<script src="/static/pin-marks\.js\?v=[0-9a-f]{12}"></script>\n'
            r'<script src="/static/comments-model\.js\?v=[0-9a-f]{12}"></script>\n'
            r'<script src="/static/sheet-drag\.js\?v=[0-9a-f]{12}"></script>\n'
            r'<script src="/static/links\.js\?v=[0-9a-f]{12}"></script>\n'
            r'<script src="/static/zoom\.js\?v=[0-9a-f]{12}"></script>\n'
            r'<script src="/static/comments-pins\.js\?v=[0-9a-f]{12}"></script>\n'
            r'<script src="/static/comments\.js\?v=[0-9a-f]{12}"></script>',
            page.text,
        ), name
        assert "connect-src 'self'" in page.headers["content-security-policy"], name
    assert client.get("/static/comments.js").status_code == 200
    assert client.get("/static/pin-marks.js").status_code == 200
    assert client.get("/static/comments-model.js").status_code == 200
    assert client.get("/static/sheet-drag.js").status_code == 200
    assert client.get("/static/comments-pins.js").status_code == 200
    assert client.get("/static/links.js").status_code == 200
    pin_css = re.search(r'<link rel="stylesheet" href="(/static/pin\.css\?v=[0-9a-f]{12})">\n<link rel="stylesheet" href="/static/site\.css', page.text)
    assert pin_css and client.get(pin_css.group(1)).status_code == 200
    css = re.search(r'href="(/static/site\.css\?v=[0-9a-f]{12})"', page.text)
    assert css and client.get(css.group(1)).status_code == 200


def test_only_the_viewer_may_connect(env):
    client, _, _ = env
    page = client.get("/a/" + "a" * 22)
    assert page.status_code == 404
    assert "connect-src" not in page.headers["content-security-policy"]
    assert "comments.js" not in page.text


def test_markdown_page_stays_about_the_size_of_the_file(env):
    client, _, keys = env
    text = "Привет, мир. " * 30 + "</script>\u2028"
    art = upload(client, keys["alice"], "ru.md", text.encode()).json()
    page = client.get(f"/a/{art['id']}/raw").text
    source = page.split("const SOURCE = ")[1].split(";\n")[0]
    assert json.loads(source) == text
    assert "</script>" not in source and "\u2028" not in source
    assert len(source.encode()) < len(text.encode()) * 1.2


def test_pages_are_sent_in_pieces_never_whole(env, tmp_path, monkeypatch):
    client, _, keys = env
    small = upload(client, keys["alice"], "a.html", b"<p>small</p>").json()
    r = client.get(f"/a/{small['id']}/raw")
    assert r.content == b"<p>small</p>" + PINS_SCRIPT
    assert r.headers["content-length"] == str(len(r.content))
    # A file many pieces long: read a piece at a time, the pieces joining
    # into the same bytes, the length known before the first.
    monkeypatch.setattr(pages, "CHUNK_BYTES", 100)
    big = tmp_path / "big.html"
    big.write_bytes(b"<p>" + b"x" * 1000 + b"</p>")
    body, length = pages.html_page(big, "big.html")
    pieces = list(body)
    assert all(len(p) <= 100 for p in pieces[:-1]) and len(pieces) > 10
    assert b"".join(pieces) == big.read_bytes() + PINS_SCRIPT
    assert length == len(b"".join(pieces))
    # Text split mid-character and a bad byte read the same as decoded whole.
    big.write_bytes(("Привет <&>\u2028" * 50).encode() + b"\xff")
    text = big.read_bytes().decode("utf-8", "replace")
    page = b"".join(pages.markdown_page(big, "a.md")[0]).decode()
    assert json.loads(page.split("const SOURCE = ")[1].split(";\n")[0]) == text
    page = b"".join(pages.text_page(big, "a.txt")[0]).decode()
    assert html.unescape(page.split("<pre>")[1].split("</pre>")[0]) == text


def test_sandboxed_forms_submit_nowhere():
    assert "form-action 'none'" in SANDBOX_CSP


def test_admin_bodies_skip_the_public_body_checks(env):
    # Kept as it was: the admin calls take a body without Content-Type or
    # Content-Length, and over the 16 KB the public calls allow.
    client, store, _ = env
    root = auth(store.add_agent("root", admin=True))
    padded = b'{"ttl_seconds": 86400' + b" " * 17000 + b"}"
    for method, path, body in (
        ("PATCH", "/api/v1/admin/settings", padded),
        ("PATCH", "/api/v1/admin/users/alice", b'{"quota_bytes": null}'),
        ("POST", "/api/v1/admin/invites", b'{"name": "carol"}'),
    ):
        r = client.request(method, path, content=iter([body]), headers=root)
        assert r.status_code in (200, 201), (path, r.text)


def test_the_frame_takes_the_pin_look_only_when_strictly_true():
    # The viewer tells the untrusted frame two settings; anything but true
    # (a string, 1, an object) leaves the pins as they are.
    static = Path(__file__).parent.parent / "src" / "agent_artifacts" / "static"
    pins = (static / "pins.js").read_text()
    assert 'toggle("pins-big", m.big === true)' in pins
    assert 'toggle("pins-shaped", m.shaped === true)' in pins
    # The dot's colour: only a name from the list, as a colour the frame
    # writes itself (tests/js/pin-marks.test.js checks the list).
    assert 'layer.style.setProperty("--dot", dotColour(m.dot))' in pins


def test_a_tap_on_an_empty_spot_lets_go_only_outside_comment_mode():
    # The frame reports a plain tap away from what the page acts on, never in
    # pin mode; the viewer takes it only from its frame, outside pin mode,
    # and lets go only when something is selected and comment mode is off.
    static = Path(__file__).parent.parent / "src" / "agent_artifacts" / "static"
    pins = (static / "pins.js").read_text()
    assert "if (picking || !tapped || e.defaultPrevented" in pins
    assert "e.detail !== 1 || fromLayer(e)\n      || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;" in pins
    assert "area[href],canvas,${WIDGETS" in pins and '"button link tab menuitem' in pins
    assert 'x.matches(ACTS) || getComputedStyle(x).cursor === "pointer"' in pins
    assert "press.y) <= 10;" in pins and "press.tap = near(e) && performance.now() - press.at <= 500;" in pins
    viewer_pins = (static / "comments-pins.js").read_text()
    assert 'm.type === "blank" && !page.picking\n      && (!navigator.userActivation || navigator.userActivation.isActive)' in viewer_pins
    assert 'imageBox.addEventListener("click"' in viewer_pins
    assert "press.y) <= 10 && performance.now() - press.at <= 500;" in viewer_pins
    handler = viewer_pins.index("function onFrameMessage(e) {")
    assert "if (e.source !== frame.contentWindow) return;" in viewer_pins[handler:handler + 120]
    comments = (static / "comments.js").read_text()
    blank = comments[comments.index("function blank() {"):]
    blank = blank[:blank.index("\n  }\n")]
    assert "if (S.mode || !S.sel || keeps()) return;" in blank and "toast" not in blank
    keeps = comments[comments.index("function keeps() {"):]
    assert "fields.reply.value.trim() ||" in keeps[:keeps.index("\n  }\n")]


def test_with_a_pin_lit_the_first_tap_is_held_back_from_the_page():
    # Outside pin mode, while a pin the viewer sent is lit and drawn (never
    # the new draft's) and the viewer did not say hold: false, the frame
    # keeps a left press that lifts as a tap from the page: its down events
    # pass, a move over 10 px or a scroll lets it go, the rest stops, its
    # click is cancelled and reports blank. A keyboard click is the page's.
    static = Path(__file__).parent.parent / "src" / "agent_artifacts" / "static"
    pins = (static / "pins.js").read_text()
    assert 'hold && pins.some((p) => p.lit && p.key !== "new" && drawn.has(p.key));' in pins
    assert "held = !picking && e.button === 0 && !fromLayer(e) && selected();" in pins
    assert "if (held && press && e.isPrimary && !near(e)) held = false;" in pins
    assert "if (!press.tap) held = false;" in pins
    assert "{ press = null; held = false; }" in pins
    assert 'if (!picking && (e.type === "pointerdown" || (e.type === "mousedown" && !(press && press.up)))) return;' in pins
    assert "hold = m.hold !== false;" in pins
    held = pins[pins.index("if (!held || picking || fromLayer(e) || e.detail === 0) return;"):]
    held = held[:held.index("}, true);")]
    assert "e.stopImmediatePropagation();\n    e.preventDefault();" in held
    assert 'tell({ type: "blank" });' in held
    viewer_pins = (static / "comments-pins.js").read_text()
    assert "hold: hold() });" in viewer_pins
    comments = (static / "comments.js").read_text()
    assert "hold: () => { kept = keeps(); return !kept; }," in comments
    assert pins.index("pageZoom.attach(window") < pins.index("let held = false;") < pins.index("function swallow(e)")


def test_local_deadline_has_utc_fallback_and_neutral_api_timestamp(env):
    client, store, keys = env
    store.clock = lambda: 1795395600.0  # 2026-11-23 01:00 UTC
    artifact = upload(client, keys["alice"], "date.html", b"date").json()
    assert artifact["expires_at"] == "2026-11-25T01:00:00Z"
    page = client.get(f"/a/{artifact['id']}")
    assert ('<time datetime="2026-11-25T01:00:00Z" data-local-deadline>'
            '2026-11-25 01:00 UTC</time>') in page.text
    invite, token = store.add_invite("guest", by="alice", ttl=86400)
    assert invite.expires_at == 1795482000.0
    join = client.get(f"/join/{token}")
    assert ('<time datetime="2026-11-24T01:00:00Z" data-local-deadline>'
            '2026-11-24 01:00 UTC</time>') in join.text
