"""The skill's artifacts_cli.py against a real server on a free port."""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from agent_artifacts.config import Config
from agent_artifacts.store import Store
from agent_artifacts.web import create_app

CLI = Path(__file__).parent.parent / "skill" / "agent-artifacts" / "scripts" / "artifacts_cli.py"


@pytest.fixture
def server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    store = Store(tmp_path / "data", max_total_bytes=10**6)
    app = create_app(Config(data_dir=tmp_path / "data", public_url=url), store)
    srv = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.01)
    yield url, store, tmp_path
    srv.should_exit = True
    thread.join()


def cli(home, *args, stdin=None, env=None):
    r = subprocess.run(  # noqa: S603 (our own script)
        [sys.executable, str(CLI), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), **(env or {})},
    )
    return r.returncode, r.stdout, r.stderr


def test_setup_upload_and_new_key(server):
    url, store, home = server
    key = store.add_agent("alice")
    assert cli(home, "me")[0] == 2  # no key yet
    code, out, _ = cli(
        home, "setup", stdin=f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY={key}\n"
    )
    env_file = home / ".config" / "agent-artifacts" / "env"
    assert code == 0 and str(env_file) in out
    assert env_file.stat().st_mode & 0o777 == 0o600

    page = home / "a;b.html"
    page.write_text("<h1>hi</h1>")
    code, out, _ = cli(home, "upload", str(page), "@etc.html")
    art = json.loads(out)
    assert code == 0 and art["name"] == "@etc.html"
    assert json.loads(cli(home, "list")[1])[0]["id"] == art["id"]
    code, out, _ = cli(home, "upload", str(page), "--title", "Q3 report")
    titled = json.loads(out)
    assert code == 0 and (titled["name"], titled["title"]) == ("a;b.html", "Q3 report")
    titled = json.loads(cli(home, "replace", titled["id"], str(page), "c.html")[1])
    assert (titled["name"], titled["title"]) == ("c.html", "Q3 report")
    out = cli(home, "replace", titled["id"], str(page), "--title", "Q4 report")[1]
    assert json.loads(out)["title"] == "Q4 report"
    assert json.loads(cli(home, "set", titled["id"], "--title", "Final")[1])["title"] == "Final"
    assert cli(home, "set", titled["id"])[0] == 2

    code, out, _ = cli(home, "new-key")
    assert code == 0 and "saved" in out and key not in env_file.read_text()
    assert json.loads(cli(home, "me")[1])["name"] == "alice"
    assert cli(home, "replace", art["id"], str(page))[0] == 0
    assert cli(home, "delete", art["id"])[1].strip() == "deleted"

    old = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": key}
    code, _, err = cli(home, "me", env=old)  # the old key, from env
    assert code == 1 and "unauthorized" in err


def test_projects(server):
    url, store, home = server
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": store.add_agent("alice")}
    page = home / "a.html"
    page.write_text("<h1>hi</h1>")
    p = json.loads(cli(home, "project-new", "Landing", env=env)[1])
    assert p["title"] == "Landing" and p["url"] == f"{url}/p/{p['id']}"
    out = cli(home, "upload", str(page), "--project", p["id"], "--section", "Mockups", env=env)[1]
    art = json.loads(out)
    assert (art["project"], art["section"], art["expires_at"]) == (p["id"], "Mockups", None)
    out = cli(home, "replace", art["id"], str(page), "--section", "Docs", env=env)[1]
    assert (json.loads(out)["project"], json.loads(out)["section"]) == (p["id"], "Docs")
    assert (
        json.loads(cli(home, "set", art["id"], "--section", "none", env=env)[1])["section"] is None
    )
    out = json.loads(cli(home, "set", art["id"], "--project", "none", env=env)[1])
    assert out["project"] is None and out["expires_at"] is not None
    cli(home, "set", art["id"], "--project", p["id"], "--title", "Hero", env=env)
    assert cli(home, "project-rename", p["id"], "Site", env=env)[0] == 0
    [listed] = json.loads(cli(home, "projects", env=env)[1])
    assert (listed["title"], listed["files"]) == ("Site", 1)
    one = json.loads(cli(home, "project", p["id"], env=env)[1])
    assert [a["title"] for a in one["artifacts"]] == ["Hero"]
    assert cli(home, "project-delete", p["id"], env=env)[1].strip() == "deleted"
    code, _, err = cli(home, "project", p["id"], env=env)
    assert code == 1 and "not_found" in err


