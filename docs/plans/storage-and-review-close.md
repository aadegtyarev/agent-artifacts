# Workspace storage and the final review summary

This is a Drive test checkpoint on the released cabinet baseline, not a
release. Drive storage and Docs remain separate review slices. Do not deploy
this worktree or connect it to a working database from another prototype.

## Storage routing and Drive layout

- Public installation: keep anonymous artifact links and `files/` storage.
- Workspace without Drive: keep the same disk storage behind verified company
  sign-in. With Drive enabled, send **new** upload bytes to the configured
  My Drive or Shared Drive folder before publishing the reference in SQLite.
  Make one direct child folder per project and one named `Без проекта` for
  unfiled uploads. Create folders on the first Drive upload; sections do not
  create folders. Use project titles and artifact titles with source extensions.
  For names that collide among managed entries, add a short stable suffix.
  Keep the Drive IDs in SQLite. Never locate managed objects by their names.
- Reserve each project folder ID before creating it. After a lost response,
  confirm the same ID and scope by reading it; never issue another create if
  its state is unclear. Only access blobs in the configured root or its direct
  managed child folders. Check the Drive location on the root, managed parent
  and file. Do not expand Drive sharing permissions.
- Replace using the artifact's recorded backend. Do not move existing disk
  files, copy every upload to disk first, or fall back to disk after an
  ambiguous Drive write. Temporary transfer files are not durable copies.
- Keep application IDs, metadata, comments, drafts and quota checks in SQLite.
  Read the registered Drive revision, not HEAD. Keep exact version links on
  the authenticated application origin, not on a public raw origin.
- Do not treat Drive 404 or an inaccessible folder as proof of data loss.
  Keep unresolved attempts for inspection; never retry a remote mutation
  merely because its response was lost.

The cabinet's SQLite migrations are 7–8. This checkpoint tests adding the
Drive migrations after them, including an upgrade with a pending disk deletion.
The earlier Drive-only test build used those same version numbers for other
schema changes. Its database must not be reused here. Disk Delete/Undo still
works and reserves quota. Until archive-aware closure is integrated, reject
cabinet deletion of a Drive file or a project containing one; do not create an
Undo marker that would later hard-delete retained data. The revision endpoints
must still respect visibility markers defensively. The optional Workspace
Drive path supports title-only, in-place file and project-folder renames.
Moves between projects still need a separate guarded operation. See
[Drive renames](workspace-drive-rename.md). Do not reorganize managed test
files by hand.

For My Drive, enforce the configured folder and managed parents even when
Google returns no `driveId`. An authorized viewer need not have permission to
add files to open an exact version; uploads still require write access.
Changing the configured folder or mode is not a migration. Define how to retain reads of registered versions before
switching a live destination; do not change existing references or silently
move files. My Drive files are not owned by the organization as Shared Drive
files are. Verify cross-user access and ownership before enabling My Drive
for normal uploads.

## Review outcome after explicit closure

See [the native Docs slice](workspace-review-docs.md) for the adapter and
remaining application wiring.

**Finish review** publishes one round. It does not close the discussion,
approve a version or compact the report. When a review report is needed,
create one native, private Google Doc beside the artifact in its Drive folder.
Do not publish a Markdown file as the report. Uploaded artifacts, including
Markdown artifacts, keep their original formats. When the discussion is
explicitly closed, compact that same Doc using the existing verified-round
and idempotent write protocol.

Use this structure for the compacted report:

1. **Chosen:** what was selected and why. Record approval with its source,
   date and exact version only if it occurred; no approval by inference.
2. **Alternatives considered:** what each option would have changed and the
   recorded reason it was not chosen. If no reason was recorded, say
   **Not recorded**. No invented rationale; a pending option is not a rejection.
3. **Versions and evidence:** exact revision for each reviewed option, and
   the published thread number or round where its reasoning was recorded.
   Preserve all previously cited exact links in the final table. A later
   replacement must not change the linked bytes.
4. **Open notes:** only consequential questions left open at closure. Add a
   screenshot only when it explains a decision better than text. Use the
   exact reviewed revision. Put numbered pins on the relevant areas and
   short published excerpts or thread references beside the image. Do not
   include drafts, full chats, credentials, or public links to protected
   material. If a private image cannot be embedded in Docs, link to the
   protected evidence instead of making the image public.

Test the final summary with alternatives that have and lack recorded reasons,
including a materially changed later upload and an ambiguous Docs write. Check
that non-closed reviews are unchanged, exact links remain private and a retry
confirms the same report instead of appending a second copy. Retention of an
exact approved revision is not a backup or permission to delete the Drive file.

## Workspace routing and settings

The account/device routes are shared code. With Drive disabled, Workspace
sign-in requests only identity and the non-Workspace installation keeps
pairing by one-time code. Do not set these options on the public installation.
For an approved Workspace deployment, pass the variables into the app
container using Compose `environment` or `env_file`:

```dotenv
AA_ACCOUNTS=1
AA_DRIVE_FOLDER_ID=FOLDER_ID_FROM_DRIVE_LINK
AA_DRIVE_FOLDER_KIND=my-drive
```

Use `shared-drive` (the default) for a folder inside a Shared Drive. Without
`AA_DRIVE_FOLDER_ID`, Drive stays off on an unbound database. These are not
the pilot's `AA_DRIVE_TEST_*` settings. The first eligible upload binds the
destination before a possible remote write. Use `AA_DRIVE_READ_ONLY=1` to
pause writes without disabling old links. See [enable, pause and
recover](workspace-drive-release.md) for release checks and the inventory
command. A settings change does not migrate files.

