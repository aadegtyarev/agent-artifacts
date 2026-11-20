"""The skill's aa command against a real server on a free port."""

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from agent_artifacts.config import Config
from agent_artifacts.store import Store
from agent_artifacts.web import create_app

AA = Path(__file__).parent.parent / "skill" / "agent-artifacts" / "scripts" / "aa"


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


def aa(home, *args, stdin=None, env=None):
    r = subprocess.run(  # noqa: S603 (our own script)
        [str(AA), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), **(env or {})},
    )
    return r.returncode, r.stdout, r.stderr


def test_setup_upload_and_new_key(server):
    url, store, home = server
    key = store.add_agent("alice")
    assert aa(home, "me")[0] == 2  # no key yet
    code, out, _ = aa(
        home, "setup", stdin=f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY={key}\n"
    )
    env_file = home / ".config" / "agent-artifacts" / "env"
    assert code == 0 and str(env_file) in out
    assert env_file.stat().st_mode & 0o777 == 0o600

    page = home / "a;b.html"
    page.write_text("<h1>hi</h1>")
    code, out, _ = aa(home, "upload", str(page), "@etc.html")
    art = json.loads(out)
    assert code == 0 and art["name"] == "@etc.html"
    assert json.loads(aa(home, "list")[1])[0]["id"] == art["id"]

    code, out, _ = aa(home, "new-key")
    assert code == 0 and "saved" in out and key not in env_file.read_text()
    assert json.loads(aa(home, "me")[1])["name"] == "alice"
    assert aa(home, "rm", art["id"])[0] == 0

    code, _, err = aa(home, "me", env={"AGENT_ARTIFACTS_KEY": key})  # the old key, from env
    assert code == 1 and "unauthorized" in err


def test_setup_refuses_a_wrong_key(server):
    url, _, home = server
    code, _, err = aa(home, "setup", stdin=f"AGENT_ARTIFACTS_URL={url}\nAGENT_ARTIFACTS_KEY=nope\n")
    assert code == 1 and "unauthorized" in err
    assert not (home / ".config" / "agent-artifacts" / "env").exists()
