# Rename a managed Drive file or project folder without changing its links

This is an unreleased Workspace Drive slice. Use the existing agent-key
endpoint `PATCH /api/v1/artifacts/{id}` with a body containing only the new
title, for example `{"title":"Setup screen"}`. The file keeps its extension.
Section changes are local and can be sent separately. Rename a project with
`PATCH /api/v1/projects/{id}` and `{"title":"Setup project"}`. A project
without a managed folder renames locally and needs no Google grant. The app
still refuses moves between projects or Drive roots.

- Only the owning agent's currently linked Workspace account can rename the
  file. Google must allow `canRename` on that file; permission to read is
  not enough. Drive read-only mode pauses renames. The public disk path
  and disk-backed replacements do not use this code.
- Before sending the metadata PATCH, record the current file ID, parent,
  head revision and name, and reserve the new name in SQLite (migration 15).
  The new name participates in the same collision rules as new uploads.
  If there is an unresolved media or Docs operation for the same file, wait
  rather than mixing operations.
- Update the name on the same Drive ID, not the file bytes or parent. The
  app keeps the existing media-operation marker and records a rename marker.
  Read the same ID and verify its parent, head, name and marker before
  committing the new title in SQLite. Do not recreate the file or its native
  review Doc. The artifact ID, exact version links and discussion stay.
- If Google may have received the PATCH, retry **only the same title**.
  The retry only reads Google metadata. A `404`, an old name or a missing
  marker does not prove no write happened. Until confirmed, leave the
  previous app title visible, keep existing version reads, and block media
  replacement, another rename and report writes for that artifact. Do not
  clear the journal or rename by hand to bypass the check.
- A ready operation that failed the preflight before dispatch can be cleared
  safely. A dispatched operation is listed in the private recovery inventory.
  The archive tool refuses that artifact until its state is understood.
  Read the recorded ID and verify access before planning any repair.

Google and SQLite are separate systems. The metadata read and PATCH do not
lock out simultaneous changes made directly in Google Drive. If a managed
file moves, changes revision or has an unexpected name, stop rather than
forcing it back. A future move needs its own protocol for both the file and
its native Doc. This slice does not implement retention, remapping old
Google IDs after a loss, or moving existing data on `.env` changes.

## Project folders

- Rename only a registered, confirmed folder of a populated project. If
  initial folder creation or its first upload is unresolved, do not rename
  that folder. Check the same configured root and the folder's project tag.
- Migration 16 stores the folder rename claim. Keep both the old folder
  name and the proposed new name reserved until readback verifies the
  single metadata PATCH. `Без проекта` remains reserved for unfiled uploads.
  A different project creating a folder uses a suffix on a collision.
- The project keeps its app ID and Google folder ID. Renaming changes no
  child parents, permissions, file revisions or Google Doc IDs. Child
  reads and uploads still use the same folder ID while a title is pending;
  the old project title stays visible until confirmation. The private
  inventory also lists unresolved folder renames.

## Scratch check on real My Drive

The scratch app uploaded a file in two versions and closed a native review
Doc. It sent one metadata PATCH to Google and deliberately discarded the
successful response. After reopening the scratch app, the same title
request verified the name and marker on the same ID without another PATCH.
Both old version links returned their original bytes; the same Doc remained
in the same parent folder. The file, Doc and empty test folder were verified
in Trash. This was one account and synthetic data, not production rollout.
The v15 inventory and verifier also checked an existing v14 archive offline.

A second scratch run used the project-folder route. It sent one real PATCH
for the existing folder ID and discarded Google's successful response. After
reopening the scratch app, the same title was confirmed by reads only. It
verified the same folder and file IDs, both exact byte sequences, and the
same native Doc under that folder. Only the three synthetic objects were
moved to Trash and checked there. Neither run changed normal uploads.
