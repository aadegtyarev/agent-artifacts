"""Recovery and isolation tests for the durable watch journal."""

import json
import os
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skill/agent-artifacts/scripts"
sys.path.insert(0, str(SCRIPTS))
import artifacts_cli  # noqa: E402
import watch_state as ws  # noqa: E402

TARGET = "a" * 22
OTHER = "b" * 22
ORIGIN = "https://example.test"


class Client:
    url = ORIGIN

    def send(self, method, path):
        return json.dumps([{"n": 1, "agent": "waiting", "replies": [], "text": "secret"}])


def journal(path, target=TARGET):
    return ws.WatchState(path, ORIGIN, "a", target)


def test_process_crash_delivery_failure_ack_and_reopen(tmp_path):
    code = """
import json, sys, time
from watch_state import WatchState
class Client:
    url = "https://example.test"
    def send(self, method, path):
        return json.dumps([{"n": 1, "agent": "waiting", "replies": [], "text": "private"}])
s = WatchState(sys.argv[1], "https://example.test", "a", "a" * 22)
s.poll(Client())
print(s.pending()[0]["id"], flush=True)
time.sleep(60)
"""
    env = dict(os.environ, PYTHONPATH=str(SCRIPTS))
    process = subprocess.Popen(  # noqa: S603 -- fixed interpreter and fixture code
        [sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        event_id = process.stdout.readline().strip()
        assert event_id
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    state = journal(tmp_path)
    assert state.pending() == [
        {"id": event_id, "event": {"event": "waiting", "comments": [{"n": 1}]}}
    ]

    def broken_transport(_event):
        raise OSError("fixture transport unavailable")

    try:
        broken_transport(state.pending()[0])
    except OSError:
        state.delivery("fixture-session", succeeded=False)
    assert state.status()["delivery_failure"] and state.status()["pending"] == 1
    state.close()
    state = journal(tmp_path)
    assert state.pending()[0]["id"] == event_id
    state.delivery("fixture-session", succeeded=True)
    assert state.ack(event_id)
    state.close()
    state = journal(tmp_path)
    assert state.pending() == []
    assert state.status()["recipient"] == "fixture-session"
    state.close()
    assert b"private" not in (tmp_path / "watches.sqlite3").read_bytes()


@pytest.mark.parametrize("kind", ["a", "p"])
def test_pending_review_notified_once_and_recovered_without_comments(tmp_path, kind):
    closure = "c" * 22
    scope = TARGET if kind == "a" else OTHER

    class ClosedReview(Client):
        ready = False

        def send(self, method, path):
            value = None if self.ready else closure
            assert method == "GET"
            if "/summary" in path:
                return json.dumps({"now": 0, "files": [
                    {"id": TARGET, "title": "Sensitive title", "waiting": 0, "accepted": 0, "review_closure": value},
                ]})
            return json.dumps({"comments": [], "review_closure": value, "ignored": "Sensitive body"})

    def open_watch():
        return ws.WatchState(tmp_path, ORIGIN, kind, scope)

    def expected():
        event = {"event": "review_closed", "artifact": TARGET, "closure": closure}
        return event | ({"project": OTHER} if kind == "p" else {})

    service = ClosedReview()
    if kind == "p":
        assert artifacts_cli.wait_project(service, scope, 5, 5, maybe_file=False) == expected()
    else:
        assert artifacts_cli.wait(service, scope, 5, 5) == expected()
    state = open_watch()
    # Simulate a checkpoint written by the older collector before upgrading.
    previous = '{"waiting":{},"resolved":[],"since":null,"accepted":{},"next_detail":null}'
    state._commit(0, previous, [], None)
    state.poll(service)
    pending, = state.pending()
    assert pending["event"] == expected()
    state.close()
    state = open_watch()
    state.poll(service)
    assert state.pending() == [pending]
    with pytest.raises(ValueError, match="invalid review event scope"):
        ws._event(expected() | {"comment": "text"}, kind, scope)
    state.ack(pending["id"])
    state.poll(service)
    assert state.pending() == []  # Delivery/ack does not write to the server.
    service.ready = True
    state.poll(service)
    assert state.pending() == []
    state.close()
    assert b"Sensitive" not in (tmp_path / "watches.sqlite3").read_bytes()


def test_failed_poll_does_not_advance_and_scope_isolation(tmp_path):
    state = journal(tmp_path)
    state.poll(Client())
    before = state.status()["last_success"]

    class Failing(Client):
        def send(self, method, path):
            raise OSError("secret credential")

    with pytest.raises(OSError):
        state.poll(Failing())
    assert state.status()["last_success"] == before
    assert state.status()["last_failure"] and state.status()["failure"] == "poll_failed"
    assert state.status()["poll_health"] == "failed"
    state.poll(Client())
    assert state.status()["poll_health"] == "ok"
    assert len(state.pending()) == 1
    other = journal(tmp_path, OTHER)
    assert not other.pending()
    other.poll(Client())
    assert len(other.pending()) == 1
    other.close()
    state.close()
    assert b"secret credential" not in (tmp_path / "watches.sqlite3").read_bytes()


def test_other_origin_cannot_read_or_advance_the_watch(tmp_path):
    class EquivalentClient(Client):
        url = "https://EXAMPLE.test:443/"

    class OtherOrigin(Client):
        url = "https://other.example.test"

        def send(self, method, path):
            pytest.fail("must reject a mismatched origin before sending any request")

    state = journal(tmp_path)
    state.poll(EquivalentClient())
    before = state.status(), state.pending()
    with pytest.raises(ValueError, match="watch origin does not match"):
        state.poll(OtherOrigin())
    assert (state.status(), state.pending()) == before
    state.poll(Client())
    assert state.pending() == before[1]
    state.close()


def test_equivalent_ipv6_literals_share_a_scope(tmp_path):
    short = "http://[::1]:18147"
    expanded = "http://[0:0:0:0:0:0:0:1]:18147/"

    class IPv6Client(Client):
        url = expanded

    first = ws.WatchState(tmp_path, short, "a", TARGET)
    first.poll(IPv6Client())
    second = ws.WatchState(tmp_path, expanded, "a", TARGET)
    assert first.key == second.key and first.pending() == second.pending()
    second.poll(IPv6Client())
    assert len(second.pending()) == 1
    first.close()
    second.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://[fe80::1%eth0]:18147",
        "https://[fe80::1%25eth0]:18147",
        "https://%65xample.test",
    ],
)
def test_escaped_authority_rejected_before_any_request(tmp_path, monkeypatch, url):
    client = artifacts_cli.Client(url, "not-used")

    def no_request(*args, **kwargs):
        pytest.fail("must not fetch an unsupported origin")

    monkeypatch.setattr(client, "send", no_request)
    unknown = tmp_path / "rejected"
    with pytest.raises(ValueError, match="unsupported watch origins"):
        ws.WatchState(unknown, url, "a", TARGET)
    assert not unknown.exists()
    state = journal(tmp_path)
    before = state.status()
    with pytest.raises(ValueError, match="unsupported watch origins"):
        state.poll(client)
    assert state.status() == before and state.pending() == []
    state.close()


