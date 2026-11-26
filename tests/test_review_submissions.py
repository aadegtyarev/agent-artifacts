"""Explicit browser closure is durable, private and separate from sending feedback."""
import sqlite3
from contextlib import closing
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient
from test_drive_web import upload
from test_report_compaction_readback import model_document, table
from test_watch_adapters import recover
from test_watch_state import ws
from test_workspace_review_routes import closure, endpoint, env, round_body, version, writes  # noqa: F401
from test_workspace_web import MAIN, page_marker

from agent_artifacts.drive_recovery import inventory
from agent_artifacts.google_oidc import GoogleIdentity
from agent_artifacts.store import Store
from agent_artifacts.web import create_app
from agent_artifacts.workspace_auth import SESSION_COOKIE, WorkspaceAuthStore


pytestmark = pytest.mark.parametrize("env", [False], indirect=True)


def browser(client, config, path, subject="author"):
    auth = WorkspaceAuthStore(config.data_dir / "workspace-auth.sqlite", config.workspace)
    client.cookies.set(SESSION_COOKIE, auth.create_session(
        MAIN, GoogleIdentity(subject, subject + "@company.example", "Reviewer")),
        domain=urlsplit(MAIN).netloc, path="/")
    return {"Origin": MAIN, "X-Workspace-Session": page_marker(client, path)}


def submission(item, key, *, close=False, **fields):
    return {"submission": {"id": key * 22, "version": version(item), "close": close}, **fields}


def test_source_keeps_reviewed_versions_at_closure_and_later_discussion_stays_open(env, tmp_path):
    client, store, cloud, keys, config, first, second = env
    path = urlsplit(first["url"]).path
    review = path + "/review"
    source = endpoint(first, "source")
    owner = keys["author"]
    headers = browser(client, config, path)
    assert client.get(source).status_code == 401
    assert client.get(source, headers=owner).status_code == 404

    # An old page may still publish its round after the next version arrived.
    old_round = submission(first, "a", text="Feedback on old version", agent=True)
    before = client.post(review, headers=headers, json=old_round)
    assert before.status_code == 201
    assert [c["n"] for c in before.json()] == [2]
    assert client.get(source, headers=owner).status_code == 404

    # The retry must not duplicate the comment or publish new drafts.
    draft = client.post(path + "/drafts", headers=headers, json={"text": "Still private"})
    assert draft.status_code == 201
    assert client.post(review, headers=headers, json=old_round).json() == before.json()
    assert client.post(review, headers=headers, json=old_round | {"text": "Changed"}).status_code == 409
    assert [d["text"] for d in client.get(path + "/drafts", headers=headers).json()] == ["Still private"]
    assert client.delete(path + "/drafts/" + draft.json()["id"], headers=headers).status_code == 204

    close = submission(second, "b", close=True)
    assert client.post(review, headers=headers, json=close).json() == []  # no text or drafts required
    assert client.post(review, headers=headers, json=close).status_code == 201
    assert client.post(review, headers=headers, json=submission(second, "c", close=True)).status_code == 409
    assert client.get(source, headers=keys["viewer"]).status_code == 404
    watch_file = f"/api/v1/artifacts/{first['id']}/comments?watch=1"
    watched = client.get(watch_file, headers=owner).json()
    assert watched["review_closure"] == "b" * 22 and len(watched["comments"]) == 2
    assert client.get(watch_file, headers=keys["viewer"]).status_code == 404
    project = f"/api/v1/projects/{first['project']}"
    for route, field in ((project, "artifacts"), (project + "/summary", "files")):
        assert client.get(route, headers=owner).json()[field][0]["review_closure"] == "b" * 22
        assert client.get(route, headers=keys["viewer"]).status_code == 404
    assert not writes(cloud)

    # Keep normal conversation after closure without moving its source boundary.
    late = client.post(review, headers=headers, json=submission(second, "d", text="Later discussion"))
    assert late.status_code == 201 and late.json()[0]["n"] == 3
    response = client.get(source, headers=owner)
    assert response.headers["cache-control"] == "no-store"
    recorded = response.json()
    assert recorded == {"id": "b" * 22, "closed_at": recorded["closed_at"], "final_version_id": version(second),
                        "versions": [{"version_id": version(first), "version_url": first["version_url"], "threads": [2]},
                                     {"version_id": version(second), "version_url": second["version_url"], "threads": []}]}
    with TestClient(create_app(config), base_url=MAIN) as restarted:
        assert restarted.get(source, headers=owner).json() == recorded
        assert restarted.post(review, headers=browser(restarted, config, path), json=close).status_code == 201

    backup = tmp_path / "standalone.sqlite"
    with store._conn() as current, closing(sqlite3.connect(backup)) as copy:
        current.backup(copy)
    state = inventory(backup)
    assert state["metadata_schema"] == 19
    assert state["pending_review_closures"] == [{"artifact_id": first["id"], "version_id": version(second),
                                                  "request_id": "b" * 22}]
    assert "Later discussion" not in str(state) and "Still private" not in str(state)


