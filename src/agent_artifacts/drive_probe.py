"""Operator-run isolated Drive probe. Never used by server startup."""
from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import stat
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from .drive_layout import project_scope
from .drive_storage import DriveAuthorizedSession, DriveError, DriveStorage
from .store import Store

SCOPE = "https://www.googleapis.com/auth/drive.file"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 (OAuth endpoint, not a secret)
_MAX_AUTH_FILE = 16 * 1024


class ProbeConfigurationError(ValueError):
    """A local configuration problem; never include credential material."""


class _ProbeCredentials(Credentials):
    def refresh(self, request) -> None:
        super().refresh(request)
        if self.granted_scopes is not None and set(self.granted_scopes) != {SCOPE}:
            self.token = None
            raise RefreshError("unexpected scope for isolated Drive probe")


def load_probe_credentials(path: Path) -> Credentials:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > _MAX_AUTH_FILE):
                raise ProbeConfigurationError("credential file must be private, regular and owned by you")
            content = file.read(_MAX_AUTH_FILE + 1)
        if len(content) > _MAX_AUTH_FILE:
            raise ProbeConfigurationError("credential file is too large")
        data = json.loads(content)
    except ProbeConfigurationError:
        raise
    except (OSError, ValueError, RecursionError):
        raise ProbeConfigurationError("could not read the private Drive credential file") from None
    if not isinstance(data, dict) or data.get("type", "authorized_user") != "authorized_user":
        raise ProbeConfigurationError("the probe requires a separate OAuth user grant")
    scopes = data.get("scopes")
    if isinstance(scopes, str):
        scopes = scopes.split()
    if scopes != [SCOPE] or data.get("token_uri") != TOKEN_ENDPOINT:
        raise ProbeConfigurationError("the probe accepts only a Google drive.file grant")
    required = ("client_id", "client_secret", "refresh_token")
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in required):
        raise ProbeConfigurationError("Drive OAuth grant is missing required fields")
    # Do not use a bearer token from a file; refresh with the explicitly
    # requested scope and send it only to the fixed Drive endpoints.
    return _ProbeCredentials(token=None, refresh_token=data["refresh_token"], token_uri=TOKEN_ENDPOINT,
                             client_id=data["client_id"], client_secret=data["client_secret"], scopes=[SCOPE])


def exercise(drive: DriveStorage, path: Path) -> None:
    """Create exactly one disposable project and two revisions of one tiny file."""
    try:
        path.mkdir(mode=0o700)
    except OSError:
        raise ProbeConfigurationError("exercise needs a new local state directory") from None
    store = Store(path, max_total_bytes=4096)
    owner = "drive-probe"
    store.add_agent(owner)
    project = store.add_project(owner, "Drive test " + secrets.token_hex(5))
    first = b"Agent Artifacts Drive exercise: revision one\n"
    second = b"Agent Artifacts Drive exercise: revision two\n"
    old = store.put_drive(drive, io.BytesIO(first), owner=owner, title="Connection check",
                          name="probe.txt", kind="txt", size=len(first), project=project.id)
    if old is None:
        raise DriveError("first exercise revision did not publish")
    latest = store.put_drive(drive, io.BytesIO(second), owner=owner, name="probe.txt", kind="txt",
                             size=len(second), artifact_id=old.id)
    if latest is None or not old.version_id or not latest.version_id:
        raise DriveError("exercise revision did not publish")
    versions = [store.get_drive_version(old.id, version) for version in (old.version_id, latest.version_id)]
    if not all(versions) or versions[0][1].file_id != versions[1][1].file_id:
        raise DriveError("exercise did not retain the same Drive file")
    for item, expected in zip(versions, (first, second), strict=True):
        with drive.open_revision(item[1]) as chunks:
            if b"".join(chunks) != expected:
                raise DriveError("exercise revision differs from uploaded bytes")
    scope = project_scope(project.id)
    with store._conn() as db:
        folder_id, = db.execute("SELECT folder_id FROM drive_folders WHERE scope=? AND ready=1",
                                (scope,)).fetchone()
    drive_id = drive.validate_folder()
    folder = drive._metadata(folder_id)
    drive._validate_child(folder, folder_id, drive_id, scope=scope)
    file = drive._validate_file(versions[0][1].file_id, drive_id, edit=False)
    if (file.get("name") != "Connection check.txt" or file.get("parents") != [folder_id]
            or folder.get("name") != project.title):
        raise DriveError("Drive project folder or readable file name did not match")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder-id", required=True, help="ID of the approved isolated shared-drive folder")
    parser.add_argument("--credentials", required=True, type=Path, help="private OAuth user grant file")
    parser.add_argument("--exercise", type=Path, metavar="NEW_LOCAL_STATE_DIR",
                        help="write one synthetic test file and keep its local journal; omitting this is read-only")
    args = parser.parse_args(argv)
    try:
        creds = load_probe_credentials(args.credentials)
        with DriveAuthorizedSession(creds) as session:
            drive = DriveStorage(session, args.folder_id, max_bytes=4096, timeout=10, project_layout=True)
            drive.validate_folder()
            print("Shared Drive folder and required capabilities verified.")
            if args.exercise is not None:
                exercise(drive, args.exercise)
                print("Project folder, readable file name and both exact revisions verified.")
            else:
                print("No folders or files written.")
    except ProbeConfigurationError as error:
        print(f"Drive probe stopped: {error}")
        return 2
    except (DriveError, ValueError):
        print("Drive probe stopped: folder or operation could not be confirmed. Do not repeat an uncertain write.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
