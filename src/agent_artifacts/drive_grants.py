"""Private refresh grants for verified Workspace subjects, not browser sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .config import WorkspaceConfig
from .google_oidc import valid_oauth_token

_SUBJECT = re.compile(r"[!-~]{1,255}")
_MAX_GRANT = 4100


class DriveGrantStoreError(OSError):
    """A fixed storage failure, without paths, identifiers or credential data."""


class WorkspaceDriveGrants:
    """Stores only refresh tokens; lookup is bound to the OAuth client/domain/subject.

    The directory is separate from the session and artifact databases. Treat
    it as a secret volume, not as part of ordinary application backups.
    """

    def __init__(self, data_dir: Path, workspace: WorkspaceConfig) -> None:
        self.directory = data_dir / "workspace-drive-grants-v1"
        self._policy = (workspace.client_id, workspace.hosted_domain)
        try:
            self.directory.mkdir(mode=0o700, exist_ok=True)
            self._check_directory()
        except OSError:
            raise DriveGrantStoreError("Drive grants directory is not private and usable") from None

    def _check_directory(self) -> None:
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise DriveGrantStoreError("Drive grants directory is not private and usable")

    def _path(self, subject: str) -> Path:
        if not isinstance(subject, str) or not _SUBJECT.fullmatch(subject):
            raise ValueError("invalid verified Google subject")
        key = json.dumps((*self._policy, subject), ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return self.directory / hashlib.sha256(b"aa-drive-grant-v1\0" + key).hexdigest()

    def get(self, subject: str) -> str | None:
        path = self._path(subject)
        try:
            self._check_directory()
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > _MAX_GRANT):
                    raise DriveGrantStoreError("Drive grant is not a private regular file")
                value = source.read(_MAX_GRANT + 1).decode("ascii")
            if not valid_oauth_token(value):
                raise DriveGrantStoreError("invalid stored Drive grant")
        except (OSError, UnicodeError):
            raise DriveGrantStoreError("Drive grant could not be read") from None
        return value

    def put(self, subject: str, refresh_token: str) -> None:
        path = self._path(subject)
        if not valid_oauth_token(refresh_token):
            raise ValueError("invalid Drive refresh token")
        temporary: Path | None = None
        try:
            self._check_directory()
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.directory, delete=False) as out:
                temporary = Path(out.name)
                os.fchmod(out.fileno(), 0o600)
                out.write(refresh_token.encode("ascii"))
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            raise DriveGrantStoreError("Drive grant could not be saved") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # a private incomplete file is never used for a grant
