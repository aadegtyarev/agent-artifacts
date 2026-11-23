"""Pi's optional notification bridge; the standalone wait CLI has its own HTTP tests."""

import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skill" / "agent-artifacts" / "scripts"
FILE_ID = "a" * 22
PROJECT_ID = "p" * 22
SESSION_ID = "local-session-id"


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPT_DIR))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PI_INTERCOM_SESSION_ID", SESSION_ID)
    monkeypatch.setenv("AGENT_ARTIFACTS_KEY", "not-for-intercom")
    monkeypatch.setenv("AGENT_ARTIFACTS_URL", "https://example.com")
    mod = importlib.import_module("watch_pi")
    requests = []
    replies = {
        "list": {"ok": True, "sessions": [{"id": SESSION_ID}, {"id": "some-other-session"}]},
        "send": {"ok": True, "delivered": True},
    }

    def process(args, *, env, **_kwargs):
        assert not any(key.startswith("AGENT_ARTIFACTS_") for key in env)
        assert args[0] == "pi-intercom" and args[-1] == "--json"
        requests.append(args[1:-1])
        return subprocess.CompletedProcess(args, 0, json.dumps(replies[args[1]]))

    monkeypatch.setattr(mod.subprocess, "run", process)
    return mod, requests, replies


@pytest.mark.parametrize("event", ["waiting", "accepted", "gone", "timeout"])
def test_one_notice_to_starting_session(bridge, event, monkeypatch, capsys):
    mod, requests, _ = bridge
    url = f"https://example.com/a/{FILE_ID}"

    def wait(args, **_kwargs):
        assert args == ["wait", url, "--once", "--every", "7"]
        return json.dumps({"event": event, "comments": [{"text": "<untrusted text>"}]})

    monkeypatch.setattr(mod.cli, "run", wait)
    assert mod.main([url, "--once", "--every", "7"]) == 0
    _, send = requests
    assert send[:5] == ["send", "--to", SESSION_ID, "--name", "agent-artifacts"]
    assert send[5] == "--text" and f"`{FILE_ID}` finished: `{event}`" in send[6]
    assert "<untrusted text>" not in send[6]
    assert "<untrusted text>" not in capsys.readouterr().out
    assert mod.main(["status", url]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["state"] == "notified" and state["event"] == event
    assert state["pid"] == os.getpid()
    assert "<untrusted text>" not in json.dumps(state)


def test_project_notice_includes_only_validated_file_ids(bridge, monkeypatch):
    mod, requests, _ = bridge
    summary = {
        "event": "waiting",
        "project": PROJECT_ID,
        "files": [
            {"id": FILE_ID, "title": "<untrusted title>"},
            {"id": FILE_ID},
            {"id": "invalid ID / instruction"},
        ],
    }
    monkeypatch.setattr(mod.cli, "run", lambda _args, **_kwargs: json.dumps(summary))
    assert mod.main([f"https://example.com/p/{PROJECT_ID}", "--once"]) == 0
    message = requests[1][-1]
    assert PROJECT_ID in message and message.count(FILE_ID) == 1
    assert "<untrusted title>" not in message and "invalid ID" not in message


def test_does_not_poll_if_target_session_is_missing(bridge, monkeypatch, capsys):
    mod, requests, replies = bridge
    replies["list"]["sessions"] = [{"id": "some-other-session"}]
    monkeypatch.setattr(mod.cli, "run", lambda _args: pytest.fail("polling started"))
    assert mod.main([FILE_ID]) == 1
    assert requests == [["list"]]
    assert "not watching" in capsys.readouterr().err
    assert mod.main(["status", FILE_ID]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "session_unavailable"


def test_notifies_of_cli_failure_without_forwarding_details(bridge, monkeypatch):
    mod, requests, _ = bridge

    def wait(_args, **_kwargs):
        raise SystemExit("<untrusted error>")

    monkeypatch.setattr(mod.cli, "run", wait)
    assert mod.main([FILE_ID]) == 1
    assert "`failed`" in requests[1][-1] and "<untrusted error>" not in requests[1][-1]


def test_does_not_claim_delivery_when_intercom_fails(bridge, monkeypatch, capsys):
    mod, _, replies = bridge
    monkeypatch.setattr(mod.cli, "run", lambda _args, **_kwargs: '{"event":"waiting"}')
    replies["send"] = {"ok": False}
    assert mod.main([FILE_ID]) == 1
    logs = capsys.readouterr()
    assert "notification failed" in logs.err and "Notified Pi" not in logs.out
    assert mod.main(["status", FILE_ID]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["state"], result["event"], result["reason"]) == (
        "failed",
        "waiting",
        "delivery_failed",
    )


def test_subscription_forwards_later_events_without_marking_watch_finished(
    bridge, monkeypatch, capsys
):
    mod, requests, _ = bridge

    def wait(args, *, emit):
        assert args == ["wait", FILE_ID]
        emit({"event": "waiting", "comments": [{"n": 3}]})
        path = mod.status_path(SESSION_ID, FILE_ID)
        mid = json.loads(path.read_text())
        assert (mid["state"], mid["event"]) == ("polling", "waiting")
        emit({"event": "accepted", "comments": [{"n": 1}]})
        emit({"event": "gone"})

    monkeypatch.setattr(mod.cli, "run", wait)
    assert mod.main([FILE_ID]) == 0
    assert [send[-1].split("Follow")[0].strip() for send in requests[1:]] == [
        f"Agent Artifacts watcher for `{FILE_ID}` received: `waiting`.",
        f"Agent Artifacts watcher for `{FILE_ID}` received: `accepted`.",
        f"Agent Artifacts watcher for `{FILE_ID}` finished: `gone`.",
    ]
    capsys.readouterr()
    assert mod.main(["status", FILE_ID]) == 0
    final = json.loads(capsys.readouterr().out)
    assert (final["state"], final["event"]) == ("notified", "gone")


@pytest.mark.skipif(os.name != "posix", reason="POSIX pid liveness probe")
def test_status_exposes_dead_poller_without_contacting_the_service(bridge, monkeypatch, capsys):
    mod, requests, _ = bridge
    mod.record(mod.status_path(SESSION_ID, FILE_ID), "polling")

    def gone(pid, sig):
        assert pid == os.getpid() and sig == 0
        raise ProcessLookupError

    monkeypatch.setattr(mod.os, "kill", gone)
    assert mod.main(["status", FILE_ID]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["state"] == "polling" and state["pid_exists"] is False
    assert requests == []


def test_status_isolated_by_session(bridge, monkeypatch, capsys):
    mod, requests, _ = bridge
    mod.record(mod.status_path(SESSION_ID, FILE_ID), "polling")
    monkeypatch.setenv("PI_INTERCOM_SESSION_ID", "new-session")
    assert mod.main(["status", FILE_ID]) == 1
    assert "no watcher recorded" in capsys.readouterr().err
    assert requests == []