def test_evidence_is_bound_to_the_closed_thread_even_when_late_replies_share_its_timestamp(env):
    client, store, cloud, keys, config, first, second = env
    path = urlsplit(first["url"]).path
    owner = keys["author"]
    headers = browser(client, config, path)
    artifact = first["id"]
    store.clock = lambda: 1000.0  # no clock distinction between any of the replies
    recorded = client.post(path + "/review", headers=headers,
                           json=submission(first, "a", text="Compare space in the two layouts", agent=True))
    assert recorded.status_code == 201
    n = recorded.json()[0]["n"]
    comment = f"/api/v1/artifacts/{artifact}/comments/{n}"
    assert client.post(comment + "/reply", headers=owner,
                       json={"text": "Compact leaves room for the list", "state": "done"}).status_code == 201
    assert client.post(path + "/review", headers=headers, json=submission(second, "b", close=True)).status_code == 201
    assert client.post(comment + "/reply", headers=owner,
                       json={"text": "Later reply at the same time", "state": "done"}).status_code == 201
    assert client.patch(comment, headers=owner, json={"resolved": True}).status_code == 200
    late = client.post(path + "/review", headers=headers, json=submission(second, "c", text="Later thread"))
    assert late.status_code == 201
    evidence = endpoint(first, f"evidence/{n}")
    closure_id = "b" * 22
    route = evidence + "?closure_id=" + closure_id
    cloud.calls.clear()
    assert client.get(evidence, headers=owner).status_code == 400
    assert client.get(route).status_code == 401
    assert client.get(route, headers=keys["viewer"]).status_code == 404
    assert client.get(route.replace(closure_id, "d" * 22), headers=owner).status_code == 404
    assert client.get(endpoint(first, f"evidence/{late.json()[0]['n']}?closure_id={closure_id}"), headers=owner).status_code == 404
    result = client.get(route, headers=owner)
    assert result.headers["cache-control"] == "no-store"
    assert result.json() == {"n": n, "closure_id": closure_id, "text": "Compare space in the two layouts",
                             "by_agent": False, "decision": None, "resolved": False,
                             "replies": [{"text": "Compact leaves room for the list", "by_agent": True}]}
    with store._conn() as db:
        assert db.execute("SELECT evidence FROM review_submissions WHERE closing=1").fetchone() == (
            '{"' + str(n) + '": [1, null, false]}',)
    assert not cloud.calls

    # Opening a v17 copy must not reconstruct the closed reply set using
    # today's replies. Its known version references still exist.
    with store._conn(write=True) as db:
        db.execute("DROP TABLE drive_moves")
        db.execute("ALTER TABLE review_submissions DROP COLUMN evidence")
        db.execute("PRAGMA user_version=17")
    migrated = Store(config.data_dir, max_total_bytes=100_000)
    with TestClient(create_app(config, migrated), base_url=MAIN) as restored:
        assert restored.get(endpoint(first, "source"), headers=owner).json()["id"] == closure_id
        assert restored.get(route, headers=owner).status_code == 404


def test_notes_source_captures_publish_only_and_excludes_later_discussion(env):
    client, store, cloud, keys, config, first, second = env
    path = urlsplit(first["url"]).path
    owner = keys["author"]
    headers = browser(client, config, path)
    notes = endpoint(first, "notes/source")
    assert client.get(notes, headers=owner).status_code == 404
    made = client.post(path + "/review", headers=headers, json=submission(first, "n", text="Publish this only"))
    n = made.json()[0]["n"]
    assert made.status_code == 201 and made.json()[0]["agent"] is None
    assert client.post(f"/api/v1/artifacts/{first['id']}/comments/{n}/reply", headers=owner,
                       json={"text": "A later reply", "state": "done"}).status_code == 201
    assert client.post(path + "/review", headers=headers,
                       json=submission(second, "q", close=True, agent=True)).status_code == 400
    assert client.post(path + "/review", headers=headers,
                       json=submission(second, "q", close=True)).status_code == 201
    later = client.post(path + "/review", headers=headers, json=submission(second, "r", text="Later discussion"))
    assert later.status_code == 201
    cloud.calls.clear()
    assert client.get(notes).status_code == 401
    assert client.get(notes, headers=keys["viewer"]).status_code == 404
    source = client.get(notes, headers=owner)
    assert source.headers["cache-control"] == "no-store"
    assert source.json() == {"id": "n" * 22, "version_id": version(first), "version_url": first["version_url"],
                             "threads": [n]}
    evidence = endpoint(first, f"notes/evidence/{n}")
    assert client.get(evidence + "?submission_id=" + "q" * 22, headers=owner).status_code == 404
    captured = client.get(evidence + "?submission_id=" + "n" * 22, headers=owner)
    assert captured.headers["cache-control"] == "no-store"
    assert captured.json() == {"n": n, "submission_id": "n" * 22, "text": "Publish this only", "by_agent": False,
                               "decision": None, "resolved": False, "replies": []}
    with store._conn() as db:
        assert db.execute("SELECT evidence FROM review_submissions WHERE request_id=?", ("r" * 22,)).fetchone() == (None,)
    assert not cloud.calls  # reading a capture does not borrow a Drive grant or create a Doc