def test_setup_keeps_a_different_key(server):
    url, store, home = server
    first, second = store.add_agent("admin", admin=True), store.add_agent("pi")
    env_file = home / ".config" / "agent-artifacts" / "env"
    for key in (first, first, second):
        cli(home, "setup", stdin=f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY={key}\n")
    (backup,) = env_file.parent.glob("env.bak-*")  # the same key again is not backed up
    assert first in backup.read_text() and second in env_file.read_text()
    assert backup.stat().st_mode & 0o777 == 0o600


def test_setup_refuses_a_wrong_key(server):
    url, _, home = server
    code, _, err = cli(
        home, "setup", stdin=f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY=nope\n"
    )
    assert code == 1 and "unauthorized" in err
    assert not (home / ".config" / "agent-artifacts" / "env").exists()


def test_help_admin_and_env_key(server):
    url, store, home = server
    assert cli(home, "--help")[0] == 0
    code, out, _ = cli(home, "version")  # works without a key
    assert code == 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}\n", out)
    root = store.add_agent("root", admin=True)
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": root}
    code, out, _ = cli(home, "invite", "petrov", "2", env=env)
    assert (
        json.loads(out)["expires_at"]
        < json.loads(cli(home, "invite", "ivanov", env=env)[1])["expires_at"]
    )
    assert code == 0 and json.loads(out)["url"].startswith(f"{url}/join/")
    assert json.loads(cli(home, "quota", "root", "10", env=env)[1])["quota_bytes"] == 10 * 1024**2
    out = json.loads(cli(home, "settings", "ttl-hours", "24", env=env)[1])
    assert out["ttl_seconds"] == 86400
    assert json.loads(cli(home, "settings", env=env)[1]) == out
    assert cli(home, "settings", "ttl", "24", env=env)[0] == 2
    code, out, _ = cli(home, "new-key", env=env)  # from the environment: printed, not saved
    assert code == 0 and out.splitlines()[-1].startswith("aa_")
    assert not (home / ".config" / "agent-artifacts" / "env").exists()


def test_a_redirect_is_an_error_and_keeps_the_key(tmp_path):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.headers.get("Authorization"))
            self.send_response(301)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    env = {"AGENT_ARTIFACTS_URL": f"http://127.0.0.1:{srv.server_port}", "AGENT_ARTIFACTS_KEY": "k"}
    code, _, err = cli(tmp_path, "me", env=env)
    srv.server_close()
    assert code == 1 and "301" in err and len(seen) == 1


def test_plain_http_only_for_a_local_server(tmp_path):
    env = {"AGENT_ARTIFACTS_URL": "http://art.example.org", "AGENT_ARTIFACTS_KEY": "k"}
    code, _, err = cli(tmp_path, "me", env=env)
    assert code == 2 and "https://" in err


def test_comments_resolve_delete_and_drop_drafts(server):
    url, store, home = server
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": store.add_agent("alice")}
    page = home / "a.html"
    page.write_text("<p>hi</p>")
    art_id = json.loads(cli(home, "upload", str(page), env=env)[1])["id"]
    for text in ("one", "two"):
        store.add_draft(art_id, "h", author="p", text=text, anchor=None)
    store.publish(art_id, "h", author="p", text=None)
    code, out, _ = cli(home, "comments", art_id, env=env)
    assert code == 0 and [c["text"] for c in json.loads(out)] == ["one", "two"]
    assert json.loads(cli(home, "resolve", art_id, "1", env=env)[1])["resolved"] is True
    assert json.loads(cli(home, "list", env=env)[1])[0]["open_comments"] == 1
    assert json.loads(cli(home, "reopen", art_id, "1", env=env)[1])["resolved"] is False
    assert cli(home, "delete-comment", art_id, "2", env=env)[1].strip() == "deleted"
    assert cli(home, "resolve", art_id, "x", env=env)[0] == 2
    store.add_draft(art_id, "h", author="p", text="unsent", anchor=None)
    assert cli(home, "drop-drafts", art_id, env=env)[1].strip() == "drafts deleted"
    assert store.drafts(art_id, "h") == []


