# Workspace review outcome in Google Docs

This slice connects the native Docs adapter to the app and SQLite, based on
the per-user Drive checkpoint. It is not deployed. See
`docs/plans/storage-and-review-close.md` for storage and sign-in.

## Content and closure

**Finish review** publishes a round. It does not mark the review closed,
approve a version, or trigger final compaction. Record closure explicitly
before issuing the final report. Keep the same native Google Doc beside the
artifact, in its managed project folder (or **Без проекта**).

- **Chosen:** the recorded selection and its reason. Do not turn this into a
  claim of approval unless approval was recorded separately.
- **Considered but not chosen:** name each recorded alternative and give its
  reason. Use **Not recorded** if the reason is unknown. Do not label a pending
  option as rejected.
- **Versions and evidence:** retain all exact version links from the published
  rounds, not just the chosen or current version. Refer to published threads
  or rounds; do not export drafts or full discussion by default.
- Add a screenshot only when it explains a decision. Use the exact version,
  number the relevant places, and put short published excerpts or thread
  references beside it. If private embedding is not available, link to the
  protected evidence instead of exposing a public image URL.

## End-to-end acceptance

The reviewer works in Agent Artifacts as on the disk-backed installation.
The agent shares work and revisions at the same link. The reviewer publishes
feedback and explicitly closes the review when the decision is made. The
workflow creates or finalizes the native Doc automatically after explicit
closure, not because someone clicked a summary button. Opening the Doc to
read it is optional; editing it is not part of the review. Show the result
as a plain link in the existing discussion for an authorized viewer, once
the server has verified closure and the Doc. The existing **Finish review**
view has a final-round choice for the linked Workspace owner. A normal round
still publishes feedback without closing the review. The existing discussion
poll updates the closing status and the summary link. The existing artifact
or project watch delivers an ID-only closing notice to the owning agent.
The agent confirms the closure, summarizes its evidence, and calls
`review-finalize`. The user does not provide report inputs.

For a Workspace Drive artifact, the regular viewer and its download are
bound to the published version selected when the page opened. A replacement
between the viewer request and the content request does not change the bytes
in that open review. Reloading the stable artifact link selects the new
version. **Show** switches the content and download to the current version
without changing the stable artifact URL. Send or discard an open draft
review before switching to avoid citing a different file from the one it
reviewed.

### Closing publication

- `POST /a/{id}/review` can carry a `submission` object with `id` (a stable
  request ID), `version` (the viewer's exact version ID), and `close` (a
  boolean). This is only for Workspace Drive artifacts in projects with
  report support enabled. Old clients can still publish a normal review.
- Migration 17 stores the submission with the published comments and
  decisions in the same SQLite transaction. Only the Workspace account
  linked to the owning agent can close the review; another reviewer can
  still send feedback. Sending a round does not close the review. Closing
  can publish the last drafts, or close with no drafts. Do not infer closure
  from comment text, resolved threads or approval of a proposal.
- Retrying the same request does not publish again or consume newer drafts.
  A different close request cannot replace the recorded boundary. Discussion
  can continue, but later publications do not enter the outcome's version
  and thread list. A requested closure is not a completed report.
- The owner agent can read `GET /api/v1/artifacts/{id}/review-report/source`
  after closure for the exact version links and published thread numbers
  through that boundary. The endpoint returns no texts, drafts or secrets.
  The browser uses that submission in the existing review flow. The collector
  sends `review_closed` once per observed pending publication and can replay
  it after restart until acknowledgment. `watch recover` still finds it after
  acknowledgment until Google readback confirms the final report.
- Migration 18 captures each cited thread's reply count and decision state
  in the same transaction as closure, without copying text. Use
  `review-evidence ID CLOSURE N` (owner-only `GET /review-report/evidence/{n}`
  with `closure_id`) for a single retained thread. Later replies are excluded
  even if they have the same timestamp. A deleted thread or a pre-capture
  backup has no readable evidence; do not guess or use the current feed.

## Enable and call

Leave `AA_REVIEW_DOCS` unset or `0` to keep the report endpoints off. Use
`AA_REVIEW_DOCS=1` with configured Workspace Drive storage.
Only a current key for the artifact's owning agent can use these commands.
The agent must be linked to a Workspace account; Drive and Docs use that
account's grant. A report currently requires a Drive artifact in a project
so its exact links do not have the unfiled artifact deadline. Archive and
unfiled-report retention remain separate work.

### Finalize a browser closure