def test_file_and_project_watch_recover_unfinished_native_doc_after_ack(env, tmp_path):
    client, _, cloud, keys, config, original, _ = env
    owner = keys["author"]
    pid = original["project"]
    first = upload(client, owner, "without-comments.txt", b"draft 1", project=pid).json()
    second = upload(client, owner, "without-comments.txt", b"draft 2", first["id"]).json()
    artifact = first["id"]
    path = urlsplit(first["url"]).path
    assert client.post(path + "/review", headers=browser(client, config, path),
                       json=submission(second, "j", close=True)).status_code == 201

    class AgentClient:
        url = MAIN

        def send(self, method, path):
            response = client.request(method, path, headers=owner)
            assert response.status_code == 200, response.text
            return response.text

    service = AgentClient()
    kinds = (("a", artifact), ("p", pid))
    watchers = [ws.WatchState(tmp_path / "watch", MAIN, kind, target) for kind, target in kinds]
    try:
        for state, (kind, target) in zip(watchers, kinds, strict=True):
            state.poll(service)
            event, = state.pending()
            expected = {"event": "review_closed", "artifact": artifact, "closure": "j" * 22}
            assert event["event"] == expected | ({"project": pid} if kind == "p" else {})
            assert state.ack(event["id"])
            assert recover(service, (MAIN, kind, target))["pending_reports"] == [{"id": artifact, "closure": "j" * 22}]

        # Provider's lost response does not retire the published closing request.
        body = {"closure_id": "j" * 22, "outcome": {"chosen": None, "alternatives": []},
                "rows": [{"version_id": version(second), "decision": "Not recorded", "reason": None}]}
        cloud.docs.lose_response = True
        assert client.post(endpoint(first, "finalize"), headers=owner, json=body).status_code == 503
        assert recover(service, (MAIN, "p", pid))["pending_reports"]
        assert client.post(endpoint(first, "finalize"), headers=owner, json=body).status_code == 200
        assert cloud.creates == cloud.docs.compact_posts == 1 and cloud.appends == 0
        for state, (kind, target) in zip(watchers, kinds, strict=True):
            state.poll(service)
            assert not state.pending() and "pending_reports" not in recover(service, (MAIN, kind, target))
    finally:
        for state in watchers:
            state.close()


