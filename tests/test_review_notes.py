"""Journal and link lifecycle against a bounded offline Google Docs model."""
from urllib.parse import urlsplit

import pytest
from test_docs_report import Response
from test_report_compaction_dispatch import AtomicDocs
from test_report_source import length
from test_review_submissions import browser, submission
from test_watch_adapters import recover
from test_watch_state import ws
from test_workspace_review_routes import endpoint, env, version, writes  # noqa: F401


class NotesDocs(AtomicDocs):
    def request(self, method, url, **kwargs):
        if not url.endswith(":batchUpdate"):
            return super().request(method, url, **kwargs)
        assert method == "POST" and kwargs["json"]["writeControl"] == {"requiredRevisionId": f"r{self.revision}"}
        requests = list(kwargs["json"]["requests"])
        if self.text != "\n":
            assert requests.pop(0) == {"deleteContentRange": {"range": {
                "startIndex": 1, "endIndex": length(self.text), "tabId": "tab"}}}
        insert = requests.pop(0)["insertText"]
        assert insert["location"] == {"index": 1, "tabId": "tab"}
        self.text = insert["text"] + "\n"
        self.link_spans.clear()
        for request in requests:
            style = request["updateTextStyle"]
            region = style["range"]
            assert style["fields"] == "link" and region["tabId"] == "tab"
            start, stop = region["startIndex"], region["endIndex"]
            expected = style["textStyle"]["link"]["url"]
            actual = self.text.encode("utf-16-le")[2*(start-1):2*(stop-1)].decode("utf-16-le")
            assert expected == actual
            self.link_spans.append((start, stop, actual))
        self.revision += 1
        return Response({})


@pytest.mark.parametrize("env", [False], indirect=True)
def test_one_verified_notes_doc_across_rounds_versions_and_completion(env, tmp_path):
    client, _, cloud, keys, config, first, second = env
    cloud.docs = NotesDocs()
    owner = keys["author"]
    path = urlsplit(first["url"]).path
    notes = endpoint(first, "notes")
    headers = browser(client, config, path)
    doc = "https://docs.google.com/document/d/doc/edit"

    def state():
        return client.get(path + "/comments", headers=headers).headers["X-Review-State"]

    def round_(key, item, *, send=False):
        return client.post(path + "/review", headers=headers,
                           json=submission(item, key, text=f"Round {key} evidence", agent=send)).json()[0]["n"]

    n = round_("a", first)
    first_note = {"submission_id": "a" * 22, "summary": "Unicode evidence 🎯", "threads": [n]}
    assert state() == "closable:updating"

    class AgentClient:
        url = config.public_url

        def send(self, method, path):
            response = client.request(method, path, headers=owner)
            assert response.status_code == 200
            return response.text

    for kind, identity in (("a", first["id"]), ("p", first["project"])):
        service = AgentClient()
        watch = ws.WatchState(tmp_path / "watch", config.public_url, kind, identity)
        try:
            watch.poll(service)
            event, = watch.pending()
            expected = {"event": "review_notes", "artifact": first["id"], "submission": "a" * 22}
            assert event["event"] == expected | ({"project": identity} if kind == "p" else {})
            assert watch.ack(event["id"])
            watch.poll(service)
            assert not watch.pending()
            assert recover(service, (config.public_url, kind, identity))["pending_notes"] == [
                {"id": first["id"], "submission": "a" * 22}]
        finally:
            watch.close()
    assert client.post(notes, headers=keys["viewer"], json=first_note).status_code == 404
    assert client.post(notes, headers=owner, json=first_note | {"threads": [n + 1]}).status_code == 400
    cloud.lose_create = True
    assert client.post(notes, headers=owner, json=first_note).status_code == 503
    assert state() == "closable:updating"
    assert client.post(notes, headers=owner, json=first_note | {"summary": "changed"}).status_code == 409
    expect = {"status": "updated", "report_url": doc}
    assert client.post(notes, headers=owner, json=first_note).json() == expect
    assert cloud.creates == 1 and cloud.appends == 1
    assert state() == "closable:notes:doc"

    n = round_("b", first, send=True)
    n2 = round_("c", second)
    second_note = {"submission_id": "b" * 22, "summary": "Same exact version", "threads": [n]}
    third_note = {"submission_id": "c" * 22, "summary": "A different version", "threads": [n2]}
    assert state() == "closable:updating:doc"
    assert client.get(notes + "/source", headers=owner).json()["id"] == "b" * 22
    assert client.post(notes, headers=owner, json=third_note).status_code == 404
    cloud.lose_append = True
    assert client.post(notes, headers=owner, json=second_note).status_code == 503
    assert state() == "closable:updating:doc"
    cloud.calls.clear()
    assert client.post(notes, headers=owner, json=second_note).json() == expect
    assert not writes(cloud)
    assert cloud.appends == 2
    assert cloud.docs.text.count("Reviewed version\n") == 1
    old = cloud.docs.text
    cloud.docs.text = "Manual addition.\n" + old
    assert client.post(notes, headers=owner, json=third_note).status_code == 409
    assert cloud.appends == 2
    cloud.docs.text = old

    draft = client.post(path + "/drafts", headers=headers, json={"text": "One open draft"})
    assert draft.status_code == 201
    close = submission(second, "d", close=True, text="Final note")
    published = client.post(path + "/review", headers=headers, json=close)
    assert published.status_code == 201
    assert state() == "pending:updating:doc"
    final = {"closure_id": "d" * 22, "outcome": {"chosen": None, "alternatives": [],
             "open_items": [{"label": "Question remains", "threads": [published.json()[0]["n"]]}]},
             "rows": [{"version_id": version(item), "decision": "Not recorded", "reason": None}
                      for item in (first, second)]}
    finish = endpoint(first, "finalize")
    assert client.post(finish, headers=owner, json=final).json()["error"] == "review_notes_pending"
    assert client.post(notes, headers=owner, json=third_note).json() == expect
    assert cloud.docs.text.count("Reviewed version\n") == 2
    assert state() == "pending:notes:doc"
    assert client.post(finish, headers=owner, json=final).json() == {
        "status": "finalized", "report_url": doc}
    assert state() == "ready:doc"
    assert cloud.creates == 1 and cloud.appends == 4
    assert cloud.docs.text.startswith("Review outcome\nChosen: Not recorded\n")
    assert "Still open:\n• Question remains\n" in cloud.docs.text
    for note in (first_note, second_note, third_note):
        assert note["summary"] in cloud.docs.text
    assert [url for _, _, url in cloud.docs.link_spans] == [first["version_url"], second["version_url"]]
    assert client.post(notes, headers=owner, json=first_note).json()["status"] == "already_updated"
    assert client.post(path + "/review", headers=headers, json=submission(second, "e", text="Late")).status_code == 201
    assert client.get(notes + "/source", headers=owner).status_code == 404
    assert cloud.appends == 4
