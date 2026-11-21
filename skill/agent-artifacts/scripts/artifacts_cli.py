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

USAGE = """usage: artifacts_cli.py <command> [args]

  setup                     read AGENT_ARTIFACTS_URL=... and AGENT_ARTIFACTS_KEY=...
                            lines from stdin and save them for later calls;
                            a different key already there is copied to env.bak-*
  upload FILE [NAME]        upload a file, print the artifact JSON (url, id, expires_at)
  replace ID FILE [NAME]    replace the file behind an existing link
  list                      your live files, with comments and open_comments counts
  delete ID                 delete one now
  comments ID               the comments on a file, oldest first; marks them read
  resolve ID N              mark comment N done
  reopen ID N               mark comment N open again
  delete-comment ID N       delete comment N; the other numbers stay
  me                        your name, space used and quota, admin or not
  new-key                   replace your key (after a leak) and save the new one

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

    def call(self, method: str, path: str, body: bytes | None = None, ctype: str = "") -> str:
        """The answer's body; an HTTP error prints it and exits with 1."""
        req = urllib.request.Request(self.url + path, data=body, method=method)  # noqa: S310 (checked above)
        req.add_header("Authorization", f"Bearer {self.key}")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with _opener.open(req, timeout=120) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace") if e.fp else ""
            # JSON errors name their code; anything else (a proxy's 429 or 502
            # page) gets the HTTP status instead.
            print(body if body.startswith("{") else f"HTTP {e.code}: {e.reason}", file=sys.stderr)
            sys.exit(1)
        except OSError as e:
            sys.exit(f"artifacts_cli: cannot reach {self.url}: {e}")

    def json(self, method: str, path: str, data: object) -> str:
        return self.call(method, path, json.dumps(data).encode(), "application/json")

    def upload(self, method: str, path: str, file: str, name: str | None) -> str:
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
        if name:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n'
                f"{name}\r\n".encode()
            )
        parts.append(f"--{boundary}--\r\n".encode())
        return self.call(method, path, b"".join(parts), f"multipart/form-data; boundary={boundary}")


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


def _quote(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', "%22").replace("\r", "").replace("\n", "")


def run(argv: list[str]) -> str | None:
    if not argv:
        raise Usage(USAGE)
    if argv[0] in ("help", "-h", "--help"):
        return USAGE.rstrip()
    cmd, args = argv[0], argv[1:]
    path = env_file()

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

    match cmd:
        case "upload":
            need(1, "FILE [NAME]", 2)
            return c.upload("POST", api, args[0], args[1] if len(args) > 1 else None)
        case "replace":
            need(2, "ID FILE [NAME]", 3)
            return c.upload("PUT", f"{api}/{args[0]}", args[1], args[2] if len(args) > 2 else None)
        case "list":
            return c.call("GET", api)
        case "delete":
            need(1, "ID")
            c.call("DELETE", f"{api}/{args[0]}")
            return "deleted"
        case "comments":
            need(1, "ID")
            return c.call("GET", f"{api}/{args[0]}/comments")
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
