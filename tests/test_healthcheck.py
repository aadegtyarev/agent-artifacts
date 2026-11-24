from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from agent_artifacts import healthcheck
from agent_artifacts.config import Config, WorkspaceConfig
from agent_artifacts.web import create_app


def test_docker_probe_sends_workspace_authority_over_loopback(tmp_path, monkeypatch, capsys):
    config = Config(data_dir=tmp_path, public_url="https://app.example:8443",
                    workspace=WorkspaceConfig("client", "secret", "company.example"))
    monkeypatch.setenv("AA_PUBLIC_URL", config.public_url)
    assert "CMD python -m agent_artifacts.healthcheck" in Path("Dockerfile").read_text()
    with TestClient(create_app(config), base_url="http://127.0.0.1:8090") as client:
        assert client.get("/health").status_code == 400
        calls = []

        class FakeConnection:
            def __init__(self, host, port, timeout):
                calls.append((host, port, timeout))

            def request(self, method, path, headers):
                calls.append((method, path, headers))
                self.response = client.request(method, path, headers=headers)

            def getresponse(self):
                return SimpleNamespace(status=self.response.status_code)

            def close(self):
                calls.append("closed")

        monkeypatch.setattr(healthcheck.http.client, "HTTPConnection", FakeConnection)
        healthcheck.main()
        assert calls == [("127.0.0.1", 8090, 3),
                         ("GET", "/health", {"Host": "app.example:8443"}), "closed"]
        assert capsys.readouterr().out == "ok\n"


def test_probe_default_and_failed_health(monkeypatch):
    monkeypatch.delenv("AA_PUBLIC_URL", raising=False)
    calls = []

    class FailingConnection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def request(self, method, path, headers):
            calls.append((method, path, headers))

        def getresponse(self):
            return type("Response", (), {"status": 503})()

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(healthcheck.http.client, "HTTPConnection", FailingConnection)
    with pytest.raises(RuntimeError):
        healthcheck.main()
    assert calls == [("127.0.0.1", 8090, 3),
                     ("GET", "/health", {"Host": "127.0.0.1:8090"}), "closed"]