def test_take_and_reply(server):
    url, store, home = server
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": store.add_agent("alice")}
    page = home / "a.html"
    page.write_text("<p>hi</p>")
    art_id = json.loads(cli(home, "upload", str(page), env=env)[1])["id"]
    store.add_draft(art_id, "h", author="p", text="make it green", anchor=None)
    store.publish(art_id, "h", author="p", text=None, for_agent=True)
    code, out, _ = cli(home, "take", art_id, env=env)
    assert code == 0 and [(c["n"], c["agent"]) for c in json.loads(out)] == [(1, "working")]
    code, out, _ = cli(home, "reply", art_id, "1", "Green now", env=env)
    c = json.loads(out)
    assert code == 0 and c["agent"] == "done" and c["replies"][0]["text"] == "Green now"
    for args, state, text in (
        (("--asked", art_id, "1", "Which?"), "asked", "Which?"),
        ((art_id, "1", "Hm", "--wrote"), "wrote", "Hm"),
        # A text that looks like the option is still the text.
        ((art_id, "1", "--wrote"), "done", "--wrote"),
    ):
        c = json.loads(cli(home, "reply", *args, env=env)[1])
        assert (c["agent"], c["replies"][-1]["text"]) == (state, text)
    assert cli(home, "reply", art_id, "1", "x", "--wrote", "--asked", env=env)[0] == 2
    assert cli(home, "reply", art_id, "1", "--asked", "x", env=env)[0] == 2
    assert cli(home, "reply", art_id, "1", env=env)[0] == 2


def _load_cli(monkeypatch, url, key, sleep):
    """The script imported, so wait's polls need not sleep for real; its
    ``time`` is swapped, not the module's own, which the server uses too."""
    import importlib.util
    import types

    spec = importlib.util.spec_from_file_location("artifacts_cli", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(monotonic=time.monotonic, sleep=sleep))
    monkeypatch.setenv("AGENT_ARTIFACTS_URL", url)
    monkeypatch.setenv("AGENT_ARTIFACTS_KEY", key)
    return mod


def test_wait_waiting_gone_and_timeout(server):
    url, store, home = server
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": store.add_agent("alice")}
    page = home / "a.html"
    page.write_text("<p>hi</p>")
    art_id = json.loads(cli(home, "upload", str(page), env=env)[1])["id"]
    start = time.monotonic()
    assert json.loads(cli(home, "wait", art_id, "--timeout", "1", env=env)[1]) == {
        "event": "timeout"
    }
    assert time.monotonic() - start < 5  # the timeout cuts the poll sleep short
    store.add_draft(art_id, "h", author="p", text="plain", anchor=None)
    store.publish(art_id, "h", author="p", text=None)
    store.add_draft(art_id, "h", author="p", text="make it green", anchor=None)
    store.publish(art_id, "h", author="p", text=None, for_agent=True)
    code, out, _ = cli(home, "wait", art_id, env=env)
    event = json.loads(out)
    assert code == 0 and event["event"] == "waiting"
    assert [c["text"] for c in event["comments"]] == ["make it green"]
    assert cli(home, "wait", art_id, "--every", "1", env=env)[0] == 2
    cli(home, "delete", art_id, env=env)
    assert json.loads(cli(home, "wait", art_id, env=env)[1]) == {"event": "gone"}


@pytest.mark.parametrize(("options", "interval"), [((), 10), (("--every", "7"), 7)])
def test_wait_accepted_counts_only_new(server, monkeypatch, options, interval):
    url, store, home = server
    key = store.add_agent("alice")
    page = home / "a.html"
    page.write_text("<p>hi</p>")
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": key}
    art_id = json.loads(cli(home, "upload", str(page), env=env)[1])["id"]
    for text in ("old", "new"):
        store.add_draft(art_id, "h", author="p", text=text, anchor=None)
    store.publish(art_id, "h", author="p", text=None)
    store.resolve_comment(art_id, 1, True, owner="alice")
    polls = []

    def sleep(seconds):  # between polls: the person accepts comment 2
        polls.append(seconds)
        if len(polls) == 2:
            store.resolve_comment(art_id, 2, True, owner="alice")

    mod = _load_cli(monkeypatch, url, key, sleep)
    event = json.loads(mod.run(["wait", art_id, *options]))
    assert event["event"] == "accepted" and [c["n"] for c in event["comments"]] == [2]
    assert polls == [interval, interval]


