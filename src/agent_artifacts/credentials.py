"""Agent key format and storage. Browser credentials use a separate namespace."""

import hashlib
import secrets

# Not starting with a dot: "." and ".." would never reach the app in a URL.
NAME_RE = r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}"
_REVOKED_PREFIX = "revoked:"


def new_key() -> str:
    return "aa_" + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def revoked_key_marker() -> str:
    # Not a SHA-256 hex digest; cannot authenticate as an agent key, even on
    # older builds. The marker is unique so it fits agents.key_hash UNIQUE.
    return _REVOKED_PREFIX + secrets.token_hex(32)


def is_revoked(key_hash: str) -> bool:
    return key_hash.startswith(_REVOKED_PREFIX)
