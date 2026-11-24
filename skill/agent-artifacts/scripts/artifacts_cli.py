#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""artifacts_cli: command line for agent-artifacts. Python standard library only.

Run it with `uv run artifacts_cli.py <command>` or `python artifacts_cli.py <command>`.

The service URL and key come together from AGENT_ARTIFACTS_URL and
AGENT_ARTIFACTS_KEY if both are set (one alone is an error), otherwise from
the env file that the setup command writes:
%APPDATA%\\agent-artifacts\\env on Windows, else
$XDG_CONFIG_HOME/agent-artifacts/env (~/.config by default).
AGENT_ARTIFACTS_ENV points to another file. The file is written with mode
600; on Windows, where that has no effect, it is as private as the user's
%APPDATA% folder.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn

# The date of the last change to this script or the skill; bump it with
# every such change. `version` prints it.
VERSION = "2026-11-23"

USAGE = """usage: artifacts_cli.py <command> [args]

  setup                     read AGENT_ARTIFACTS_URL=... and AGENT_ARTIFACTS_KEY=...
                            lines from stdin and save them for later calls;
                            a different key already there is copied to env.bak-*
  upload FILE [NAME] [--title TEXT] [--project ID] [--section TEXT]
                            upload a file, print the artifact JSON (url, id,
                            expires_at); pages show TEXT, else the name; in
                            project ID (kept until deleted), under a section
  replace ID FILE [NAME] [--title TEXT] [--project ID] [--section TEXT]
                            replace the file behind an existing link; title,
                            project and section stay unless given
  set ID [--title TEXT] [--project ID|none] [--section TEXT|none]
                            change a file's details without uploading it
                            again; none takes it out of its project (a new
                            lifetime starts) or clears the section
  project-new TITLE         a new project; prints its JSON (id, url)
  projects                  your projects, with files and used_bytes
  project ID                one project with its files in page order
  project-rename ID TITLE   a project's new title
  project-delete ID         delete a project with every file in it
  list                      your live files, with comments and open_comments counts
  delete ID                 delete one now
  comments ID               the comments on a file, oldest first
  take ID                   take the comments waiting for you ("agent": "waiting"):
                            they become "working"; prints them
  comment ID TEXT [--anchor JSON]
  reply ID N TEXT [--wrote|--asked]
                            answer in comment N's chat: done (default), done
                            with a remark or question (--wrote), or not done
                            until the person answers (--asked); the option
                            goes after TEXT or before ID
  wait ID [--once] [--every S] [--timeout S]
                            subscribe to a file or project (including new files)
                            and flush one JSON line per waiting/accepted event;
                            only IDs, comment numbers or counts, no comment text.
                            Poll every S seconds (default 10, minimum 5).
                            Stop on gone, Ctrl+C, or --timeout (default 0: none).
                            --once: return the first event with the full legacy
                            payload and exit (default timeout: 6 hours).
  resolve ID N              mark comment N done
  reopen ID N               mark comment N open again
  delete-comment ID N       delete comment N; the other numbers stay
  drop-drafts ID            delete every unpublished draft on a file, unread
  me                        your name, space used and quota, admin or not,
                            and "limits": file lifetime, sizes, types
  new-key                   replace your key (after a leak) and save the new one
  version                   this skill's version

admin keys only:
  users                     people with their space and last upload
  stats                     totals and free disk
  settings                  the service settings
  settings ttl-hours N      keep new uploads N hours
  settings user-quota-mb N  everyone's space, unless set per person
  settings max-total-mb N   all files together
  invite NAME [DAYS]        a one-time link that gives NAME a key (7 days by default)
  invites                   open invites
  cancel-invite ID          cancel one
  quota NAME MB|null        NAME's own quota, or back to the common one
  remove NAME               revoke NAME's key and delete their files

An ID is the 22 characters the service gave (letters, digits, - and _) or
the link itself: .../a/ID for a file, .../p/ID for a project.

Answers are JSON as the service sends them: sizes in bytes, times in seconds.
A wrong command or argument exits 2; an error from the service exits 1.
"""

URL_VAR, KEY_VAR = "AGENT_ARTIFACTS_URL", "AGENT_ARTIFACTS_KEY"
MB, HOUR, DAY = 1024 * 1024, 3600, 86400
# settings NAME -> (API field, unit it is given in)
SETTINGS = {
    "ttl-hours": ("ttl_seconds", HOUR),
    "user-quota-mb": ("user_quota_bytes", MB),
    "max-total-mb": ("max_total_bytes", MB),
}


