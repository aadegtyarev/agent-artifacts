"""Settings from environment variables, checked once at startup."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MB = 1024 * 1024


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

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ
        public_url = check_public_url(env.get("AA_PUBLIC_URL", "http://127.0.0.1:8090"))
        return cls(
            data_dir=Path(env.get("AA_DATA_DIR", "./data")),
            public_url=public_url,
            raw_url=check_raw_url(env.get("AA_RAW_URL", ""), public_url),
            ttl_seconds=int(float(env.get("AA_TTL_HOURS", "48")) * 3600),
            max_file_bytes=int(float(env.get("AA_MAX_FILE_MB", "25")) * MB),
            max_total_bytes=int(float(env.get("AA_MAX_TOTAL_MB", "2048")) * MB),
            user_quota_bytes=int(float(env.get("AA_USER_QUOTA_MB", "500")) * MB),
        )


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
    """The raw origin: "" when unset, else a bare origin on a host other than
    the public URL's, as checked for the public URL itself."""
    url = url.strip()
    if not url:
        return ""
    url = check_public_url(url, "AA_RAW_URL")
    parts = urlsplit(url)
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(f"AA_RAW_URL must be an origin with no path: {url!r}")
    if parts.hostname == urlsplit(public_url).hostname:
        raise ValueError(f"AA_RAW_URL {url!r} needs a host name of its own")
    return url


def _is_local(host: str) -> bool:
    if host == "localhost" or host.endswith((".local", ".lan", ".localhost")):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private
