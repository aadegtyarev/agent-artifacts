"""Independent readers, explicit Pi binding, retries, and scoped acknowledgments."""

import io
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_artifacts.config import Config
from agent_artifacts.store import Store
from agent_artifacts.web import create_app

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill/agent-artifacts/scripts"
sys.path.insert(0, str(SCRIPTS))
from watch import recover  # noqa: E402
from watch_state import WatchState  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "posix", reason="watch uses POSIX advisory locks")
WATCH = [sys.executable, str(SCRIPTS / "artifacts_cli.py"), "watch"]
ORIGIN = "https://example.test"
TARGET = "a" * 22
LINK = f"{ORIGIN}/a/{TARGET}"


def test_invalid_collector_configuration_does_not_create_state(tmp_path):
    env = {
        "XDG_STATE_HOME": str(tmp_path),
        "AGENT_ARTIFACTS_URL": "https://%65xample.test",
        "AGENT_ARTIFACTS_KEY": "not-used",
    }
    r = subprocess.run(  # noqa: S603
        WATCH + ["collect", LINK], env=env, capture_output=True, text=True, timeout=3
    )
    assert r.returncode == 1 and "invalid link, configuration, or local state" in r.stderr
    assert not (tmp_path / "agent-artifacts").exists()


def test_follow_deadline_under_output_and_sqlite_backpressure(tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path)}
    command = WATCH + ["follow", LINK, "--timeout", "0.3"]
    root = tmp_path / "agent-artifacts/reliable-watch"
    empty = subprocess.run(command, env=env, capture_output=True, timeout=3)  # noqa: S603
    assert empty.returncode == 0 and not empty.stdout and not root.exists()
    state = WatchState(root, ORIGIN, "a", TARGET)
    state._commit(0, "{}", ['{"event":"waiting","comments":[{"n":1}]}'], None)
    pending = state.pending()
    try:
        read_fd, write_fd = os.pipe()
        try:
            os.set_blocking(write_fd, False)
            with pytest.raises(BlockingIOError):
                while True:
                    os.write(write_fd, b"x" * 4096)
            os.set_blocking(write_fd, True)
            # Do not drain the pipe until the process has exited.
            with subprocess.Popen(  # noqa: S603
                command, env=env, stdout=write_fd, stderr=subprocess.PIPE
            ) as reader:
                try:
                    assert reader.wait(timeout=3) == 0
                    assert b"deadline reached" in reader.stderr.read()
                finally:
                    if reader.poll() is None:
                        reader.kill()
        finally:
            os.close(write_fd)
            os.close(read_fd)
        assert state.pending() == pending
        state.db.execute("BEGIN EXCLUSIVE")
        locked = subprocess.run(command, env=env, capture_output=True, timeout=3)  # noqa: S603
        assert locked.returncode == 0 and not locked.stdout
        assert b"deadline reached" in locked.stderr
        state.db.rollback()
        reattached = subprocess.run(command, env=env, capture_output=True, timeout=3)  # noqa: S603
        assert reattached.returncode == 0
        assert json.loads(reattached.stdout) == pending[0] and state.pending() == pending
    finally:
        state.close()


def test_recover_reports_taken_threads_without_changing_them(tmp_path):
    origin = "http://127.0.0.1:18151"
    cfg = Config(data_dir=tmp_path, public_url=origin)
    store = Store(tmp_path, max_total_bytes=10000)
    key = store.add_agent("fixture")
    project = store.add_project("fixture", "scope")
    a = store.put(
        io.BytesIO(b"<p>demo</p>"),
        owner="fixture",
        name="demo.html",
        kind="html",
        size=11,
        project=project.id,
    )
    for i in range(2):
        store.add_draft(a.id, "viewer", author="Fixture reviewer", text="private", anchor=None)
        store.publish(a.id, "viewer", author="Fixture reviewer", text=None, for_agent=True)
        if i == 0:
            assert len(store.take(a.id, owner="fixture")) == 1

    with TestClient(create_app(cfg, store), base_url=origin) as http:
        requests = []

        class LocalClient:
            def send(self, method, path):
                requests.append((method, path))
                r = http.request(method, path, headers={"Authorization": f"Bearer {key}"})
                assert r.status_code == 200
                return r.text

        for kind, target in (("p", project.id), ("a", a.id)):
            result = recover(LocalClient(), (origin, kind, target))
            assert result == {
                "scope": {"kind": kind, "id": target},
                "unfinished": [{"id": a.id, "comments": [{"n": 1}]}],
            }
        assert all(method == "GET" for method, _ in requests)
    _, comments = store.comments(a.id, owner="fixture")
    assert [(c.n, c.agent) for c in comments] == [(1, "working"), (2, "waiting")]


