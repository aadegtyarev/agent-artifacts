# Workspace Drive: enable, pause and recover

This safety slice is not deployed. Keep the public installation on disk and
pair its devices by code. Use separate Workspace volume, environment and
backups; do not promote a pilot database or copy its Google grant.

## Configuration and pausing uploads

Pass `AA_DRIVE_FOLDER_ID` and `AA_DRIVE_FOLDER_KIND` into the Workspace app
container, not just into Compose's interpolation environment. Use the ID
from the folder link. The mode is `my-drive` or `shared-drive`. Google still
authorizes each request as the appropriate user. `AA_REVIEW_DOCS=1` enables
a native summary on explicit review closure.

At the first eligible upload, the app reads the configured folder from
Google and saves its ID, mode, app origin and OAuth client/domain policy in
SQLite (migration 14), before reserving any remote writes. A failed or
uncertain write does not remove this binding. Changing the client secret
without changing the client ID is allowed. The app rejects a different
folder, mode, origin or OAuth policy, or disabled Drive, once the binding
exists. An old database with Drive records or pending operations and no
binding cannot be adopted simply by filling in `.env`.

To pause Google writes while keeping existing Drive links readable, keep
the same settings and set `AA_DRIVE_READ_ONLY=1`. New uploads, replacement
of Drive files and native Docs commands return `503 drive_read_only`; none
fall back to disk. Replacements of existing disk files stay on disk. Google
login and per-user reads remain available. To resume, use the same
configuration with `AA_DRIVE_READ_ONLY=0`. Stop or drain old writers before
changing modes; a config switch does not cancel an in-flight request.

## Release checks and live acceptance

Finish the normal agent-to-reviewer flow, including the handoff from explicit
closure to a visible report link. Take and check backups, then deploy the
complete flow directly on `wb-art.adsrv.ru`; do not enable Drive on
`art.adsrv.ru`. Run the operator's single acceptance session on the live
Workspace installation, not a separate pilot. Use the usual workflow: ask an
agent for work, open the project in Agent Artifacts, send review feedback,
see the revised version, and explicitly close the review. Find the resulting
report link in Agent Artifacts and verify that the summary tells the actual
decision story. The operator does not manually upload test files, invoke
report endpoints, or create or edit a Google Doc. Run the separate-account
check at this final stage rather than asking coworkers to opt into a pilot.

Existing agent keys still need their owner's Workspace account link. If a
key is not linked, the agent runs `account-claim` and the owner confirms the
one-time link in their normal Workspace sign-in. An existing session may
need its own Drive consent on first use. Never infer ownership from an email
or copy the pilot grant to avoid this step.

1. Keep technical Google probes in the approved folder and in scratch state.
   At live acceptance, use the actual linked accounts. The operator reports
   that company users, including the second participant, have write access to
   the folder. Keep the existing sharing. Use two independently consenting
   Workspace accounts with separate browser sessions, agent keys and Drive
   grants. Have A upload two versions of a synthetic file and close its review
   into a native Doc. Have B open both exact versions and the Doc under B's
   own grant, then upload a separate synthetic file with B's linked agent.
   Confirm that B's agent cannot replace A's artifact or change A's report
   through the app; check that both exact versions and the Doc are unchanged.
   This tests the app's ownership checks where both users can write in Drive.
   It does not test a Google read-only role or prevent edits made directly in
   Google. Record that limitation, verify the result with real accounts, and
   clean up only the synthetic objects. Do not reuse the single-account
   pilot's saved grant for this check.
2. Back up `meta.sqlite` with SQLite backup, the Workspace session DB if
   needed, and disk `files/` independently for each installation. Be able to
   open the metadata copy, restore disk test bytes and use the same app
   origin and folder policy. Store or reissue protected grants separately;
   never put them in the inventory or an ordinary backup.
3. Restore a scratch copy and open a known old Drive version and its native
   Doc through the normal authenticated path. Check that the file ID and
   revision ID match and compare a known byte digest, not just `HEAD`,
   filename, response status or byte count. Verify the available recovery
   copy for cited bytes and report content if Google no longer serves them.
   A kept revision in the same Google file is not an independent backup.
   See [the selected-artifact archive tool](workspace-drive-archive.md) for
   a separate copy of the bytes and editable native-report export.
4. Rehearse pause and resume on the same configuration. Check health plus
   the actual old-version and report paths. Verify that a paused upload
   creates no object. Reconcile pending operations before backing out or
   changing destination. Do not roll back to a build that ignores Drive
   references or pending deletion markers after publishing Drive links.

## Read-only recovery inventory

Run this on a standalone SQLite backup, not the live database (versions 14–18):

```sh
python -m agent_artifacts.drive_recovery \
  --backup /private/copy/meta.sqlite \
  --output /private/copy/drive-inventory.json
```

The output is a new `0600` file with the destination, exact version
references and sizes, managed folder IDs, Doc mappings and unresolved
attempts, including closing publications without a verified report. It
includes hidden and expired items. It contains neither file
bytes nor review bodies, names or credentials. Do not publish it. It
checks the SQLite copy but makes no Google requests and verifies no remote
bytes. A pending create without a response is not a failed create. Inspect
its reserved ID or report tag read-only and keep the same operation key.
Do not issue another create, delete, move or overwrite based only on a
missing record, `404`, or an empty search.

## Scratch restore rehearsal

On the current Workspace host, a private scratch app used the approved My
Drive folder and the pilot grant in memory. It uploaded one synthetic file,
made a standalone SQLite backup, created the inventory and opened a scratch
app from the copy. The restored app rejected a different folder setting and
refused writes in read-only mode without calling a Google mutation. With the
original settings, an authorized request to the original exact-version URL
returned bytes with the same SHA-256 digest. The test file and emptied test
folder were verified in Trash. No live app routes, configuration or database
were used. This proves metadata recovery while Google still has the bytes;
it does not prove independent Drive/Docs backup or two-account access.

## What can and cannot move

- Changing `.env` does not move files or update existing version links.
  A move to a different root or from My Drive to Shared Drive needs an
  explicit migration after output is paused. Test permissions, exact
  versions, Doc ID and managed parent IDs in a scratch copy. Do not clear
  the binding or update the production database by hand to bypass the check.
- [File and project title changes](workspace-drive-rename.md) update
  metadata on the existing Drive ID. The app reserves the new name and
  records the rename before dispatch. It verifies the resulting name and
  parent, and the head revision for a file, before publishing. After a lost
  response, it reads the same ID instead of dispatching a second edit.
- Moving an artifact between projects needs a separate, journaled operation
  for both the blob and its native Doc. Google cannot move the pair
  atomically. Check both parents before exposing the new placement; leave
  a partial move unresolved instead of claiming success or copying just
  `HEAD`. Project moves are still blocked in the app.
- Drive hard deletion remains blocked. Expiry of a working link does not
  prove that a Google file can be removed. Before release, agree on archive
  visibility, retention for unfiled items and cited versions, independent
  backups and who performs a verified restore. My Drive ownership and
  access can change when a person leaves; a metadata inventory alone does
  not solve that problem.
