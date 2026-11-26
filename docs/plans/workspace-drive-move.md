# Move a Drive-backed artifact between managed projects

Backend only. Keep the existing artifact ID, Drive blob ID, retained provider
revisions, comment threads, and optional native review Doc ID. Do not copy or
replace the bytes. The current artifact URL and every retained version URL stay
valid. Moving has no bearing on what was reviewed.

## Preconditions

- Resolve the owner and linked Workspace subject from the current agent key.
  Use the approved bound root and the same signed-in writer, never a browser
  grant from someone else or a disk fallback. Honor the Drive write pause.
- Check the current visible artifact and both project scopes, including Undo
  and destination quota. A project destination is owned by the same agent.
  Resolve both managed folders inside the same configured root. Use the existing
  create/reconcile path if the destination does not have a folder yet.
- Verify the source folder, blob file, head revision, managed name and any
  mapped review Doc by their registered IDs. Refuse a move while a content,
  project rename, artifact rename or report write is unresolved.
- Reparenting changes inherited Google permissions even with no explicit
  sharing call. Require equally scoped source/destination and equivalent
  readable effective permission lists, including roles, expiration and limited
  access. If permissions are incomplete or the difference cannot be verified,
  refuse before sending. Do not broaden or rewrite an ACL to make it match.
- Plan the managed name at the destination with the existing suffix/collision
  convention. Reserve the placement slot and name in SQLite before any file
  reparent request; ordinary creation/rename must respect those reservations.

## Dispatch and reconcile

1. Record a move ticket: exact request, artifact/head, old/new scope and names,
   expected folder and Doc IDs, and the independent dispatch state of each
   object. Do not accept different input while a ticket is active.
2. For each object, inspect its parent and metadata, then claim its one dispatch
   in a transaction. Use Drive `files.update` with `addParents` and
   `removeParents` on the known ID; set the blob name in that same request,
   keep the Doc name and carry the ticket's operation marker. Do not send media
   or create another revision. Use an entity precondition where Google supports
   it; readback is mandatory regardless of the response.
3. Verify both parents, operation marks, blob name and unchanged head. The
   file and Doc do not move atomically. A partial move remains pending; the
   SQLite project and exact version URLs are not changed by a half-success. On
   retry reconcile by known IDs before considering the unsent second dispatch.
   An ambiguous first dispatch can be read back but is never sent again blindly.
4. Only after both are verified, atomically commit the new project,
   section and managed name, then release the ticket. Before that, refuse
   competing replacement, rename, report writing, project removal, archive
   operations requiring a settled placement, and destination replacement.
   Include pending tickets in the private recovery inventory, not UI logs.

Start with unfiled → project, no Doc. Moves must not accidentally apply an
expiry to retained review evidence; until that policy is explicit, refuse to
unfile an artifact with a closing capture or review Doc. Next verify
project → project with a Doc, exact retained versions and an interruption
between the two remote writes. A live folder share does not replace the
readback/ACL checks. Do not release a Shared Drive move as verified on the
basis of My Drive results alone.