ID_RE = re.compile(r"[A-Za-z0-9_-]{22}")


class Usage(Exception):
    pass


def _id(value: str, kind: str = "a") -> str:
    """A file's (kind "a") or project's ("p") id, bare or from its link.
    Anything else would land in the URL path: a link 404s, which wait takes
    for a deleted file, and "ID/drafts" reaches another call."""
    bare = value
    parts = urllib.parse.urlsplit(value)
    if parts.scheme in ("http", "https"):
        m = re.fullmatch(rf"(?:/.*)?/{kind}/([^/]*)/?", parts.path)
        bare = m.group(1) if m else ""
    if not ID_RE.fullmatch(bare):
        what = "file" if kind == "a" else "project"
        raise Usage(
            f"not a {what} id: {value}; an id is 22 letters, digits, - or _,"
            f" or give the link .../{kind}/ID"
        )
    return bare


def _wait_target(value: str) -> tuple[str, str | None]:
    """The id ``wait`` watches and its kind: "p" or "a" from a link, None
    for a bare id."""
    if urllib.parse.urlsplit(value).scheme not in ("http", "https"):
        return _id(value), None
    try:
        return _id(value, "p"), "p"
    except Usage:
        return _id(value), "a"


def env_file() -> Path:
    if os.environ.get("AGENT_ARTIFACTS_ENV"):
        return Path(os.environ["AGENT_ARTIFACTS_ENV"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "agent-artifacts" / "env"


def parse_settings(text: str) -> dict[str, str]:
    """KEY=VALUE lines, as on the invite page; 'export ' and quotes allowed."""
    found = {}
    for line in text.splitlines():
        line = line.strip().removeprefix("export ").strip()
        name, sep, value = line.partition("=")
        if sep and name in (URL_VAR, KEY_VAR):
            found[name] = value.strip().strip("'\"")
    return found


def write_settings(path: Path, url: str, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"{URL_VAR}={url}\n{KEY_VAR}={key}\n")
    os.replace(tmp, path)


def _check_writable(path: Path) -> None:
    """Create and remove a file next to the env file, as write_settings
    would, so a read-only folder stops new-key before the key changes."""
    # Its own name: not the .tmp a setup running at the same time writes.
    probe = path.with_name(f"{path.name}.probe-{os.getpid()}")
    try:
        os.close(os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        os.unlink(probe)
        os.close(os.open(path, os.O_WRONLY | os.O_APPEND))
    except OSError as e:
        sys.exit(f"artifacts_cli: cannot write {path} ({e}); the key is not changed")


def back_up(path: Path) -> Path:
    """Copy the env file aside before setup overwrites a different key."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1, 100):
        backup = path.with_name(f"{path.name}.bak-{stamp}" + (f"-{n}" if n > 1 else ""))
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            continue
    else:
        raise Usage(f"too many backups of {path} this second")
    with os.fdopen(fd, "wb") as f:
        f.write(path.read_bytes())
    return backup


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect means a wrong URL (http:// to https://, say). Following it
    would turn an upload into a GET and send the key to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, PLR0913
        raise urllib.error.HTTPError(
            req.full_url, code, f"redirected to {newurl}; fix {URL_VAR}", headers, fp
        )


_opener = urllib.request.build_opener(_NoRedirect)


class Client:
    def __init__(self, url: str, key: str) -> None:
        parts = urllib.parse.urlsplit(url)
        # Plain http:// would send the key in the clear before any redirect
        # to https:// could be refused; only a local test server may use it.
        if not (parts.scheme == "https" or parts.scheme == "http" and _is_local(parts.hostname)):
            raise Usage(f"{URL_VAR} must start with https:// (http:// only for a local server)")
        self.url = url.rstrip("/")
        self.key = key

    def send(self, method: str, path: str, body: bytes | None = None, ctype: str = "") -> str:
        """The answer's body; raises HTTPError or OSError."""
        req = urllib.request.Request(self.url + path, data=body, method=method)  # noqa: S310 (checked above)
        req.add_header("Authorization", f"Bearer {self.key}")
        if ctype:
            req.add_header("Content-Type", ctype)
        with _opener.open(req, timeout=120) as r:
            return r.read().decode()

    def call(self, method: str, path: str, body: bytes | None = None, ctype: str = "") -> str:
        """The answer's body; an HTTP error prints it and exits with 1."""
        try:
            return self.send(method, path, body, ctype)
        except urllib.error.HTTPError as e:
            _fail(e)
        except OSError as e:
            sys.exit(f"artifacts_cli: cannot reach {self.url}: {e}")

    def json(self, method: str, path: str, data: object) -> str:
        return self.call(method, path, json.dumps(data).encode(), "application/json")

    def upload(self, method: str, path: str, file: str, fields: dict[str, str | None]) -> str:
        p = Path(file)
        if not p.is_file():
            raise Usage(f"no such file: {file}")
        boundary = secrets.token_hex(16)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{_quote(p.name)}"\r\nContent-Type: {ctype}\r\n\r\n'.encode(),
            p.read_bytes(),
            b"\r\n",
        ]
        for field, value in fields.items():
            if value is not None:
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'
                    f"{value}\r\n".encode()
                )
        parts.append(f"--{boundary}--\r\n".encode())
        return self.call(method, path, b"".join(parts), f"multipart/form-data; boundary={boundary}")


def _fail(e: urllib.error.HTTPError) -> NoReturn:
    body = e.read().decode(errors="replace") if e.fp else ""
    # JSON errors name their code; anything else (a proxy's 429 or 502
    # page) gets the HTTP status instead.
    print(body if body.startswith("{") else f"HTTP {e.code}: {e.reason}", file=sys.stderr)
    sys.exit(1)


def _watch(c: Client, path, every: float, timeout: float, check) -> dict:  # noqa: ANN001
    """Poll ``path()`` until ``check`` finds news in an answer and return
    it; {"event": "gone"} on a 404, {"event": "timeout"} when the time is
    up. ``check`` raises ValueError on an odd answer: polled again. Silent
    meanwhile, so it can run in the background."""
    end = time.monotonic() + timeout if timeout else None
    while True:
        try:
            if event := check(json.loads(c.send("GET", path()))):
                return event
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"event": "gone"}
            if e.code != 429 and e.code < 500:
                _fail(e)  # a wrong or revoked key will not get better
        except (OSError, ValueError, http.client.HTTPException):
            pass  # the network, a restart, a proxy page, an odd answer: poll again
        left = end - time.monotonic() if end is not None else every
        if left <= 0:
            return {"event": "timeout"}
        time.sleep(min(every, left))


