"""Pin a Workspace Drive destination before any app-managed remote write."""
from __future__ import annotations

import hashlib
import json
from dataclasses import astuple, dataclass

from .config import DriveFolderConfig, WorkspaceConfig
from .drive_storage import DriveError

SCHEMA = """
CREATE TABLE drive_destination (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    folder_id TEXT NOT NULL,
    folder_kind TEXT NOT NULL CHECK (folder_kind IN ('my-drive', 'shared-drive')),
    origin TEXT NOT NULL,
    policy_hash TEXT NOT NULL
);
"""

# A reserved folder ID or an unresolved create is not proof that nothing
# happened remotely. Refuse to adopt old metadata based on current env values.
_EVIDENCE = (
    "drive_refs", "drive_versions", "drive_attempts", "drive_folders", "drive_names",
    "review_documents", "review_operations", "drive_renames", "drive_project_renames", "review_submissions",
)


class DriveDestinationError(DriveError):
    """Unsafe or unverified configuration, without folder or account identities."""


@dataclass(frozen=True)
class DriveDestination:
    folder_id: str
    folder_kind: str
    origin: str
    policy_hash: str

    @classmethod
    def from_config(cls, folder: DriveFolderConfig, workspace: WorkspaceConfig, origin: str) -> DriveDestination:
        # The OAuth app owns Drive's private appProperties. A new secret for
        # the same client is normal rotation, not a different destination.
        policy = json.dumps([workspace.client_id, workspace.hosted_domain], ensure_ascii=True).encode("ascii")
        digest = hashlib.sha256(b"aa-drive-destination-v1\0" + policy).hexdigest()
        return cls(folder.folder_id, folder.folder_kind, origin.rstrip("/"), digest)


class DriveDestinationStore:
    def __init__(self, connect) -> None:
        self._conn = connect

    @staticmethod
    def _check(db, expected: DriveDestination | None) -> bool:
        row = db.execute("SELECT folder_id, folder_kind, origin, policy_hash FROM drive_destination WHERE singleton=1").fetchone()
        if row is not None:
            if expected is None or DriveDestination(*row) != expected:
                raise DriveDestinationError("Drive destination does not match the database; restore the original settings or verify a migration")
            return True
        if expected is not None and any(db.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()  # noqa: S608 (fixed tables)
                                        for name in _EVIDENCE):
            raise DriveDestinationError("unbound Drive data requires verification before enabling a destination")
        return False

    def check(self, expected: DriveDestination | None) -> bool:
        """Return whether this destination is already pinned; make no changes."""
        with self._conn() as db:
            db.execute("BEGIN")
            return self._check(db, expected)

    def pin(self, expected: DriveDestination) -> None:
        """Call only after Google folder preflight, before the first possible write."""
        with self._conn(write=True) as db:
            if not self._check(db, expected):
                db.execute("INSERT INTO drive_destination VALUES (1, ?, ?, ?, ?)", astuple(expected))
