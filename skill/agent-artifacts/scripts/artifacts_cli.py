#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""artifacts_cli: command line for agent-artifacts. Python standard library only.

Run it with `uv run artifacts_cli.py <command>` or `python artifacts_cli.py <command>`.

The service URL and key come from AGENT_ARTIFACTS_URL and AGENT_ARTIFACTS_KEY
if set, otherwise from the env file that the setup command writes:
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
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
  reply ID N TEXT [--wrote|--asked]
                            answer in comment N's chat: done (default), done
                            with a remark or question (--wrote), or not done
                            until the person answers (--asked); the option
                            goes after TEXT or before ID
  wait ID [--every S] [--timeout S]
                            poll every S seconds (30 by default, 5 at least)
                            and print, then exit, on the first of: comments
                            waiting for you {"event": "waiting", "comments"},
                            comments accepted since the start {"event":
                            "accepted", "comments"}, the file gone {"event":
                            "gone"}, or after --timeout (6 hours by default,
                            0 for none) {"event": "timeout"}
  resolve ID N              mark comment N done
  reopen ID N               mark comment N open again
  delete-comment ID N       delete comment N; the other numbers stay
  drop-drafts ID            delete every unpublished draft on a file, unread
  me                        your name, space used and quota, admin or not
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

Answers are JSON as the service sends them: sizes in bytes, times in seconds.
"""

URL_VAR, KEY_VAR = "AGENT_ARTIFACTS_URL", "AGENT_ARTIFACTS_KEY"
MB, HOUR, DAY = 1024 * 1024, 3600, 86400
# settings NAME -> (API field, unit it is given in)
SETTINGS = {
    "ttl-hours": ("ttl_seconds", HOUR),
    "user-quota-mb": ("user_quota_bytes", MB),
    "max-total-mb": ("max_total_bytes", MB),
}


class Usage(Exception):
    pass


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


def wait(c: Client, path: str, every: float, timeout: float) -> str:
    """Poll a file's comments until there is something for the agent and
    say what: comments waiting for it, comments the person accepted since the
    start, the file gone, or the time up. Silent meanwhile, so it can run in
    the background."""
    end = time.monotonic() + timeout if timeout else None
    before = None  # the comments resolved at the first poll do not count
    while True:
        try:
            comments = json.loads(c.send("GET", path))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return json.dumps({"event": "gone"})
            if e.code != 429 and e.code < 500:
                _fail(e)  # a wrong or revoked key will not get better
            comments = None
        except (OSError, ValueError, http.client.HTTPException):
            comments = None  # the network, a restart, a proxy page: poll again
        if not isinstance(comments, list) or not all(isinstance(x, dict) for x in comments):
            comments = None  # not a list of comments: an odd answer, poll again
        if comments is not None:
            waiting = [x for x in comments if x.get("agent") == "waiting" and not x.get("resolved")]
            if waiting:
                return json.dumps({"event": "waiting", "comments": waiting})
            if before is None:
                before = {x.get("n") for x in comments if x.get("resolved")}
            accepted = [x for x in comments if x.get("resolved") and x.get("n") not in before]
            if accepted:
                return json.dumps({"event": "accepted", "comments": accepted})
        left = end - time.monotonic() if end is not None else every
        if left <= 0:
            return json.dumps({"event": "timeout"})
        time.sleep(min(every, left))


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


def run(argv: list[str]) -> str | None:
    if not argv:
        raise Usage(USAGE)
    if argv[0] in ("help", "-h", "--help"):
        return USAGE.rstrip()
    cmd, args = argv[0], argv[1:]
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
        return f"saved to {path}" + (f"; the previous one is kept in {backup}" if backup else "")

    from_file = parse_settings(path.read_text()) if path.is_file() else {}
    url = os.environ.get(URL_VAR) or from_file.get(URL_VAR)
    key = os.environ.get(KEY_VAR) or from_file.get(KEY_VAR)
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
            return c.upload("PUT", f"{api}/{args[0]}", args[1], {"name": name, **details})
        case "set":
            how = "ID [--title TEXT] [--project ID|none] [--section TEXT|none]"
            need(1, how)
            if not details:
                raise Usage(f"usage: artifacts_cli.py set {how}")
            # Only here can a project or a section be cleared.
            for field in ("project", "section"):
                if details.get(field) == "none":
                    details[field] = None
            return c.json("PATCH", f"{api}/{args[0]}", details)
        case "project-new":
            need(1, "TITLE")
            return c.json("POST", projects, {"title": args[0]})
        case "projects":
            return c.call("GET", projects)
        case "project":
            need(1, "ID")
            return c.call("GET", f"{projects}/{args[0]}")
        case "project-rename":
            need(2, "ID TITLE")
            return c.json("PATCH", f"{projects}/{args[0]}", {"title": args[1]})
        case "project-delete":
            need(1, "ID")
            c.call("DELETE", f"{projects}/{args[0]}")
            return "deleted"
        case "list":
            return c.call("GET", api)
        case "delete":
            need(1, "ID")
            c.call("DELETE", f"{api}/{args[0]}")
            return "deleted"
        case "comments":
            need(1, "ID")
            return c.call("GET", f"{api}/{args[0]}/comments")
        case "take":
            need(1, "ID")
            return c.call("POST", f"{api}/{args[0]}/take")
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
                f"{api}/{args[0]}/comments/{_number(args[1])}/reply",
                {"text": args[2], "state": state},
            )
        case "wait":
            every, args = _option(args, "--every")
            limit, args = _option(args, "--timeout")
            need(1, "ID [--every SECONDS] [--timeout SECONDS]")
            every = float(every or 30)
            limit = float(limit if limit is not None else 6 * HOUR)
            if not (every >= 5 and limit >= 0):  # NaN too
                raise Usage("wait: --every 5 or more seconds, --timeout 0 (no limit) or more")
            return wait(c, f"{api}/{args[0]}/comments", every, limit)
        case "resolve" | "reopen":
            need(2, "ID N")
            return c.json(
                "PATCH",
                f"{api}/{args[0]}/comments/{_number(args[1])}",
                {"resolved": cmd == "resolve"},
            )
        case "delete-comment":
            need(2, "ID N")
            c.call("DELETE", f"{api}/{args[0]}/comments/{_number(args[1])}")
            return "deleted"
        case "drop-drafts":
            need(1, "ID")
            c.call("DELETE", f"{api}/{args[0]}/drafts")
            return "drafts deleted"
        case "me":
            return c.call("GET", "/api/v1/me")
        case "new-key":
            new = json.loads(c.call("POST", "/api/v1/me/key"))["key"]
            if os.environ.get(KEY_VAR):
                return f"the old key no longer works; put the new one into {KEY_VAR}:\n{new}"
            write_settings(path, url, new)
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
    try:
        out = run(sys.argv[1:])
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