def wait(c: Client, artifact_id: str, every: float, timeout: float) -> dict:
    """News on one file: comments waiting for the agent, comments resolved
    since the first poll, the file gone, or the time up."""
    before = None  # the comments resolved at the first poll do not count

    def check(comments: object) -> dict | None:
        nonlocal before
        if not isinstance(comments, list) or not all(isinstance(x, dict) for x in comments):
            raise ValueError("not a list of comments")
        waiting = [x for x in comments if x.get("agent") == "waiting" and not x.get("resolved")]
        if waiting:
            return {"event": "waiting", "comments": waiting}
        if before is None:
            before = {x.get("n") for x in comments if x.get("resolved")}
        accepted = [x for x in comments if x.get("resolved") and x.get("n") not in before]
        return {"event": "accepted", "comments": accepted} if accepted else None

    path = f"/api/v1/artifacts/{artifact_id}/comments"
    return _watch(c, lambda: path, every, timeout, check)


def wait_project(
    c: Client, project_id: str, every: float, timeout: float, *, maybe_file: bool
) -> dict | None:
    """The same for every file of a project, files added later too, in one
    request per poll: the files with comments waiting for the agent, or
    with comments resolved since the first poll (by the server's clock), the
    project gone, or the time up. None if ``maybe_file`` and the first
    answer is a 404: no such project, so the id may be a file's."""
    since = None
    seen = False

    def check(summary: object) -> dict | None:
        nonlocal since, seen
        files = summary.get("files") if isinstance(summary, dict) else None
        if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
            raise ValueError("not a project summary")
        seen = True
        if since is None:
            now = summary.get("now")
            if not isinstance(now, (int, float)) or isinstance(now, bool):
                raise ValueError("no time in the summary")
            since = float(now)
        for event in ("waiting", "accepted"):
            news = [
                {"id": f.get("id"), "title": f.get("title"), event: f[event]}
                for f in files
                if isinstance(f.get(event), int) and f[event] > 0
            ]
            if news:
                return {"event": event, "project": project_id, "files": news}
        return None

    def path() -> str:
        query = "" if since is None else f"?since={since!r}"
        return f"/api/v1/projects/{project_id}/summary{query}"

    event = _watch(c, path, every, timeout, check)
    return None if maybe_file and not seen and event["event"] == "gone" else event


