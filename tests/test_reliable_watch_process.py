"""Exercise the explicit watch commands against a real loopback HTTP server."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill/agent-artifacts/scripts"
SCRIPT = SCRIPTS / "watch.py"
TARGET = "a" * 22

sys.path.insert(0, str(SCRIPTS))
import watch  # noqa: E402
from watch_lock import acquire, release  # noqa: E402
from watch_state import WatchState  # noqa: E402


@pytest.mark.parametrize(("options", "interval"), [((), 5), (("--every", "12"), 12)])
def test_collector_interval(tmp_path, monkeypatch, options, interval):
    origin = "https://example.test"

    class QuietService:
        url = origin

        def send(self, method, path):
            assert (method, path) == ("GET", f"/api/v1/artifacts/{TARGET}/comments")
            return "[]"

    def after_poll(seconds):
        assert seconds == interval
        raise KeyboardInterrupt

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(watch, "client", lambda _: QuietService())
    monkeypatch.setattr(watch.time, "sleep", after_poll)
    assert watch.main(["collect", f"{origin}/a/{TARGET}", *options]) == 130


def test_collector_reader_crash_retry_and_scope_lock(tmp_path):
    fixture = {"comments": [], "status": 200, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            fixture["requests"].append(
                (self.path, self.headers.get("Authorization"), fixture["status"])
            )
            assert self.path == f"/api/v1/artifacts/{TARGET}/comments"
            body = json.dumps(fixture["comments"]).encode()
            self.send_response(fixture["status"])
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    link = f"{origin}/a/{TARGET}"
    env = dict(os.environ, AGENT_ARTIFACTS_URL=origin, AGENT_ARTIFACTS_KEY="fixture-key")
    if os.name == "nt":
        env.pop("XDG_STATE_HOME", None)
        env["LOCALAPPDATA"] = str(tmp_path)
    else:
        env["XDG_STATE_HOME"] = str(tmp_path)
    offline_env = {k: v for k, v in env.items() if not k.startswith("AGENT_ARTIFACTS_")}
    processes = []

    def start(*args):
        p = subprocess.Popen(  # noqa: S603
            [sys.executable, str(SCRIPT), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(p)
        return p

    def run(*args, environment=env):
        return subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT), *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def status():
        result = run("status", link, environment=offline_env)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def wait_for(predicate, timeout=18):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.15)
        pytest.fail(f"timed out: {status()} requests={fixture['requests']}")

    def reader(duration=1.5):
        p = start("follow", link)
        time.sleep(duration)
        p.terminate()
        out, err = p.communicate(timeout=5)
        assert not err
        return [json.loads(line) for line in out.splitlines()]

    def publish(n):
        fixture["comments"].append({"n": n, "agent": "waiting", "replies": [], "text": "private"})

    try:
        assert not status()["collector_active"]
        (lock_path,) = (tmp_path / "agent-artifacts/reliable-watch").glob("*.lock")
        with lock_path.open("r+b") as probe:
            assert acquire(probe.fileno(), shared=True)
            assert not status()["collector_active"], "two status probes are not a collector"
            collector = start("collect", link, "--every", "5")
            time.sleep(0.4)
            assert collector.poll() is None, "a status probe did not start a collector"
            assert fixture["requests"] == []
            release(probe.fileno())
        wait_for(lambda: status()["last_success"] is not None)
        assert status()["collector_active"]
        assert reader() == [] and collector.poll() is None
        duplicate = run("collect", link, "--every", "5")
        assert duplicate.returncode == 1 and "already running" in duplicate.stderr
        publish(1)
        wait_for(lambda: status()["pending"] == 1)
        first = reader(6)
        assert len(first) == 1 and collector.poll() is None
        publish(2)
        wait_for(lambda: status()["pending"] == 2)
        both = reader()
        assert len(both) == 2 and both[0] == first[0] and both[0]["id"] != both[1]["id"]
        assert run("ack", link, both[0]["id"]).returncode == 0
        fixture["status"] = 503
        before = len(fixture["requests"])
        wait_for(lambda: len(fixture["requests"]) >= before + 2, timeout=13)
        failed = status()
        assert failed["poll_health"] == "failed" and failed["pending"] == 1
        assert failed["failure"] == "service_unavailable" and failed["collector_active"]
        assert sum(code == 503 for _, _, code in fixture["requests"][before:]) >= 2
        fixture["status"] = 200
        publish(3)
        wait_for(lambda: status()["poll_health"] == "ok" and status()["pending"] == 2)
        collector.kill()
        collector.wait(timeout=5)
        wait_for(lambda: not status()["collector_active"])
        restarted = start("collect", link, "--every", "5")
        wait_for(lambda: status()["collector_active"])
        replay = reader()
        assert len(replay) == 2 and replay[0] == both[1]
        assert {e["event"]["comments"][0]["n"] for e in replay} == {2, 3}
        for event in replay:
            assert run("ack", link, event["id"]).returncode == 0
        restarted.kill()
        restarted.wait(timeout=5)
        restarted = start("collect", link, "--every", "5")
        wait_for(lambda: status()["collector_active"])
        assert reader() == []
        publish(4)
        wait_for(lambda: status()["pending"] == 1)
        fixture["status"] = 401
        wait_for(lambda: restarted.poll() is not None)
        assert restarted.returncode == 1
        assert "HTTP 401; collector stopped" in restarted.stderr.read()
        refused = status()
        assert not refused["collector_active"]
        assert refused["failure"] == "unauthorized" and refused["pending"] == 1
        assert all(auth == "Bearer fixture-key" for _, auth, _ in fixture["requests"])
        assert (
            b"private"
            not in (tmp_path / "agent-artifacts/reliable-watch/watches.sqlite3").read_bytes()
        )
        count = len(fixture["requests"])
        wrong = run("collect", f"http://127.0.0.1:{server.server_port + 1}/a/{TARGET}")
        assert wrong.returncode == 1 and len(fixture["requests"]) == count
        print(
            f"fixture proof: first={first[0]['id']} second={both[1]['id']} "
            f"third={replay[1]['id']}; repeated 503, recovery, kill/restart, ack, "
            "no replay, and duplicate collector refusal verified"
        )
    finally:
        for p in processes:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_timed_reader_exits_even_if_output_is_not_drained(tmp_path):
    origin = "https://example.test"
    link = f"{origin}/a/{TARGET}"
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path))
    with_state = WatchState(tmp_path / "agent-artifacts/reliable-watch", origin, "a", TARGET)
    try:
        # Enough valid output to fill a pipe even on systems with a large
        # default buffer. Do not drain stdout until the reader has exited.
        event = json.dumps({"event": "waiting", "comments": [{"n": n} for n in range(1000)]})
        with_state._commit(0, "{}", [event] * 500, None)
        pending = with_state.pending()
        command = [sys.executable, str(SCRIPT), "follow", link, "--timeout", "0.8"]
        with subprocess.Popen(  # noqa: S603
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as reader:
            try:
                assert reader.wait(timeout=5) == 0
                out, err = reader.communicate(timeout=5)
            finally:
                if reader.poll() is None:
                    reader.kill()
        assert b"deadline reached" in err
        assert len(out) < len(event) * len(pending)
        assert with_state.pending() == pending
        with_state.db.execute("BEGIN EXCLUSIVE")
        locked = subprocess.run(command, env=env, capture_output=True, timeout=5)  # noqa: S603
        assert locked.returncode == 0 and not locked.stdout
        with_state.db.rollback()
        attached = subprocess.run(command, env=env, capture_output=True, timeout=5)  # noqa: S603
        assert attached.returncode == 0
        assert json.loads(attached.stdout.splitlines()[0]) == pending[0]
    finally:
        with_state.close()
