"""Settings from environment variables, checked once at startup."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MB = 1024 * 1024


@dataclass(frozen=True)
class WorkspaceConfig:
    client_id: str
    client_secret: str = field(repr=False)
    hosted_domain: str


@dataclass(frozen=True)
class Config:
    data_dir: Path
    public_url: str
    ttl_seconds: int = 48 * 3600
    max_file_bytes: int = 25 * MB
    max_total_bytes: int = 2048 * MB
    user_quota_bytes: int = 500 * MB
    # The raw origin for "Open separately", "" when there is none.
    raw_url: str = ""
    workspace: WorkspaceConfig | None = None
    accounts: bool = False

    def __post_init__(self) -> None:
        if self.accounts:
            check_https_origin(self.public_url, "AA_PUBLIC_URL", "with accounts enabled")

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ
        public_url = check_public_url(env.get("AA_PUBLIC_URL", "http://127.0.0.1:8090"))
        raw_url = check_raw_url(env.get("AA_RAW_URL", ""), public_url)
        workspace = workspace_from_env(env, public_url, raw_url)
        accounts = env.get("AA_ACCOUNTS", "0")
        if accounts not in ("0", "1"):
            raise ValueError("AA_ACCOUNTS must be 0 or 1")
        return cls(
            data_dir=Path(env.get("AA_DATA_DIR", "./data")),
            public_url=public_url,
            raw_url=raw_url,
            workspace=workspace,
            accounts=accounts == "1",
            ttl_seconds=int(float(env.get("AA_TTL_HOURS", "48")) * 3600),
            max_file_bytes=int(float(env.get("AA_MAX_FILE_MB", "25")) * MB),
            max_total_bytes=int(float(env.get("AA_MAX_TOTAL_MB", "2048")) * MB),
            user_quota_bytes=int(float(env.get("AA_USER_QUOTA_MB", "500")) * MB),
        )


def workspace_from_env(env, public_url: str, raw_url: str) -> WorkspaceConfig | None:  # noqa: ANN001
    names = ("AA_GOOGLE_CLIENT_ID", "AA_GOOGLE_CLIENT_SECRET", "AA_GOOGLE_HOSTED_DOMAIN")
    values = [env.get(name, "").strip() for name in names]
    if not any(name in env for name in names):
        return None
    if not all(values):
        raise ValueError("all Workspace settings are required: " + ", ".join(names))
    client_id, client_secret, domain = values
    if (not domain.isascii() or not _HOST_RE.fullmatch(domain) or len(domain) > 253
            or any(len(label) > 63 for label in domain.split("."))):
        raise ValueError("AA_GOOGLE_HOSTED_DOMAIN must be a lowercase DNS domain")
    for name, url in (("AA_PUBLIC_URL", public_url), ("AA_RAW_URL", raw_url)):
        if not url:
            continue
        check_https_origin(url, name, "in Workspace mode")
    return WorkspaceConfig(client_id, client_secret, domain)


def check_https_origin(url: str, name: str, context: str) -> None:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"{name} has a bad port") from None
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/")
            or parts.query or parts.fragment or parts.netloc.endswith(":")
            or (port is not None and port == 0)):
        raise ValueError(f"{name} must be an HTTPS origin {context}")


def check_public_url(url: str, name: str = "AA_PUBLIC_URL") -> str:
    """Return ``url`` without a trailing slash, or raise if it is plain HTTP
    on a host reachable from the internet."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{name} is not an http(s) URL: {url!r}")
    if parts.scheme == "http" and not _is_local(parts.hostname):
        raise ValueError(
            f"{name} {url!r} is plain HTTP on a public host; "
            "put the service behind a TLS reverse proxy and use https://"
        )
    return url.rstrip("/")


def check_raw_url(url: str, public_url: str) -> str:
    """The raw origin: "" when unset, else ``scheme://host[:port]`` on a host
    other than the public URL's, as checked for the public URL itself."""
    url = url.strip()
    if not url:
        return ""
    if not url.isascii():
        raise ValueError(f"AA_RAW_URL {url!r}: write a non-ASCII host name in its xn-- form")
    check_public_url(url, "AA_RAW_URL")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"AA_RAW_URL {url!r} has a bad port") from None
    if any(c in url for c in "?#@") or parts.path not in ("", "/") or parts.netloc.endswith(":"):
        raise ValueError(f"AA_RAW_URL must be an origin with no path: {url!r}")
    host = normal_host(parts.hostname or "")
    if not _HOST_RE.fullmatch(host):
        raise ValueError(f"AA_RAW_URL {url!r} has a bad host name")
    if host == normal_host(urlsplit(public_url).hostname or ""):
        raise ValueError(f"AA_RAW_URL {url!r} needs a host name of its own")
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")


def normal_host(host: str) -> str:
    """A host name as compared: lower case, without the root's trailing dot."""
    return host.lower().removesuffix(".")


# Letters, digits and hyphens in dot-separated labels (IPv4 included).
_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*")


def _is_local(host: str) -> bool:
    if host == "localhost" or host.endswith((".local", ".lan", ".localhost")):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private