def _watch_file(c: Client, artifact_id: str) -> tuple[dict[int, int], set[int]]:
    """Each waiting number and its reply count, and resolved numbers."""
    rows = json.loads(c.send("GET", f"/api/v1/artifacts/{artifact_id}/comments"))
    if not isinstance(rows, list):
        raise ValueError("not a comments list")
    waiting, resolved = {}, set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("n")) is not int:
            raise ValueError("no comment number")
        n = row["n"]
        if row.get("resolved") is True:
            resolved.add(n)
        elif row.get("agent") == "waiting":
            replies = row.get("replies")
            if not isinstance(replies, list):
                raise ValueError("no reply list")
            waiting[n] = len(replies)
    return waiting, resolved


def _new_waiting(before: dict[int, int], now: dict[int, int]) -> list[int]:
    return [n for n, reply_count in now.items() if before.get(n) != reply_count]


# The shipped proxy allows 2 API requests/s per IP, with a burst of 10.
# Keep each detail batch below the burst and space requests within it.
_PROJECT_DETAIL_COUNT = 6
_PROJECT_DETAIL_DELAY = 0.6


class _Subscription:
    def __init__(self, client: Client, target: str, kind: str | None) -> None:
        self.client, self.target, self.kind = client, target, kind
        self.detail_failure: str | None = None
        self.waiting: dict[str, dict[int, int]] = {}
        self.resolved: set[int] | None = None
        self.since: float | None = None
        self.accepted: dict[str, int] = {}
        self.next_detail: str | None = None

    def poll(self, deadline: float | None = None) -> list[dict]:
        self.detail_failure = None
        if self.kind != "a":
            # Only a bare ID may fall back to a file; not an explicit or
            # previously identified project that just disappeared.
            try:
                data = self.project_updates(deadline)
            except urllib.error.HTTPError as e:
                if e.code != 404 or self.kind is not None:
                    raise
                if deadline is not None and time.monotonic() >= deadline:
                    return []
            else:
                self.kind = "p"
                return data
        self.kind = "a"
        waiting, resolved = _watch_file(self.client, self.target)
        news = _new_waiting(self.waiting.get(self.target, {}), waiting)
        closed = sorted(resolved - self.resolved) if self.resolved is not None else []
        self.waiting, self.resolved = {self.target: waiting}, resolved
        return [
            {"event": event, "comments": [{"n": n} for n in numbers]}
            for event, numbers in (("waiting", news), ("accepted", closed))
            if numbers
        ]

    def project_updates(self, deadline: float | None) -> list[dict]:
        # Keep the first server timestamp as the baseline. Advancing to the
        # response's `now` can report the same concurrent approval twice.
        suffix = "" if self.since is None else f"?since={self.since!r}"
        path = f"/api/v1/projects/{self.target}/summary{suffix}"
        summary = json.loads(self.client.send("GET", path))
        if not isinstance(summary, dict) or not isinstance(summary.get("files"), list):
            raise ValueError("not a project summary")
        now = summary.get("now")
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("no server clock")
        pending, accepted = [], self.accepted.copy()
        news = {"waiting": [], "accepted": []}
        for row in summary["files"]:
            if not isinstance(row, dict) or not isinstance(fid := row.get("id"), str):
                raise ValueError("no file ID")
            if not ID_RE.fullmatch(fid) or any(
                type(row.get(k)) is not int or row[k] < 0 for k in news
            ):
                raise ValueError("bad project file summary")
            if row["waiting"]:
                pending.append(fid)
            if self.since is not None:
                additional = row["accepted"] - accepted.get(fid, 0)
                if additional > 0:
                    news["accepted"].append({"id": fid, "accepted": additional})
                # Keep positive counts for files that leave and rejoin.
                if row["accepted"]:
                    accepted[fid] = row["accepted"]
                else:
                    accepted.pop(fid, None)
        if self.since is None:
            self.since = float(now)
        self.accepted = accepted
        news["waiting"] = self.project_waiting(pending, deadline)
        return [
            {"event": event, "project": self.target, "files": files}
            for event, files in news.items()
            if files
        ]

    def project_waiting(self, pending: list[str], deadline: float | None) -> list[dict]:
        # A count alone misses different threads at the same queue size.
        # Keep snapshots we cannot refresh in this poll and rotate the batch.
        waiting = {fid: self.waiting[fid] for fid in pending if fid in self.waiting}
        if self.next_detail in pending:
            start = pending.index(self.next_detail)
            pending = pending[start:] + pending[:start]
        self.next_detail = None
        news = []
        for index, fid in enumerate(pending):
            if index == _PROJECT_DETAIL_COUNT or (
                deadline is not None and time.monotonic() + _PROJECT_DETAIL_DELAY >= deadline
            ):
                self.next_detail = fid
                break
            time.sleep(_PROJECT_DETAIL_DELAY)
            try:
                snapshot, _ = _watch_file(self.client, fid)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    waiting.pop(fid, None)
                elif e.code == 429:
                    # Return partial updates and retry from here next poll.
                    self.detail_failure = "detail_rate_limited"
                    self.next_detail = fid
                    break
                elif e.code < 500:
                    raise
                else:
                    self.detail_failure = "detail_unavailable"
                continue
            except (OSError, ValueError, http.client.HTTPException):
                self.detail_failure = "detail_unavailable"
                continue
            if fresh := _new_waiting(waiting.get(fid, {}), snapshot):
                news.append({"id": fid, "waiting": len(fresh)})
            waiting[fid] = snapshot
        self.waiting = waiting
        return news