def test_pi_adapter_retry_reattach_and_scope(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    target = "a" * 22
    link = f"{origin}/a/{target}"
    other = f"{origin}/a/{'b' * 22}"
    directory = tmp_path / "agent-artifacts/reliable-watch"
    state = WatchState(directory, origin, "a", target)
    state._commit(
        0,
        json.dumps(
            {"waiting": {}, "resolved": None, "since": None, "accepted": {}, "next_detail": None}
        ),
        ['{"event":"waiting","comments":[{"n":1}]}'],
        None,
    )
    event_id = state.pending()[0]["id"]
    state.close()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    broker = bin_dir / "pi-intercom"
    broker.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ["BROKER"])
if sys.argv[1] == "list":
 print(json.dumps({"ok": True, "sessions": [{"id": "one"}, {"id": "two"}]}))
elif not (p / "up").exists():
 print(json.dumps({"ok": False}))
else:
 with (p / "sent").open("a") as f: f.write(json.dumps(sys.argv) + "\\n")
 print(json.dumps({"ok": True, "delivered": True}))
""")
    broker.chmod(0o755)
    env = dict(
        os.environ,
        XDG_STATE_HOME=str(tmp_path),
        BROKER=str(tmp_path),
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        AGENT_ARTIFACTS_URL=origin,
        AGENT_ARTIFACTS_KEY="fixture",
    )
    cmd = [sys.executable, str(SCRIPTS / "artifacts_cli.py"), "watch"]
    procs = []

    def run(*args):
        return subprocess.run(cmd + list(args), env=env, capture_output=True, text=True, timeout=5)  # noqa: S603

    def start(*args):
        p = subprocess.Popen(  # noqa: S603
            cmd + list(args), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        procs.append(p)
        return p

    def wait(predicate, timeout=14):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.1)
        raise AssertionError("timeout waiting for adapter")

    def status():
        result = run("status", link)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    try:
        collector = start("collect", link, "--every", "5")
        wait(lambda: status()["collector_active"])
        collector_pid = collector.pid
        pi = start("pi", link, "--session", "one")
        wait(lambda: status()["recipient"] == "one")
        wait(lambda: status()["delivery_failure"] is not None)
        assert run("pi", link, "--session", "two").returncode == 1
        assert status()["pending"] == 1
        (tmp_path / "up").touch()
        wait(lambda: (tmp_path / "sent").exists())
        assert pi.poll() is None and status()["pending"] == 1
        time.sleep(6)
        assert len((tmp_path / "sent").read_text().splitlines()) == 1
        pi.kill()
        pi.wait(timeout=5)
        assert status()["recipient"] is None
        start("pi", link, "--session", "two")
        wait(lambda: status()["recipient"] == "two")
        wait(lambda: len((tmp_path / "sent").read_text().splitlines()) == 2)
        assert '"two"' in (tmp_path / "sent").read_text().splitlines()[-1]
        assert run("ack", other, event_id).returncode == 1
        assert status()["pending"] == 1
        result = run("follow", link, "--timeout", "0.1")
        assert json.loads(result.stdout)["id"] == event_id and "deadline" in result.stderr
        assert run("ack", link, event_id).returncode == 0
        assert status()["pending"] == 0
        assert collector.pid == collector_pid and collector.poll() is None
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
