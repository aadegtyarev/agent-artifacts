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
AGENT_ARTIFACTS_ENV points to another file.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

USAGE = """usage: artifacts_cli.py <command> [args]

  setup                     read AGENT_ARTIFACTS_URL=... and AGENT_ARTIFACTS_KEY=...
                            lines from stdin and save them for later calls
  upload FILE [NAME]        upload a file, print the artifact JSON (url, id, expires_at)
  replace ID FILE [NAME]    replace the file behind an existing link
  list                      your live files
  delete ID                 delete one now
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
        if not url.startswith(("https://", "http://")):
            raise Usage(f"{URL_VAR} must start with https:// or http://, got {url!r}")
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
        write_settings(path, found[URL_VAR], found[KEY_VAR])
        return f"saved to {path}"

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
    except ValueError as e:  # a number argument that does not parse
        print(f"artifacts_cli: {e}", file=sys.stderr)
        sys.exit(2)
    if out:
        print(out)


if __name__ == "__main__":
    main()
