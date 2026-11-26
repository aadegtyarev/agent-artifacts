#!/usr/bin/env python3
"""Explicit local collector and reconnectable JSON-lines reader; not a service manager."""

import argparse
import hashlib
import http.client
import json
import math
import os
import re
import select
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path

import artifacts_cli as cli
from watch_lock import acquire, release
from watch_state import PollConflict, StateFull, WatchState, _origin, read_pending

_PI_MAILBOX_INTERVAL = 1
_PI_RETRY_INTERVAL = 5


def scope(link):
    parts = urllib.parse.urlsplit(link)
    if parts.query or parts.fragment or parts.username is not None or parts.password is not None:
        raise ValueError("invalid watch link")
    target, kind = cli._wait_target(link)
    if kind is None or parts.path != f"/{kind}/{target}":
        raise ValueError("expected full artifact or project URL")
    return _origin(f"{parts.scheme}://{parts.netloc}"), kind, target


def directory():
    if os.name == "nt":
        default = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    else:
        default = Path.home() / ".local/state"
    return Path(os.environ.get("XDG_STATE_HOME") or default) / "agent-artifacts/reliable-watch"


def lock_file(state, role="collector"):
    suffix = {"collector": ".lock", "pi": ".pi-binding", "guard": ".pi-guard"}[role]
    name = hashlib.sha256(json.dumps(state.key).encode()).hexdigest() + suffix
    return os.open(directory() / name, os.O_CREAT | os.O_RDWR, 0o600)


def lock(state):
    fd = lock_file(state)
    acquired = False
    try:
        while True:
            if acquire(fd):
                acquired = True
                return fd
            # A short-lived shared status probe is not a collector.
            if not acquire(fd, shared=True):
                return None
            release(fd)
            time.sleep(0.01)
    finally:
        if not acquired:
            os.close(fd)


def collector_active(state):
    fd = lock_file(state)
    try:
        return not acquire(fd, shared=True)
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
    try:
        acquire(guard, wait=True)
        fd = lock_file(state, "pi")
        try:
            if not acquire(fd):
                # An OS-owned binding lock is stronger evidence than a PID
                # lookup (and os.kill(pid, 0) can terminate a Windows process).
                try:
                    recipient = json.loads(os.read(fd, 4096))["recipient"]
                    return recipient if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", recipient) else None
                except (OSError, ValueError, KeyError, TypeError):
                    return None
            return None
        finally:
            os.close(fd)
    finally:
        os.close(guard)


def pi_follow(state, session):
    from watch_pi import intercom
    from watch_state import _event

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        raise ValueError("invalid session")
    guard = lock_file(state, "guard")
    fd = lock_file(state, "pi")
    try:
        acquire(guard, wait=True)
        try:
            if not acquire(fd):
                print("watch: Pi adapter already running", file=sys.stderr)
                return 1
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"pid": os.getpid(), "recipient": session}).encode())
        finally:
            release(guard)
        seen = set()
        check_session_at = 0.0
        while True:
            pending = state.pending()
            seen.intersection_update(event["id"] for event in pending)
            unsent = [item for item in pending if item["id"] not in seen]
            if unsent or time.monotonic() >= check_session_at:
                try:
                    sessions = intercom("list").get("sessions")
                    if not isinstance(sessions, list) or not any(
                        isinstance(row, dict) and row.get("id") == session for row in sessions
                    ):
                        raise ValueError("session unavailable")
                except (OSError, ValueError, subprocess.SubprocessError):
                    state.delivery(session, succeeded=False)
                    time.sleep(_PI_RETRY_INTERVAL)
                    continue
                check_session_at = time.monotonic() + _PI_RETRY_INTERVAL
            delay = _PI_MAILBOX_INTERVAL
            for item in unsent:
                event_id = item["id"]
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
                    delay = _PI_RETRY_INTERVAL
                    break
                state.delivery(session, succeeded=True)
                seen.add(event_id)
            time.sleep(delay)
    finally:
        os.close(fd)
        os.close(guard)


def remaining(deadline):
    return max(0.0, deadline - time.monotonic())


def write_before_windows(fd, line, deadline):
    # Windows select handles sockets, not redirected stdout. A timed CLI
    # attach can exit with a blocked raw writer; it does not hold Python's
    # stdout stream lock or change pipe modes visible to other processes.
    done = threading.Event()
    error = []

    def write():
        try:
            pending = memoryview(line)
            while pending:
                pending = pending[os.write(fd, pending) :]
        except OSError as e:
            error.append(e)
        finally:
            done.set()

    threading.Thread(target=write, daemon=True).start()
    if not done.wait(remaining(deadline)):
        return False
    if error:
        raise error[0]
    return True


def write_before(fd, line, deadline):
    if os.name == "nt":
        return write_before_windows(fd, line, deadline)
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
        if os.name == "nt":
            write_before_windows(fd, b"watch: follow deadline reached\n", time.monotonic() + 0.03)
            return 0
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
    nonblocking = fd is not None and os.name != "nt"
    if nonblocking:
        blocking = os.get_blocking(fd)
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
        if nonblocking:
            os.set_blocking(fd, blocking)


def recover(client, key):
    """Find interrupted work without assigning it or acknowledging notices."""
    _, kind, target = key
    closures, notes = {}, {}
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
            if (closure := cli._review_closure(row.get("review_closure"))) is not None:
                closures[file_id] = closure
            if (submission := cli._review_closure(row.get("review_notes"))) is not None:
                notes[file_id] = submission
            if count:
                candidates.append(file_id)
    files = []
    for file_id in candidates:
        if kind == "p":
            time.sleep(cli._PROJECT_DETAIL_DELAY)
        try:
            data = json.loads(client.send("GET", f"/api/v1/artifacts/{file_id}/comments?watch=1"))
        except urllib.error.HTTPError as e:
            if kind == "p" and e.code == 404:
                closures.pop(file_id, None)
                notes.pop(file_id, None)
                continue  # removed after the project listing
            raise
        rows, closure, submission = cli._read_watch_comments(data)
        for pending, record in ((closures, closure), (notes, submission)):
            if record is not None:
                pending[file_id] = record
            else:
                pending.pop(file_id, None)
        working = []
        for row in rows:
            if not isinstance(row, dict) or type(n := row.get("n")) is not int or n < 1:
                raise ValueError("invalid comment number")
            if row.get("agent") == "working" and row.get("resolved") is not True:
                working.append({"n": n})
        if working:
            files.append({"id": file_id, "comments": working})
    result = {"scope": {"kind": kind, "id": target}, "unfinished": files}
    if notes:
        result["pending_notes"] = [{"id": fid, "submission": submission} for fid, submission in notes.items()]
    if closures:
        result["pending_reports"] = [{"id": fid, "closure": closure} for fid, closure in closures.items()]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "status", "follow", "ack", "pi", "recover"):
        p = sub.add_parser(name)
        p.add_argument("link")
        if name == "collect":
            p.add_argument(
                "--every",
                type=float,
                default=cli.WATCH_EVERY,
                help="seconds between polls (default: %(default)s, minimum: 5)",
            )
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
