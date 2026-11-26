"""Docs response cap is independent from the smaller Drive metadata cap."""

import pytest

from agent_artifacts.docs_report import _DOC_JSON_LIMIT, DocsReport
from agent_artifacts.drive_storage import _JSON_LIMIT, DriveProtocolError, DriveStorage


class LargeResponse:
    status_code = 200

    def __init__(self):
        self.read = 0
        self.closed = False

    def iter_content(self, chunk_size):
        while True:
            self.read += chunk_size
            yield b"x" * chunk_size

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.response = LargeResponse()

    def request(self, *args, **kwargs):
        return self.response


def test_docs_cap_reads_at_most_one_chunk_over_and_closes():
    session = Session()
    report = DocsReport.for_folder(session, "folder", public_url="https://app.example")
    with pytest.raises(DriveProtocolError, match="Docs JSON response exceeds limit"):
        report._json_request("GET", "https://docs.googleapis.com/v1/documents/doc", document=True)
    assert _DOC_JSON_LIMIT < session.response.read <= _DOC_JSON_LIMIT + 8192
    assert session.response.closed


def test_drive_metadata_limit_stays_small():
    response = LargeResponse()
    with pytest.raises(DriveProtocolError, match="Drive JSON response exceeds limit"):
        DriveStorage._json(response)
    assert _JSON_LIMIT < response.read <= _JSON_LIMIT + 8192
    assert _JSON_LIMIT == 65536
