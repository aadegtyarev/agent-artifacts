"""The skill's artifacts_cli.py against a real server on a free port."""

import json
import re
import socket
import subprocess
import sys
import threading
import time
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

    code, out, _ = cli(home, "new-key")
    assert code == 0 and "saved" in out and key not in env_file.read_text()
    assert json.loads(cli(home, "me")[1])["name"] == "alice"
    assert cli(home, "replace", art["id"], str(page))[0] == 0
    assert cli(home, "delete", art["id"])[1].strip() == "deleted"

    code, _, err = cli(home, "me", env={"AGENT_ARTIFACTS_KEY": key})  # the old key, from env
    assert code == 1 and "unauthorized" in err


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
    assert code == 0 and [(c["text"], c["read"]) for c in json.loads(out)] == [
        ("one", True),
        ("two", True),
    ]
    assert json.loads(cli(home, "resolve", art_id, "1", env=env)[1])["resolved"] is True
    assert json.loads(cli(home, "list", env=env)[1])[0]["open_comments"] == 1
    assert json.loads(cli(home, "reopen", art_id, "1", env=env)[1])["resolved"] is False
    assert cli(home, "delete-comment", art_id, "2", env=env)[1].strip() == "deleted"
    assert cli(home, "resolve", art_id, "x", env=env)[0] == 2
    store.add_draft(art_id, "h", author="p", text="unsent", anchor=None)
    assert cli(home, "drop-drafts", art_id, env=env)[1].strip() == "drafts deleted"
    assert store.drafts(art_id, "h") == []
