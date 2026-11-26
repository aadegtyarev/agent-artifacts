"""Resolve exactly one verified subject's Google Drive transport per operation."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import requests
from google.oauth2.credentials import Credentials

from .config import WorkspaceConfig
from .drive_grants import DriveGrantStoreError, WorkspaceDriveGrants
from .drive_storage import DriveAuthorizedSession, DriveError, DriveReadControl, DriveStorage, DriveTransientError
from .google_oidc import DRIVE_SCOPE, TOKEN_ENDPOINT, drive_scope_granted, valid_oauth_token

_MAX_TOKEN_RESPONSE = 16 * 1024
_REFRESH_TIMEOUT = 8.0


class DriveGrantRequired(DriveError):
    """This caller needs to authorize Drive; no other caller's grant substitutes."""


def refresh_access(workspace: WorkspaceConfig, refresh_token: str, *, timeout: float) -> str:
    try:
        with requests.post(TOKEN_ENDPOINT, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": workspace.client_id, "client_secret": workspace.client_secret,
            "scope": DRIVE_SCOPE,
        }, allow_redirects=False, stream=True, timeout=timeout) as response:
            status = response.status_code
            if status not in (200, 400):
                raise DriveTransientError("Google authorization is temporarily unavailable")
            body = bytearray()
            for part in response.iter_content(4096):
                body.extend(part)
                if len(body) > _MAX_TOKEN_RESPONSE:
                    raise DriveTransientError("Google authorization returned an invalid response")
            result = json.loads(body)
    except (requests.RequestException, ValueError, RecursionError):
        raise DriveTransientError("Google authorization is temporarily unavailable") from None
    if not isinstance(result, dict):
        raise DriveTransientError("Google authorization returned an invalid response")
    if status == 400 and result.get("error") == "invalid_grant":
        raise DriveGrantRequired("Google Drive authorization has expired or been revoked")
    if status != 200:
        raise DriveTransientError("Google authorization is temporarily unavailable")
    if "scope" in result and not drive_scope_granted(result["scope"]):
        raise DriveGrantRequired("Google did not grant Drive access")
    access, token_type = result.get("access_token"), result.get("token_type")
    if not valid_oauth_token(access) or not isinstance(token_type, str) or token_type.casefold() != "bearer":
        raise DriveTransientError("Google authorization returned an invalid response")
    return access


class WorkspaceDrive:
    def __init__(self, workspace: WorkspaceConfig, grants: WorkspaceDriveGrants, *,
                 folder_id: str, folder_kind: str, max_bytes: int,
                 check_destination: Callable[[], object] | None = None) -> None:
        self._workspace, self._grants = workspace, grants
        self._folder_id, self._folder_kind, self._max_bytes = folder_id, folder_kind, max_bytes
        self._check_destination = check_destination

    @contextmanager
    def open(self, subject: str | None, *, control: DriveReadControl | None = None) -> Iterator[DriveStorage]:
        if self._check_destination is not None:
            self._check_destination()
        if subject is None:
            raise DriveGrantRequired("No linked Workspace account has authorized Drive")
        try:
            refresh = self._grants.get(subject)
        except DriveGrantStoreError:
            raise DriveTransientError("Google authorization is temporarily unavailable") from None
        if refresh is None:
            raise DriveGrantRequired("Google Drive is not authorized for this account")
        timeout = min(_REFRESH_TIMEOUT, control.check()) if control else _REFRESH_TIMEOUT
        access = refresh_access(self._workspace, refresh, timeout=timeout)
        if control is not None:
            control.check()
        # The bearer is held for this operation only. This session never retries a
        # write after expiry, refreshes itself, or follows a redirect.
        with DriveAuthorizedSession(Credentials(access)) as session:
            yield DriveStorage(session, self._folder_id, folder_kind=self._folder_kind,
                               max_bytes=self._max_bytes, project_layout=True)