def subscribe(
    c: Client, target: str, kind: str | None, every: float, timeout: float
) -> Iterator[dict]:
    state = _Subscription(c, target, kind)
    end = time.monotonic() + timeout if timeout else None
    while end is None or time.monotonic() < end:
        try:
            events = state.poll(end)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                yield {"event": "gone"}
                return
            if e.code != 429 and e.code < 500:
                # Don't inject an untrusted server error body into a monitor.
                sys.exit(f"artifacts wait: HTTP {e.code}")
        except (OSError, ValueError, http.client.HTTPException):
            pass
        else:
            yield from events
        left = end - time.monotonic() if end is not None else every
        if left <= 0:
            break
        time.sleep(min(every, left))
    yield {"event": "timeout"}


def event_line(event: dict) -> None:
    print(json.dumps(event), flush=True)


def _is_local(host: str | None) -> bool:
    """The same rule the server uses for a plain-HTTP public URL."""
    if not host:
        return False
    if host == "localhost" or host.endswith((".local", ".lan", ".localhost")):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def _number(n: str) -> str:
    if not n.isdecimal() or not n.isascii():
        raise Usage(f"not a comment number: {n}")
    return n


def _option(args: list[str], option: str) -> tuple[str | None, list[str]]:
    """The value after ``option`` and the arguments without the two."""
    if option not in args:
        return None, args
    i = args.index(option)
    if i + 1 == len(args):
        raise Usage(f"{option} needs a value")
    return args[i + 1], args[:i] + args[i + 2 :]


