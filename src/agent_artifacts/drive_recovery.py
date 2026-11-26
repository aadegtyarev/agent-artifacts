"""Create a private, read-only inventory of a standalone metadata backup."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing, suppress
from pathlib import Path

_SCHEMA_VERSIONS = (14, 15, 16, 17, 18)
_LIST_VERSION = {"renames": 15, "project_renames": 16, "pending_review_closures": 17}
_MAX_ROWS = 100_000

# Include hidden/expired Drive records, not just visible working links. Do not
# expose titles, review text, operation payloads, named users or credentials.
_LISTS = {
    "blobs": "SELECT artifact_id,file_id,revision_id FROM drive_refs ORDER BY artifact_id",
    "versions": ("SELECT artifact_id,version_id,file_id,revision_id,size FROM drive_versions "
                 "ORDER BY artifact_id,version_id"),
    "folders": "SELECT scope,root_id,folder_id,ready FROM drive_folders ORDER BY scope",
    "media_attempts": ("SELECT operation_id,artifact_id,file_id,previous_revision,returned_revision "
                       "FROM drive_attempts ORDER BY operation_id"),
    "reports": ("SELECT d.artifact_id,d.file_id,d.create_sent,d.doc_id,d.presentation IS NOT NULL AS has_baseline, "
                "EXISTS (SELECT 1 FROM review_operations o WHERE o.artifact_id=d.artifact_id "
                "AND o.kind='close' AND o.confirmed_at IS NOT NULL) AS closed "
                "FROM review_documents d ORDER BY d.artifact_id"),
    "report_attempts": ("SELECT artifact_id,operation_key,kind,sent FROM review_operations "
                        "WHERE confirmed_at IS NULL ORDER BY sequence"),
    "pending_review_closures": ("SELECT s.artifact_id,s.version_id,s.request_id FROM review_submissions s "
                                "WHERE s.closing=1 AND NOT EXISTS (SELECT 1 FROM review_operations o "
                                "WHERE o.artifact_id=s.artifact_id AND o.kind='close' AND o.confirmed_at IS NOT NULL) "
                                "ORDER BY s.sequence"),
    "renames": "SELECT artifact_id,operation_id,file_id,revision_id,folder_id,sent FROM drive_renames ORDER BY artifact_id",
    "project_renames": ("SELECT project_id,operation_id,root_id,folder_id,sent FROM drive_project_renames "
                        "ORDER BY project_id"),
}


class InventoryError(ValueError):
    """Fixed feedback only. Never print database paths or data entries."""


def inventory(backup: Path) -> dict:
    try:
        source = backup.resolve(strict=True)
        if not source.is_file() or any(os.path.lexists(str(source) + ext) for ext in ("-wal", "-shm", "-journal")):
            raise InventoryError("Use a standalone SQLite backup without journal sidecars")
        # An SQLite backup is a complete snapshot. Immutable read-only mode
        # neither starts recovery nor creates a WAL/shared-memory sidecar.
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in _SCHEMA_VERSIONS:
                raise InventoryError("Unexpected metadata schema; use the inventory tool for the backed-up build")
            if db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise InventoryError("SQLite backup failed its integrity check")
            target = db.execute("SELECT folder_id,folder_kind,origin,policy_hash FROM drive_destination WHERE singleton=1").fetchone()
            lists = {}
            for name, query in _LISTS.items():
                rows = [] if version < _LIST_VERSION.get(name, 14) else db.execute(query).fetchmany(_MAX_ROWS + 1)
                if len(rows) > _MAX_ROWS:
                    raise InventoryError("Inventory exceeds the supported size; no partial list will be written")
                lists[name] = [dict(row) for row in rows]
    except (OSError, sqlite3.Error):
        raise InventoryError("Could not read the metadata backup") from None
    return {
        "format": "agent-artifacts-drive-inventory-v1",
        "metadata_schema": version,
        "destination": dict(target) if target is not None else None,
        "requires_destination_verification": target is None and any(lists.values()),
        "remote_bytes_verified": False,
        **lists,
    }


def save_inventory(path: Path, data: dict) -> None:
    opened = False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        opened = True
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(data, out, ensure_ascii=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
    except OSError:
        if opened:
            with suppress(OSError):
                path.unlink()
        raise InventoryError("Could not create a new private inventory file") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, type=Path, help="standalone SQLite backup, not the live DB")
    parser.add_argument("--output", required=True, type=Path, help="new private output file, never overwritten")
    args = parser.parse_args(argv)
    try:
        data = inventory(args.backup)
        save_inventory(args.output, data)
    except InventoryError as exc:
        print(f"Inventory stopped: {exc}.")
        return 1
    counts = ", ".join(f"{len(data[name])} {name.replace('_', ' ')}" for name in _LISTS)
    print(f"Private inventory created: {counts}. No Google requests made; remote bytes were not verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
