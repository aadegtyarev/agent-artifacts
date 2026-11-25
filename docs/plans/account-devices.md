# Account and devices: implementation slices

Status: local candidate for review. See the [browser preview](https://art.adsrv.ru/a/tH_flHf21uI6zgGk-CNnVw)
from both local installation modes. The existing viewer and sign-in releases
are not changed by this branch. Drive/Docs work is a separate slice.

## First slice: common account entry and devices

- `AA_ACCOUNTS=1` enables account pages on an HTTPS installation. `/account`
  uses one template. With Workspace configured, `/` starts the existing Google
  flow; a protected file or project still returns to its own link. Without
  Workspace, `/` offers a device code. Public file and project URLs do not
  require a browser login. Device cookies do not change public comment authorship.
- `artifacts account-claim` uses an existing agent key to request a 10-minute,
  single-use setup code. It returns the code separately from the claim page
  address. Paste it in the browser, check the named agent and confirm. An
  unclaimed agent can be attached to a signed-in account or create the first
  non-Google account and device, never by name, email, or file link alone.
  Rotating an agent key invalidates its outstanding claim. An already linked
  agent cannot issue a new claim. Send codes only to the person claiming them.
- **Connect another device** issues one 8-character code per account for at
  most 5 minutes, never in a URL. Only the issuing browser sees it. The code
  creates a separate host-only, HTTP-only session on successful redemption;
  it cannot be reused. Generating another code invalidates the previous one.
  A signed-out issuing device cannot authorize a new browser. Failed attempts
  are limited both per caller and across the installation.
- The account lists projects with their files, stand-alone files, linked agents
  and devices. Share uses the existing file or project link, not a new access
  grant. Device sign-out invalidates only the chosen session; both
  sign-in providers use the same device list and actions. A Google account is
  keyed by the verified subject under the configured Google issuer, not email.
- Agent records still own their files and projects. A human account groups the
  agent records it has claimed; an agent credential never gains access to a
  different agent's files or the right to upload to its project. Workspace
  membership does not grant admin access.
- Account holders can issue, revoke and replace normal agent keys. Only the
  first response shows a new key; revocation leaves files, projects and other
  keys alone. Admin keys are not managed by the account. A non-Google user can
  explicitly save one high-entropy recovery code outside the browser. Using it
  once revokes all previous browser sessions and pairing codes, not agent keys.
  No self-service recovery without a connected device or its saved code.

## Implementation preview feedback

- Show Undo in the existing toast treatment, centred at the top at phone and
  desktop width. Keep the real Undo deadline and check concurrent deletions.
- Match button and field sizes to the shipped mobile controls. Keep compact
  visuals separate from hit targets, and use the same Text scaling for
  equivalent controls in both entry modes and the account.
- Amend the design reference with these rules. Include reviewer checks in
  `AGENTS.md` and browser assertions for geometry, placement and focus. Replace
  the implementation preview on its existing link before closing the threads.

## Before production account release

- Check the UI against the approved entry/account flow in Chromium at phone
  and desktop width, both themes, normal and enlarged Text, including the
  confirmation form, code input and focus after errors.
- Verify project and file deletion with the account page and old links. Keep
  the rollback path compatible with pending deletion markers. Do not migrate
  account ownership or data based on similarity of names.
- Review the optional Workspace baseline separately from account work when
  bringing them onto `main`. No real credentials are needed for local tests.

## Delete and Undo

- Mark a file or project pending deletion in the metadata database; do not
  delete or move its bytes during the Undo window. Hide marked files, their
  comments and downloads, and the files of marked projects from normal
  lookups, including the existing agent API. Retained bytes still count
  against storage limits until cleanup.
- Delete a single file directly and offer **Undo**. Confirm project deletion
  with the number of affected files. If the scope changes before confirmation
  commits, ask again even when the count stayed the same. The Undo window is
  one minute.
- Only the account that owns the file or project can undo deletion. Restore
  the same IDs, links, bytes, comments and drafts. Undoing a project does not
  undo a separate deletion that was already pending on one of its files.
  A stand-alone file's and an active draft's remaining lifetimes pause while
  deletion is pending.
- The cleanup job purges expired deletions and their bytes. Starting cleanup
  and undoing the same deletion must have one winner. Keep administrative
  deletion and agent-key revocation separate from this action.

## Follow-up: Drive and final review summary

First test uploads, replacement and immutable revision links in an isolated
shared-drive folder. Keep comments and drafts in SQLite; no migration of
existing files or changes to public access. Drive/Docs adapters currently have
local fake-backed work in separate worktrees; this does not mean a real Google
folder or OAuth scopes have been tested or approved.

Keep one report per reviewed file. Use a confirmed end-of-review event, not the
existing **Finish review** button by itself: that button publishes a round of
changes. In the final summary, name the options considered, why each rejected
option was rejected, and why the final option was chosen. Keep links to the
actual versions and discussion that support those claims. If no reason was
recorded, say so. Add a screenshot with a short annotated caption when it makes
an important difference visible; reference the version it shows. Do not export
the full discussion or unpublished drafts. Keep the resulting report within
the Workspace access boundary.