def _quote(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', "%22").replace("\r", "").replace("\n", "")


def run(argv: list[str], *, emit: Callable[[dict], None] = event_line) -> str | None:
    if not argv:
        raise Usage(USAGE)
    if argv[0] in ("help", "-h", "--help"):
        return USAGE.rstrip()
    cmd, args = argv[0], argv[1:]
    if cmd == "watch":
        import watch

        code = watch.main(args)
        if code:
            raise SystemExit(code)
        return None
    path = env_file()

    if cmd == "version":
        return VERSION
    if cmd == "setup":
        found = parse_settings(sys.stdin.read())
        if set(found) != {URL_VAR, KEY_VAR} or not all(found.values()):
            raise Usage(f"expected {URL_VAR}=... and {KEY_VAR}=... on stdin")
        # A wrong key or URL stops here, before anything is saved.
        Client(found[URL_VAR], found[KEY_VAR]).call("GET", "/api/v1/me")
        old = parse_settings(path.read_text()) if path.is_file() else {}
        backup = back_up(path) if old and old != found else None
        write_settings(path, found[URL_VAR], found[KEY_VAR])
        out = f"saved to {path}" + (f"; the previous one is kept in {backup}" if backup else "")
        if set_in_env := [v for v in (URL_VAR, KEY_VAR) if os.environ.get(v)]:
            names = " and ".join(set_in_env)
            out += f"\nbut {names} in the environment win over the file: unset them to use it"
        return out

    # URL and key from one place: a URL alone from a project's environment
    # with the saved key would send that key to another host.
    in_env = {v: os.environ.get(v) for v in (URL_VAR, KEY_VAR) if os.environ.get(v)}
    if len(in_env) == 1:
        (has,) = in_env
        missing = KEY_VAR if has == URL_VAR else URL_VAR
        raise Usage(
            f"{has} is set but {missing} is not: set both, or unset it to use the saved ones"
        )
    from_env = bool(in_env)
    source = in_env or (parse_settings(path.read_text()) if path.is_file() else {})
    url, key = source.get(URL_VAR), source.get(KEY_VAR)
    if not url or not key:
        raise Usage(f"no key: run 'artifacts_cli.py setup' or set {URL_VAR} and {KEY_VAR}")
    c = Client(url, key)
    api, admin = "/api/v1/artifacts", "/api/v1/admin"

    def need(n: int, how: str, at_most: int | None = None) -> None:
        if len(args) < n or len(args) > (at_most if at_most is not None else n):
            raise Usage(f"usage: artifacts_cli.py {cmd} {how}")

    details: dict[str, str | None] = {}
    if cmd in ("upload", "replace", "set"):
        for field in ("title", "project", "section"):
            value, args = _option(args, f"--{field}")
            if value is not None:
                # "none" clears a field, which only `set` can do.
                if value == "none" and (cmd != "set" or field == "title"):
                    raise Usage(f"{cmd}: --{field} none only clears a project or section, with set")
                if field == "project" and value != "none":
                    value = _id(value, "p")
                details[field] = value
    opts = "[--title TEXT] [--project ID] [--section TEXT]"
    projects = "/api/v1/projects"
    match cmd:
        case "upload":
            need(1, f"FILE [NAME] {opts}", 2)
            name = args[1] if len(args) > 1 else None
            return c.upload("POST", api, args[0], {"name": name, **details})
        case "replace":
            need(2, f"ID FILE [NAME] {opts}", 3)
            name = args[2] if len(args) > 2 else None
            return c.upload("PUT", f"{api}/{_id(args[0])}", args[1], {"name": name, **details})
        case "set":
            how = "ID [--title TEXT] [--project ID|none] [--section TEXT|none]"
            need(1, how)
            if not details:
                raise Usage(f"usage: artifacts_cli.py set {how}")
            # Only here can a project or a section be cleared.
            for field in ("project", "section"):
                if details.get(field) == "none":
                    details[field] = None
            return c.json("PATCH", f"{api}/{_id(args[0])}", details)
        case "project-new":
            need(1, "TITLE")
            return c.json("POST", projects, {"title": args[0]})
        case "projects":
            return c.call("GET", projects)
        case "project":
            need(1, "ID")
            return c.call("GET", f"{projects}/{_id(args[0], 'p')}")
        case "project-rename":
            need(2, "ID TITLE")
            return c.json("PATCH", f"{projects}/{_id(args[0], 'p')}", {"title": args[1]})
        case "project-delete":
            need(1, "ID")
            c.call("DELETE", f"{projects}/{_id(args[0], 'p')}")
            return "deleted"
        case "list":
            return c.call("GET", api)
        case "delete":
            need(1, "ID")
            c.call("DELETE", f"{api}/{_id(args[0])}")
            return "deleted"
        case "comments":
            need(1, "ID")
            return c.call("GET", f"{api}/{_id(args[0])}/comments")
        case "comment":
            anchor, tail = _option(args[2:], "--anchor")
            args = args[:2] + tail
            need(2, "ID TEXT [--anchor JSON]")
            body = {"text": args[1]}
            if anchor is not None:
                try:
                    body["anchor"] = json.loads(anchor)
                except ValueError:
                    raise Usage("comment: --anchor must be JSON") from None
            return c.json("POST", f"{api}/{_id(args[0])}/comments", body)
        case "take":
            need(1, "ID")
            return c.call("POST", f"{api}/{_id(args[0])}/take")
        case "reply":
            # The option only before ID or after TEXT, so any TEXT can be sent.
            flags = ("--wrote", "--asked")
            state = "done"
            if args and args[0] in flags:
                state, args = args[0], args[1:]
            elif len(args) == 4 and args[3] in flags:
                state, args = args[3], args[:3]
            need(3, "[--wrote|--asked] ID N TEXT  or  ID N TEXT [--wrote|--asked]")
            state = state.removeprefix("--")
            return c.json(
                "POST",
                f"{api}/{_id(args[0])}/comments/{_number(args[1])}/reply",
                {"text": args[2], "state": state},
            )
        case "wait":
            every_arg, args = _option(args, "--every")
            limit_arg, args = _option(args, "--timeout")
            once = "--once" in args
            if once:
                args.remove("--once")
            need(1, "ID [--once] [--every SECONDS] [--timeout SECONDS]")
            every = float(every_arg or 10)
            limit = float(limit_arg if limit_arg is not None else 6 * HOUR if once else 0)
            if not (math.isfinite(every) and math.isfinite(limit) and every >= 5 and limit >= 0):
                raise Usage("wait: --every 5 or more seconds, --timeout 0 (no limit) or more")
            # A link says which; a bare id is a project's if the server
            # knows such a project of ours, else a file's.
            target, kind = _wait_target(args[0])
            if not once:
                for event in subscribe(c, target, kind, every, limit):
                    emit(event)
                return None
            event = None
            if kind != "a":
                event = wait_project(c, target, every, limit, maybe_file=kind is None)
            return json.dumps(event or wait(c, target, every, limit))
        case "resolve" | "reopen":
            need(2, "ID N")
            return c.json(
                "PATCH",
                f"{api}/{_id(args[0])}/comments/{_number(args[1])}",
                {"resolved": cmd == "resolve"},
            )
        case "delete-comment":
            need(2, "ID N")
            c.call("DELETE", f"{api}/{_id(args[0])}/comments/{_number(args[1])}")
            return "deleted"
        case "drop-drafts":
            need(1, "ID")
            c.call("DELETE", f"{api}/{_id(args[0])}/drafts")
            return "drafts deleted"
        case "me":
            return c.call("GET", "/api/v1/me")
        case "new-key":
            if not from_env:
                _check_writable(path)  # before the old key stops working
            new = json.loads(c.call("POST", "/api/v1/me/key"))["key"]
            if from_env:
                return f"the old key no longer works; put the new one into {KEY_VAR}:\n{new}"
            try:
                write_settings(path, url, new)
            except OSError as e:
                # The old key is gone already: the new one must not be.
                print(
                    f"the old key no longer works, and the new one could not be saved to"
                    f" {path} ({e}); put this line into that file, or set {URL_VAR} and"
                    f" {KEY_VAR} in the environment:\n{KEY_VAR}={new}"
                )
                sys.exit(1)
            return f"the old key no longer works; the new one is saved to {path}"
        case "users":
            return c.call("GET", f"{admin}/users")
        case "stats":
            return c.call("GET", f"{admin}/stats")
        case "invite":
            need(1, "NAME [DAYS]", 2)
            body = {"name": args[0]}
            if len(args) > 1:
                body["ttl_seconds"] = round(float(args[1]) * DAY)
            return c.json("POST", f"{admin}/invites", body)
        case "invites":
            return c.call("GET", f"{admin}/invites")
        case "cancel-invite":
            need(1, "ID")
            c.call("DELETE", f"{admin}/invites/{args[0]}")
            return "cancelled"
        case "quota":
            need(2, "NAME MB|null")
            quota = None if args[1] == "null" else round(float(args[1]) * MB)
            return c.json("PATCH", f"{admin}/users/{args[0]}", {"quota_bytes": quota})
        case "remove":
            need(1, "NAME")
            c.call("DELETE", f"{admin}/users/{args[0]}")
            return "removed"
        case "settings":
            if not args:
                return c.call("GET", f"{admin}/settings")
            need(2, "|".join(SETTINGS) + " N")
            if args[0] not in SETTINGS:
                raise Usage(f"settings: one of {', '.join(SETTINGS)}")
            field, unit = SETTINGS[args[0]]
            return c.json("PATCH", f"{admin}/settings", {field: round(float(args[1]) * unit)})
    raise Usage(USAGE)


def main() -> None:
    # A comment in Cyrillic or with an emoji must not crash a cp1252 or
    # cp866 console after `take` has already moved it to "working".
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        out = run(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(130)
    except Usage as e:
        print(str(e).rstrip(), file=sys.stderr)
        sys.exit(2)
    except (ValueError, OverflowError) as e:  # a number argument that does not parse
        print(f"artifacts_cli: {e}", file=sys.stderr)
        sys.exit(2)
    if out:
        print(out)


if __name__ == "__main__":
    main()