def test_invalid_event_rejected_atomically(tmp_path):
    state = journal(tmp_path)
    with patch.object(
        ws._Subscription,
        "poll",
        return_value=[{"event": "waiting", "comments": [{"n": 1, "text": "secret"}]}],
    ):
        with pytest.raises(ValueError):
            state.poll(Client())
    assert state.pending() == []
    assert state.status()["last_success"] is None
    state.poll(Client())
    assert len(state.pending()) == 1
    state.close()


def test_competing_polls_use_compare_and_swap(tmp_path):
    state = journal(tmp_path)
    state.close()
    barrier = threading.Barrier(2)
    outcomes = []

    class RacingClient(Client):
        def send(self, method, path):
            barrier.wait(timeout=5)
            return super().send(method, path)

    def run():
        state = journal(tmp_path)
        try:
            state.poll(RacingClient())
            outcomes.append("committed")
        except ws.PollConflict:
            outcomes.append("conflict")
        finally:
            state.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) == ["committed", "conflict"]
    state = journal(tmp_path)
    assert len(state.pending()) == 1
    state.poll(Client())
    assert len(state.pending()) == 1
    state.close()


def test_full_mailbox_rolls_back_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "MAX_PENDING", 0)
    state = journal(tmp_path)
    with pytest.raises(ws.StateFull):
        state.poll(Client())
    assert state.status()["last_success"] is None
    monkeypatch.setattr(ws, "MAX_PENDING", 1)
    state.poll(Client())
    assert len(state.pending()) == 1
    state.close()


@pytest.mark.parametrize("throttled", [False, True])
def test_project_detail_failure_preserves_partial_results(tmp_path, monkeypatch, throttled):
    monkeypatch.setattr(artifacts_cli, "time", SimpleNamespace(sleep=lambda _: None))
    later = "c" * 22

    class ProjectClient(Client):
        def __init__(self, fail=False, empty=False):
            self.fail = fail
            self.empty = empty
            self.detail = []

        def send(self, method, path):
            if "/summary" in path:
                files = (
                    []
                    if self.empty
                    else [
                        {"id": OTHER, "waiting": 1, "accepted": 1},
                        {"id": later, "waiting": 1, "accepted": 0},
                    ]
                )
                return json.dumps({"now": 123, "files": files})
            file_id = path.split("/")[-2]
            self.detail.append(file_id)
            if self.fail and file_id == later:
                if throttled:
                    raise urllib.error.HTTPError(path, 429, "fixture rate limit", {}, None)
                raise OSError("fixture detail unavailable")
            return Client().send(method, path)

    state = ws.WatchState(tmp_path, ORIGIN, "p", TARGET)
    state.poll(ProjectClient(empty=True))
    state.poll(ProjectClient(fail=True))
    assert state.status()["poll_health"] == "degraded"
    assert state.status()["failure"] == (
        "detail_rate_limited" if throttled else "detail_unavailable"
    )
    partial = state.pending()
    assert [item["event"] for item in partial] == [
        {"event": "waiting", "project": TARGET, "files": [{"id": OTHER, "waiting": 1}]},
        {"event": "accepted", "project": TARGET, "files": [{"id": OTHER, "accepted": 1}]},
    ]
    state.close()
    state = ws.WatchState(tmp_path, ORIGIN, "p", TARGET)
    resumed = ProjectClient()
    state.poll(resumed)
    if throttled:
        assert resumed.detail[0] == later
    assert state.status()["poll_health"] == "ok"
    pending = state.pending()
    assert pending[:2] == partial and len(pending) == 3
    assert pending[-1]["event"]["files"] == [{"id": later, "waiting": 1}]
    state.close()