def test_one_native_summary_requires_closure_and_keeps_captured_versions_and_evidence(env):
    client, store, cloud, keys, config, first, second = env
    path = urlsplit(first["url"]).path
    final = endpoint(first, "finalize")
    owner = keys["author"]
    headers = browser(client, config, path)
    original = closure(first, second)
    body = {"closure_id": "k" * 22, "rows": original["rows"], "outcome": original["outcome"]}
    body["outcome"]["chosen"]["threads"] = [2]
    assert client.post(final, headers=owner, json=body).status_code == 404
    assert client.post(path + "/review", headers=headers,
                       json=submission(first, "l", text="Compact preserves the list")).status_code == 201
    # Before living notes, ordinary publications kept version/thread IDs but
    # no round boundary. Keep the verified legacy first-write path for these.
    with store._conn(write=True) as db:
        db.execute("UPDATE review_submissions SET evidence=NULL WHERE artifact_id=? AND closing=0", (first["id"],))
    assert client.post(path + "/review", headers=headers,
                       json=submission(second, "k", close=True, text="Wide uses too much space.")).status_code == 201
    assert client.post(path + "/review", headers=headers,
                       json=submission(second, "m", text="Late discussion")).status_code == 201
    extra = upload(client, owner, "review.txt", b"Not reviewed", first["id"]).json()
    cloud.calls.clear()
    assert client.post(final, headers=keys["viewer"], json=body).status_code == 404
    assert client.post(final, headers=owner, json=body | {"closure_id": "z" * 22}).status_code == 404
    assert client.post(final, headers=owner, json=body | {"rows": body["rows"][:1]}).status_code == 400
    invented = body["rows"] + [{"version_id": version(extra), "decision": "Extra", "reason": None}]
    assert client.post(final, headers=owner, json=body | {"rows": invented}).status_code == 400
    late = body["outcome"] | {"chosen": body["outcome"]["chosen"] | {"threads": [4]}}
    assert client.post(final, headers=owner, json=body | {"outcome": late}).status_code == 400
    assert client.post(endpoint(first), headers=owner, json=round_body(first)).status_code == 404
    assert client.post(endpoint(first, "compact"), headers=owner, json=original).status_code == 404
    assert not cloud.calls

    # A lost create is found by the same scoped tag. A lost Docs reply is
    # reconciled on restart by reading the exact table, never replaying it.
    cloud.lose_create = cloud.hide_lookup = True
    assert client.post(final, headers=owner, json=body).status_code == 503
    assert client.post(final, headers=owner, json=body).status_code == 409
    assert cloud.creates == 1 and cloud.docs.compact_posts == 0
    cloud.hide_lookup = False
    cloud.docs.lose_response = True
    assert client.post(final, headers=owner, json=body).status_code == 503
    assert cloud.creates == cloud.docs.compact_posts == 1 and cloud.appends == 0
    cloud.calls.clear()
    with TestClient(create_app(config), base_url=MAIN) as restarted:
        response = restarted.post(final, headers=owner, json=body)
        assert response.json() == {"status": "finalized", "report_url": "https://docs.google.com/document/d/doc/edit"}
        assert not writes(cloud)
        assert restarted.post(final, headers=owner, json=body).json()["status"] == "already_finalized"
        assert restarted.post(final, headers=owner, json=body | {"outcome": late}).status_code == 400
    doc = model_document(cloud.docs.compacted)
    rows = table(doc)["tableRows"][1:]
    links = [row["tableCells"][0]["content"][0]["paragraph"]["elements"][0]["textRun"]["textStyle"]["link"]["url"]
             for row in rows]
    assert links == [first["version_url"], second["version_url"]]
    assert "Preserves the list" in str(doc) and "Uses too much space" in str(doc)
    assert "Private draft" not in str(doc) and "Late discussion" not in str(doc)
    with store._conn() as db:
        assert db.execute("SELECT kind,sent,confirmed_at IS NOT NULL FROM review_operations").fetchall() == [("close", 1, 1)]
    assert cloud.creates == cloud.docs.compact_posts == 1


def test_close_requires_the_linked_workspace_user_and_a_matching_version(env, monkeypatch):
    client, store, cloud, keys, config, first, second = env
    path = urlsplit(first["url"]).path
    review = path + "/review"
    original = cloud.request

    def transport(actor, method, url, **kwargs):
        if actor == "viewer":
            assert method == "GET"  # a separate consenting reader, no provider changes
            return cloud.drive.request(method, url, **kwargs)
        return original(actor, method, url, **kwargs)

    monkeypatch.setattr(cloud, "request", transport)
    with TestClient(create_app(config, store), base_url=MAIN) as other:
        headers = browser(other, config, path, "viewer")
        assert 'data-review-state="open"' in other.get(path).text
        assert other.get(path + "/comments", headers=headers).headers["X-Review-State"] == "open"
        note = {"text": "Peer feedback"}
        assert other.post(review, headers=headers, json=submission(first, "e", close=True, **note)).status_code == 404
        # Their ordinary review is still allowed on an exact version.
        assert other.post(review, headers=headers, json=submission(first, "e", **note)).status_code == 201
    headers = browser(client, config, path)
    made_up = submission(first, "f", close=True, text="Wrong version")
    made_up["submission"]["version"] = "z" * 22
    assert client.post(review, headers=headers, json=made_up).status_code == 404
    assert client.post(review, headers=headers, json=submission(second, "g")).status_code == 400
    assert client.get(endpoint(first, "source"), headers=keys["author"]).status_code == 404
    with TestClient(create_app(replace(config, drive_read_only=True), store), base_url=MAIN) as paused:
        paused_headers = browser(paused, config, path)
        assert 'data-review-state="open:updating"' in paused.get(path).text
        assert paused.post(review, headers=paused_headers,
                           json=submission(second, "h", close=True)).json()["error"] == "drive_read_only"
    with store._conn() as db:
        assert db.execute("SELECT request_id,closing FROM review_submissions").fetchall() == [("e" * 22, 0)]
        assert db.execute("SELECT count(*) FROM review_documents").fetchone() == (0,)
