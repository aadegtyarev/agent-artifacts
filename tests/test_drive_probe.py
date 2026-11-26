import json

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from test_drive_layout import LayoutDrive

from agent_artifacts import drive_probe


def credential_file(path, **overrides):
    path.write_text(json.dumps({"type": "authorized_user", "token_uri": drive_probe.TOKEN_ENDPOINT,
                                "scopes": [drive_probe.SCOPE], "client_id": "test-client",
                                "client_secret": "synthetic-secret", "refresh_token": "synthetic-refresh",
                                "token": "supplied-but-unused"} | overrides))
    path.chmod(0o600)
    return path


def test_private_grant_does_not_reuse_access_token(tmp_path):
    path = credential_file(tmp_path / "auth.json")
    credentials = drive_probe.load_probe_credentials(path)
    assert credentials.token is None and credentials.scopes == [drive_probe.SCOPE]
    path.chmod(0o644)
    with pytest.raises(drive_probe.ProbeConfigurationError, match="private"):
        drive_probe.load_probe_credentials(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(drive_probe.ProbeConfigurationError):
        drive_probe.load_probe_credentials(link)


@pytest.mark.parametrize("override", [
    {"type": "service_account"},
    {"scopes": ["https://www.googleapis.com/auth/drive"]},
    {"scopes": [drive_probe.SCOPE, "openid"]},
    {"token_uri": "https://example.test/token"},
    {"refresh_token": None},
])
def test_other_auth_is_rejected_without_revealing_values(tmp_path, override):
    path = credential_file(tmp_path / "auth.json", **override)
    with pytest.raises(drive_probe.ProbeConfigurationError) as refused:
        drive_probe.load_probe_credentials(path)
    assert "synthetic-" not in str(refused.value)


def test_scope_widening_from_refresh_is_refused(tmp_path, monkeypatch):
    path = credential_file(tmp_path / "auth.json")
    def broad_grant(self, request):
        self.token = "test"
        self._granted_scopes = ["https://www.googleapis.com/auth/drive"]
    monkeypatch.setattr(Credentials, "refresh", broad_grant)
    credentials = drive_probe.load_probe_credentials(path)
    with pytest.raises(RefreshError):
        credentials.refresh(None)
    assert credentials.token is None


def test_probe_defaults_to_read_only_and_exercises_one_file_when_explicit(tmp_path, monkeypatch, capsys):
    class OfflineSession(LayoutDrive):
        def __enter__(self): return self
        def __exit__(self, *args): return False

    fake = OfflineSession()
    monkeypatch.setattr(drive_probe, "DriveAuthorizedSession", lambda _: fake)
    path = credential_file(tmp_path / "auth.json")
    args = ["--credentials", str(path), "--folder-id", "root"]
    assert drive_probe.main(args) == 0
    assert fake.folder_posts == fake.blob_posts == 0
    assert "No folders or files written" in capsys.readouterr().out
    state = tmp_path / "journal"
    assert drive_probe.main([*args, "--exercise", str(state)]) == 0
    assert fake.folder_posts == fake.blob_posts == 1
    assert (state / "meta.sqlite").is_file()
    assert "both exact revisions verified" in capsys.readouterr().out
    assert drive_probe.main([*args, "--exercise", str(state)]) == 2
    assert fake.folder_posts == fake.blob_posts == 1