`POST /api/v1/artifacts/{id}/review-report/finalize` receives `closure_id`,
`outcome`, and `rows`. The owner agent can send them using `review-finalize ID
JSON_FILE`. The ID must match the authenticated closing publication. `outcome`
has `chosen` and `alternatives`; options have `label`, `reason`, and optional
published `threads` from the source. A missing choice or reason is `null`
and prints **Not recorded**. `rows` has exactly one `{version_id, decision,
reason}` per captured version. The server supplies the URLs and their order.
It rejects any missing or extra version, evidence outside the boundary, and
changed payload after dispatch.

The server reserves an operation keyed by the closure, creates one tagged
blank native Doc in the managed folder, and writes the outcome and version
table in one guarded Docs batch. No Doc is needed for intermediate rounds.
`finalized` and `already_finalized` mean the same Doc was confirmed. When a
response is lost, read the same tag/Doc and original payload before claiming
success. Never repeat a Google create or batch that might have been sent.

The earlier manual `rounds` and `compact` routes are used only by local
regression fixtures for the staged protocol. The deployed app and agent CLI
do not expose them.

## Adapter and journal

- The adapter uses the caller's existing Drive transport. It accepts a native
  Doc only if the tagged Doc is in the artifact blob's exact managed parent.
  My Drive lookup uses the user's corpus; Shared Drive lookup uses that drive.
  It does not call the permissions API or change sharing.
- Capture the document, tab, and presentation digest from a verified blank
  Doc before appending anything. Google returns nonempty default document,
  named and section styles even for a synthetic native Doc; do not assume
  empty style dictionaries. Persist the digest independently of the Doc and
  reject compaction if the styles later differ. Do not capture a new baseline
  from a populated document to bypass that check.
- SQLite migration 13 adds the Doc mapping and operation journal. Each
  confirmed round keeps its exact version link, text digest and link offsets.
  Verify actual hyperlinks on recovery, not just a marker found in the Doc.
  The app validates version IDs against committed versions and thread numbers
  against published threads on the same artifact before opening Google.
  It does not copy draft text, discussion text, or credentials into the report.
- Persist an exclusive closure operation and its inputs before dispatch.
  On an uncertain create or edit, inspect the same mapped Doc or scoped tag
  using reads only. An empty listing is not proof of failed creation. Do not
  issue another mutation, pick a different Doc, or mark success until readback
  verifies the complete intended result.
- Simulated Google tests drive the owner routes through the app, restart
  from the same SQLite file, and confirm exact version bytes after a later
  upload. The isolated app also passed the Google write/read test below.
  Cross-user access with two real accounts still needs verification.

## Live synthetic probe

The approved My Drive folder accepted a native blank Doc, two rounds with
synthetic exact-version URLs, and a single table compaction in the same Doc.
The first readback refused Google's expanded default table styles. The probe
did not resend the Docs batch. A stricter reader that recognizes the observed
native defaults verified the complete table, exact hyperlinks and chosen and
rejected reasons from the already written Doc; it still rejects custom style
changes. The probe then moved the marked synthetic Doc to Trash and confirmed
that state. The synthetic URLs were not tied to working artifacts.

A separate scratch app on the same host used the approved My Drive folder
and the pilot grant in memory only. It uploaded one synthetic project file
in two versions via the real app routes, appended two report rounds, and
closed the same Doc explicitly. An early call returned unavailable before
any Doc write claim; the scratch journal confirmed it was safe to continue
that same request. The app verified the compacted Doc and treated the next
identical closure as already compacted. Anonymous version download was
refused; a synthetic signed-in session using the approved test grant read
both exact byte sequences. The probe confirmed its Doc, file and emptied
project folder in Trash. It did not use the production database, change
live routes, or share any files. This is a one-account integration check,
not live rollout or a real two-account permission test.

The direct finalization probe used a fresh scratch app, the approved My
Drive folder, and the existing pilot grant in memory. It published a round
and a closing review for two different exact revisions through the browser
review endpoint, read the agent's captured source, then sent one real Docs
batch against the blank native Doc. It discarded Google's successful reply.
After reopening the app, the same request verified the whole table and links
without a second batch. The discussion status included the ready Doc ID;
both protected exact downloads matched the original bytes. The synthetic
Doc, blob and project folder were the only objects moved to Trash.

`tests/test_workspace_docs_report.py` exercises both folder kinds and
refuses unrelated Docs. `tests/test_workspace_review_routes.py` exercises
owner checks, explicit closure, ambiguous and concurrent writes, restart,
exact-version delivery and published evidence only. Planner and parser
tests also cover UTF-16 calculations and refusal of unsupported edits.
