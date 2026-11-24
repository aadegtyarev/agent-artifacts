#!/usr/bin/env python3
"""Explicit local collector and reconnectable JSON-lines reader; not a service manager."""

import argparse
import fcntl
import hashlib
import http.client
import json
import math
import os
import re
import select
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

import artifacts_cli as cli
from watch_state import PollConflict, StateFull, WatchState, _origin, read_pending


def scope(link):
    parts = urllib.parse.urlsplit(link)
    if parts.query or parts.fragment or parts.username is not None or parts.password is not None:
        raise ValueError("invalid watch link")
    target, kind = cli._wait_target(link)
    if kind is None or parts.path != f"/{kind}/{target}":
        raise ValueError("expected full artifact or project URL")
    return _origin(f"{parts.scheme}://{parts.netloc}"), kind, target


def directory():
    return (
        Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        / "agent-artifacts"
        / "reliable-watch"
    )


def lock_file(state, role="collector"):
    suffix = {"collector": ".lock", "pi": ".pi-binding", "guard": ".pi-guard"}[role]
    name = hashlib.sha256(json.dumps(state.key).encode()).hexdigest() + suffix
    return os.open(directory() / name, os.O_CREAT | os.O_RDWR, 0o600)


def lock(state):
    fd = lock_file(state)
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                return fd
            except BlockingIOError:
                # A short-lived shared status probe is not a collector.
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    return None
                fcntl.flock(fd, fcntl.LOCK_UN)
                time.sleep(0.01)
    finally:
        if not acquired:
            os.close(fd)


def collector_active(state):
    fd = lock_file(state)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


def client(origin):
    in_env = {v: os.environ.get(v) for v in (cli.URL_VAR, cli.KEY_VAR) if os.environ.get(v)}
    if len(in_env) == 1:
        raise ValueError("incomplete client configuration")
    path = cli.env_file()
    source = in_env or (cli.parse_settings(path.read_text()) if path.is_file() else {})
    url, key = source.get(cli.URL_VAR), source.get(cli.KEY_VAR)
    if not url or not key or _origin(url) != origin:
        raise ValueError("client configuration does not match watch origin")
    return cli.Client(url, key)


def pi_active(state):
    guard = lock_file(state, "guard")
    fcntl.flock(guard, fcntl.LOCK_EX)
    fd = lock_file(state, "pi")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                data = json.loads(os.read(fd, 4096))
                os.kill(data["pid"], 0)
                return data["recipient"]
            except (OSError, ValueError, KeyError, TypeError):
                return None
        return None
    finally:
        os.close(fd)
        os.close(guard)


def pi_follow(state, session):
    from watch_pi import intercom
    from watch_state import _event

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        raise ValueError("invalid session")
    guard = lock_file(state, "guard")
    fd = lock_file(state, "pi")
    try:
        fcntl.flock(guard, fcntl.LOCK_EX)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("watch: Pi adapter already running", file=sys.stderr)
                return 1
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"pid": os.getpid(), "recipient": session}).encode())
        finally:
            fcntl.flock(guard, fcntl.LOCK_UN)
        seen = set()
        while True:
            try:
                sessions = intercom("list").get("sessions")
                if not isinstance(sessions, list) or not any(
                    isinstance(row, dict) and row.get("id") == session for row in sessions
                ):
                    raise ValueError("session unavailable")
            except (OSError, ValueError, subprocess.SubprocessError):
                state.delivery(session, succeeded=False)
                time.sleep(5)
                continue
            pending = state.pending()
            seen.intersection_update(event["id"] for event in pending)
            for item in pending:
                event_id = item["id"]
                if event_id in seen:
                    continue
                if not re.fullmatch(r"[0-9a-f]{32}", event_id):
                    raise ValueError("invalid event ID")
                event = json.loads(_event(item["event"], state.kind, state.target))
                try:
                    text = (
                        f"Agent Artifacts watch {state.kind}/{state.target} event {event_id}: "
                        f"{json.dumps(event, separators=(',', ':'))}. "
                        "Delivery is not acknowledgment. Follow the agent-artifacts skill."
                    )
                    result = intercom(
                        "send", "--to", session, "--name", "agent-artifacts", "--text", text
                    )
                    if result.get("delivered") is not True:
                        raise ValueError("not delivered")
                except (OSError, ValueError, subprocess.SubprocessError):
                    state.delivery(session, succeeded=False)
                    break
                state.delivery(session, succeeded=True)
                seen.add(event_id)
            time.sleep(5)
    finally:
        os.close(fd)
        os.close(guard)


def remaining(deadline):
    return max(0.0, deadline - time.monotonic())


def write_before(fd, line, deadline):
    pending = memoryview(line)
    while pending and remaining(deadline):
        try:
            pending = pending[os.write(fd, pending) :]
        except BlockingIOError:
            if not select.select([], [fd], [], remaining(deadline))[1]:
                break
    return not pending


def follow_deadline():
    # The tool's stderr can also be a pipe. A terminal notice must not stall exit.
    try:
        fd = sys.stderr.fileno()
        blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        try:
            os.write(fd, b"watch: follow deadline reached\n")
        finally:
            os.set_blocking(fd, blocking)
    except OSError:
        pass
    return 0


