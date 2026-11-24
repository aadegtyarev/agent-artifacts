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
SCRIPT = ROOT / "skill/agent-artifacts/scripts/watch.py"
TARGET = "a" * 22


@pytest.mark.skipif(os.name != "posix", reason="the local collector uses POSIX advisory locks")
def test_collector_reader_crash_retry_and_scope_lock(tmp_path):
    import fcntl

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
    env = dict(
        os.environ,
        XDG_STATE_HOME=str(tmp_path),
        AGENT_ARTIFACTS_URL=origin,
        AGENT_ARTIFACTS_KEY="fixture-key",
    )
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
        result = run("status", link, environment={"XDG_STATE_HOME": str(tmp_path)})
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
        with lock_path.open("rb") as probe:
            fcntl.flock(probe, fcntl.LOCK_SH)
            assert not status()["collector_active"], "two status probes are not a collector"
            collector = start("collect", link, "--every", "5")
            time.sleep(0.4)
            assert collector.poll() is None, "a status probe did not start a collector"
            assert fixture["requests"] == []
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
        assert not status()["collector_active"]
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
