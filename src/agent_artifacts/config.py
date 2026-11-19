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

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ
        return cls(
            data_dir=Path(env.get("AA_DATA_DIR", "./data")),
            public_url=check_public_url(env.get("AA_PUBLIC_URL", "http://127.0.0.1:8090")),
            ttl_seconds=int(float(env.get("AA_TTL_HOURS", "48")) * 3600),
            max_file_bytes=int(float(env.get("AA_MAX_FILE_MB", "25")) * MB),
            max_total_bytes=int(float(env.get("AA_MAX_TOTAL_MB", "2048")) * MB),
        )


def check_public_url(url: str) -> str:
    """Return ``url`` without a trailing slash, or raise if it is plain HTTP
    on a host reachable from the internet."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"AA_PUBLIC_URL is not an http(s) URL: {url!r}")
    if parts.scheme == "http" and not _is_local(parts.hostname):
        raise ValueError(
            f"AA_PUBLIC_URL {url!r} is plain HTTP on a public host; "
            "put the service behind a TLS reverse proxy and use https://"
        )
    return url.rstrip("/")


def _is_local(host: str) -> bool:
    if host == "localhost" or host.endswith((".local", ".lan", ".localhost")):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private