def test_wait_on_a_project(server, monkeypatch):
    url, store, home = server
    key = store.add_agent("alice")
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": key}
    page = home / "a.html"
    page.write_text("<p>hi</p>")
    p = json.loads(cli(home, "project-new", "Site", env=env)[1])
    a = json.loads(cli(home, "upload", str(page), "--project", p["id"], env=env)[1])["id"]
    store.add_draft(a, "h", author="p", text="old", anchor=None)
    store.publish(a, "h", author="p", text=None)
    added = []

    def sleep(seconds):  # between polls: a new file gets a comment for the agent
        if not added:
            out = cli(home, "upload", str(page), "--project", p["id"], "--title", "New", env=env)
            added.append(json.loads(out[1])["id"])
            store.add_draft(added[0], "h", author="p", text="fix", anchor=None)
            store.publish(added[0], "h", author="p", text=None, for_agent=True)

    mod = _load_cli(monkeypatch, url, key, sleep)
    # A bare id: the server knows it as a project.
    assert json.loads(mod.run(["wait", p["id"]])) == {
        "event": "waiting",
        "project": p["id"],
        "files": [{"id": added[0], "title": "New", "waiting": 1}],
    }
    store.take(added[0], owner="alice")

    def accept(seconds):
        store.resolve_comment(a, 1, True, owner="alice")

    mod = _load_cli(monkeypatch, url, key, accept)
    assert json.loads(mod.run(["wait", p["url"]])) == {
        "event": "accepted",
        "project": p["id"],
        "files": [{"id": a, "title": "a.html", "accepted": 1}],
    }
    # Nothing new: the comment accepted before the start does not count.
    assert json.loads(cli(home, "wait", p["url"], "--timeout", "1", env=env)[1]) == {
        "event": "timeout"
    }
    cli(home, "project-delete", p["id"], env=env)
    for target in (p["url"], p["id"]):
        assert json.loads(cli(home, "wait", target, env=env)[1]) == {"event": "gone"}