With Drive enabled, **Sign in with Google** asks for identity and `drive`
in one flow. Request offline access and explicit consent so an earlier
Google authorization does not silently skip the prompt. A valid browser
session does not ask again when the user opens another page. If Google does
not return a new refresh token, keep any existing grant for the same
verified account and keep disk content accessible. An older browser session
without a grant can reauthorize on first Drive use. Renewal keeps the same
session and verified account and returns to the requested link. The raw
origin requests identity only and does not serve delegated Drive bytes.

Save refresh tokens in the private `workspace-drive-grants-v1/` directory,
not in the session database, artifact database, or ordinary backups. The
files contain no client secret and are keyed by OAuth app, hosted domain and
verified Google subject. Do not copy the one-user pilot grant into this store.
Loss of this directory means users authorize again, not that files are lost.

Agent uploads use the linked Workspace user's grant. Browser file and review
requests use the visitor's own grant, never the author’s. Review requests
for Drive-backed files also check Google read permission; project and file
metadata still follow the existing Workspace link rules. Disk files keep
their provider and do not require a Drive grant. This routing has simulated
cross-account checks; real multi-account verification is still required.

## Offline checks

- `tests/test_account_drive_compat.py`: upgrade with pending disk deletion,
  quota across providers, protected version URLs and refusal to hard-delete
  Drive-backed items before archive-aware closure.
- `tests/test_drive_web.py`: legacy disk replacement, exact Drive revision
  delivery and provider failures.
- `tests/test_workspace_drive.py`: combined sign-in, same-subject renewal,
  private grant storage, different author/viewer grants even with equal
  email strings, denial without fallback, and independent raw and disk paths.
- `tests/test_workspace_review_routes.py`: the optional native Docs outcome,
  explicit closure, published evidence and exact-version links after a restart.
- `tests/test_drive_release_safety.py`: destination mismatch, write pause
  with exact reads, and the private metadata-only recovery inventory.

## Isolated Google test

Use one configured Google Drive folder for all users of the Workspace
installation. The operator chose the existing folder shared from My Drive.
Support both `my-drive` and `shared-drive` in server configuration so an
operator can change the destination later. Both the consent test and storage
adapter validate the configured mode. The adapter checks the folder boundary
in simulated application tests; per-user production routing is not enabled.
Users do not choose a Drive folder and do not use Google Picker. Keep the current
`wb-art.adsrv.ru` domain, OAuth client and `/auth/callback`. Use the current
server for the isolated test rather than waiting for a move.

The isolated consent pilot left normal sign-in unchanged. The Workspace
Drive mode uses the combined sign-in described above, not the pilot's
separate setup page.

The operator chose user delegation, not a shared server or service-account
credential. The folder ID chooses the destination, not permissions. Browser
operations, including reads of Drive-backed links, use the signed-in viewer's
own Drive grant. Agent operations use only the grant of
the Workspace account explicitly linked to that agent; an agent API key is
not a Google grant. Do not fall back to another person's token or silently
store new Drive uploads on disk. Google enforces the chosen user's folder
rights. Resolve the browsing user's grant for the current request and the
agent's grant through its existing account link after API-key authorization.
The injected Drive transport in this checkpoint is a test seam, not a shared
production credential. Do not consult Drive grants for disk-backed reads or
replacements. Report a missing grant separately from lack of folder access
and from provider failure; do not treat an inaccessible file as deleted.

Run the [real two-user acceptance check](workspace-drive-release.md#release-checks-and-live-acceptance)
on `wb-art.adsrv.ru` after the complete flow is deployed. Do not change the
approved folder's sharing to make a read-only test account.
Keep automated coverage for missing grants, Drive access denial and no disk
fallback, and keep the existing API key and same-agent contribution rules.

For the first isolated test, the operator accepted requesting `drive` from
one consenting work user, without Picker. The grant covers the user's files;
the test itself checks only the configured folder. This test does not enable
Drive for all users. Keep per-user storage grants separate from login
sessions, out of Git, build contexts and logs. Use native Google Docs for
review summaries, with no unnecessary Docs-wide authorization.

The separate Workspace consent pilot passed a live My Drive exercise on the
approved folder: one binary file with two retained revisions and one native
Google Doc, written and read back from one consenting account. Both marked
objects were moved to the Trash and verified there. A follow-up created a
project subfolder by reserved ID, uploaded and verified two revisions of a
file inside it, and moved only the test objects to the Trash. These probes
check Google API behavior, not cross-user access or application routing for
managed project folders. See `workspace-drive-consent.md` in the consent
pilot slice.

The older probe below is for the Shared Drive checkpoint and a separately
approved `drive.file` user grant, not the My Drive grant above. Without
`--exercise` it checks folder metadata and capabilities and writes no Drive
objects.
This command makes real Google requests despite the package resolver's
`--offline`:

```sh
uv run --offline --locked python -m agent_artifacts.drive_probe \
  --folder-id APPROVED_TEST_FOLDER_ID --credentials /private/drive-user-grant.json
```

For the explicitly approved write test, add `--exercise /private/new-probe-state`
with a new local directory. It creates one test project folder and two
revisions of one synthetic text file, reads both revisions back and checks
its returned Drive name and parent. It keeps the SQLite journal and the test
objects. Do not repeat a failed write with a new state directory until the
operator inspects the outcome. Resolve retention, recovery and cleanup before
enabling Drive on `wb-art.adsrv.ru`.