def follow(key, deadline):
    fd = None if deadline is None else sys.stdout.fileno()
    blocking = None if fd is None else os.get_blocking(fd)
    if fd is not None:
        os.set_blocking(fd, False)
    try:
        seen = set()
        while deadline is None or remaining(deadline):
            pending = read_pending(directory(), key)
            if pending is not None:
                seen.intersection_update(event["id"] for event in pending)
                for event in pending:
                    if event["id"] in seen:
                        continue
                    line = json.dumps(event)
                    if fd is None:
                        print(line, flush=True)
                    elif not write_before(fd, (line + "\n").encode(), deadline):
                        return follow_deadline()
                    seen.add(event["id"])
            time.sleep(1 if deadline is None else min(1, remaining(deadline)))
        return follow_deadline()
    except BrokenPipeError:
        return 0  # detach without acknowledging anything
    finally:
        if fd is not None:
            os.set_blocking(fd, blocking)


def recover(client, key):
    """Find interrupted work without assigning it or acknowledging notices."""
    _, kind, target = key
    if kind == "a":
        candidates = [target]
    else:
        project = json.loads(client.send("GET", f"/api/v1/projects/{target}"))
        rows = project.get("artifacts") if isinstance(project, dict) else None
        if not isinstance(rows, list):
            raise ValueError("invalid project files")
        candidates = []
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(file_id := row.get("id"), str)
                or not cli.ID_RE.fullmatch(file_id)
                or type(count := row.get("open_comments")) is not int
                or count < 0
            ):
                raise ValueError("invalid project file")
            if count:
                candidates.append(file_id)
    files = []
    for file_id in candidates:
        if kind == "p":
            time.sleep(cli._PROJECT_DETAIL_DELAY)
        try:
            rows = json.loads(client.send("GET", f"/api/v1/artifacts/{file_id}/comments"))
        except urllib.error.HTTPError as e:
            if kind == "p" and e.code == 404:
                continue  # removed after the project listing
            raise
        if not isinstance(rows, list):
            raise ValueError("invalid comments")
        working = []
        for row in rows:
            if not isinstance(row, dict) or type(n := row.get("n")) is not int or n < 1:
                raise ValueError("invalid comment number")
            if row.get("agent") == "working" and row.get("resolved") is not True:
                working.append({"n": n})
        if working:
            files.append({"id": file_id, "comments": working})
    return {"scope": {"kind": kind, "id": target}, "unfinished": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "status", "follow", "ack", "pi", "recover"):
        p = sub.add_parser(name)
        p.add_argument("link")
        if name == "collect":
            p.add_argument("--every", type=float, default=10)
        if name == "follow":
            p.add_argument("--timeout", type=float, default=0)
        if name == "pi":
            p.add_argument("--session", required=True)
        if name == "ack":
            p.add_argument("event_id")
    args = parser.parse_args(argv)
    try:
        origin, kind, target = scope(args.link)
        if args.command == "collect" and (not math.isfinite(args.every) or args.every < 5):
            raise ValueError("invalid poll interval")
        if args.command == "follow":
            if not math.isfinite(args.timeout) or args.timeout < 0:
                raise ValueError("invalid attach timeout")
            deadline = time.monotonic() + args.timeout if args.timeout else None
            return follow((origin, kind, target), deadline)
        if args.command == "recover":
            print(json.dumps(recover(client(origin), (origin, kind, target))))
            return 0
        c = client(origin) if args.command == "collect" else None
        state = WatchState(directory(), origin, kind, target)
        try:
            if args.command == "collect":
                fd = lock(state)
                if fd is None:
                    print("watch: collector already running", file=sys.stderr)
                    return 1
                try:
                    while True:
                        try:
                            state.poll(c)
                        except urllib.error.HTTPError as e:
                            if e.code != 429 and e.code < 500:
                                # WatchState has already recorded the safe reason.
                                print(f"watch: HTTP {e.code}; collector stopped", file=sys.stderr)
                                return 0 if e.code == 404 else 1
                        except (
                            OSError,
                            ValueError,
                            http.client.HTTPException,
                            PollConflict,
                            StateFull,
                        ):
                            pass  # status explains a failed or back-pressured poll
                        time.sleep(args.every)
                finally:
                    os.close(fd)
            elif args.command == "status":
                print(
                    json.dumps(
                        {
                            "origin": origin,
                            "scope": {"kind": kind, "id": target},
                            "collector_active": collector_active(state),
                            **state.status(),
                            "recipient": pi_active(state),
                        }
                    )
                )
            elif args.command == "ack":
                if not state.ack(args.event_id):
                    print("watch: event not pending", file=sys.stderr)
                    return 1
            elif args.command == "pi":
                return pi_follow(state, args.session)
        finally:
            state.close()
    except KeyboardInterrupt:
        return 130
    except urllib.error.HTTPError as e:
        print(f"watch: recovery failed (HTTP {e.code})", file=sys.stderr)
        return 1
    except (cli.Usage, ValueError, OSError, OverflowError):
        print("watch: invalid link, configuration, or local state", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