def test_wait_retries_a_failed_poll_and_stops_on_auth(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    waiting = [{"n": 1, "agent": "waiting", "resolved": False}]
    answers = [(503, "busy"), (502, "<html>proxy</html>"), (200, "not json")]
    answers += [(200, json.dumps(waiting)), (401, '{"error": "unauthorized"}')]

    class Flaky(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            # A bare id is tried as a project first: not one here.
            code, body = (404, "{}") if "/projects/" in self.path else answers.pop(0)
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Flaky)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mod = _load_cli(monkeypatch, f"http://127.0.0.1:{srv.server_port}", "k", lambda s: None)
    try:
        event = json.loads(mod.run(["wait", "x" * 22]))
        assert event == {"event": "waiting", "comments": waiting}
        with pytest.raises(SystemExit) as e:
            mod.run(["wait", "x" * 22])
        assert e.value.code == 1 and answers == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_ids_bare_or_as_links(server):
    url, store, home = server
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": store.add_agent("alice")}
    page = home / "a.html"
    page.write_text("<p>hi</p>")
    p = json.loads(cli(home, "project-new", "Site", env=env)[1])
    art = json.loads(cli(home, "upload", str(page), "--project", p["url"], env=env)[1])
    assert art["project"] == p["id"]
    assert json.loads(cli(home, "project", p["url"] + "/", env=env)[1])["id"] == p["id"]
    # A link is not a deleted file to wait reports as gone.
    out = cli(home, "wait", art["url"], "--timeout", "1", env=env)[1]
    assert json.loads(out) == {"event": "timeout"}
    for args in (
        ("delete", f"{art['id']}/drafts"),  # would reach drop-drafts
        ("delete", p["url"]),  # a project's link is not a file's
        ("comments", "nope"),
        ("project", art["url"]),
        ("upload", str(page), "--project", "../x"),
    ):
        code, _, err = cli(home, *args, env=env)
        assert code == 2 and "22 letters" in err, args
    assert cli(home, "delete", art["url"], env=env)[1].strip() == "deleted"


def test_url_and_key_come_from_one_place(server):
    url, store, home = server
    key = store.add_agent("alice")
    setup = f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY={key}\n"
    assert cli(home, "setup", stdin=setup)[0] == 0
    # A URL alone in the environment must not get the saved key.
    code, _, err = cli(home, "me", env={"AGENT_ARTIFACTS_URL": "https://other.example.org"})
    assert code == 2 and "AGENT_ARTIFACTS_KEY is not" in err
    code, _, err = cli(home, "me", env={"AGENT_ARTIFACTS_KEY": key})
    assert code == 2 and "AGENT_ARTIFACTS_URL is not" in err
    code, out, _ = cli(home, "setup", stdin=setup, env={"AGENT_ARTIFACTS_KEY": "old"})
    assert code == 0 and "AGENT_ARTIFACTS_KEY in the environment win" in out


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root writes anyway")
def test_new_key_stops_before_the_change_when_it_cannot_save(server):
    url, store, home = server
    key = store.add_agent("alice")
    cli(home, "setup", stdin=f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY={key}\n")
    folder = home / ".config" / "agent-artifacts"
    folder.chmod(0o500)
    try:
        code, _, err = cli(home, "new-key")
    finally:
        folder.chmod(0o700)
    assert code == 1 and "the key is not changed" in err
    assert json.loads(cli(home, "me")[1])["name"] == "alice"  # the old key still works


def test_new_key_printed_when_saving_fails(server, monkeypatch, capsys):
    import importlib.util

    url, store, home = server
    key = store.add_agent("alice")
    env_file = home / "env"
    env_file.write_text(f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY={key}\n")
    spec = importlib.util.spec_from_file_location("artifacts_cli", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("AGENT_ARTIFACTS_URL", raising=False)
    monkeypatch.delenv("AGENT_ARTIFACTS_KEY", raising=False)
    monkeypatch.setenv("AGENT_ARTIFACTS_ENV", str(env_file))

    def full(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod, "write_settings", full)
    with pytest.raises(SystemExit) as e:
        mod.run(["new-key"])
    out = capsys.readouterr().out
    assert e.value.code == 1 and "No space left" in out
    new = out.splitlines()[-1].removeprefix("AGENT_ARTIFACTS_KEY=")
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": new}
    assert json.loads(cli(home, "me", env=env)[1])["name"] == "alice"


def test_output_is_utf8_on_a_narrow_console(server):
    url, store, home = server
    env = {"AGENT_ARTIFACTS_URL": url, "AGENT_ARTIFACTS_KEY": store.add_agent("alice")}
    page = home / "a.html"
    page.write_text("<p>hi</p>")
    art_id = json.loads(cli(home, "upload", str(page), env=env)[1])["id"]
    store.add_draft(art_id, "h", author="p", text="сделай зелёным \U0001f600", anchor=None)
    store.publish(art_id, "h", author="p", text=None, for_agent=True)
    r = subprocess.run(  # noqa: S603 (our own script)
        [sys.executable, str(CLI), "take", art_id],
        capture_output=True,
        env={"HOME": str(home), "PYTHONIOENCODING": "cp1252", **env},
    )
    assert r.returncode == 0 and "сделай зелёным" in r.stdout.decode()


def test_bundle_packs_the_files_a_page_points_at(server, tmp_path):
    url, store, home = server
    key = store.add_agent("alice")
    cli(home, "setup", stdin=f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY={key}\n")

    work = home / "work"
    work.mkdir()
    (work / "style.css").write_text("body { color: #222; }")
    (work / "app.js").write_text("console.log('hi');")
    (work / "pic.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    page = work / "page.html"
    page.write_text(
        "<html><head><link rel='stylesheet' href='style.css'></head>"
        "<body><script src='app.js'></script><img src='pic.svg'>"
        "<img src='https://example.com/remote.png'></body></html>"
    )

    out_file = work / "packed.html"
    code, out, _ = cli(home, "bundle", str(page), "--out", str(out_file))
    assert code == 0 and "style.css" in out and "pic.svg" in out
    packed = out_file.read_text()
    assert "data:image/svg+xml;base64," in packed
    assert "body { color: #222; }" in packed
    assert "console.log('hi');" in packed
    assert "https://example.com/remote.png" in packed  # a remote file stays as it is
    assert 'href="style.css"' not in packed

    code, out, _ = cli(home, "upload", str(page), "--inline")
    assert code == 0
    art = json.loads(out.splitlines()[0])
    assert "packed 3 file(s)" in out
    fetched = urllib.request.urlopen(f"{url}/a/{art['id']}/raw").read().decode("utf-8")
    assert "data:image/svg+xml;base64," in fetched
    assert "body { color: #222; }" in fetched
