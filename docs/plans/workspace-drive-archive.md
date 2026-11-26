# Independent copy of selected Drive data

This is a manual recovery tool, not part of uploads or link serving. It does
not copy every new upload, write to Google, change sharing, or restore app
links automatically. The code is not deployed.

## Export using a consenting work account

Work from a standalone SQLite backup made with the app's backup procedure.
Use the pinned Workspace origin, Drive folder ID/mode and OAuth client policy
for that backup. Select one artifact ID explicitly. Use only the exporting
user's authorized Workspace Drive grant; the subject is Google's verified
account ID, not an email, key or token. Authorize the export with that user;
never use a colleague's grant to get around Google access restrictions.

```sh
python -m agent_artifacts.drive_archive export \
  --backup /private/snapshot/meta.sqlite \
  --artifact ARTIFACT_ID \
  --subject CONSENTING_GOOGLE_SUBJECT \
  --include-report \
  --output /private/archive/artifact-copy
```

- The destination must be a new private directory. Keep the archive and its
  matching SQLite backup on separate protected storage, not under served
  `files/` or in Git. Do not copy Google credentials with them.
- The tool refuses an unbound or mismatched backup, or an artifact with an
  unresolved media/report operation in the snapshot. Use a consistent backup
  after pausing or draining writes. An unverified remote operation is not
  proof of absence.
- For each registered version, read its exact Drive revision, not `HEAD`.
  Match the recorded size and record SHA-256 of the bytes copied. The copy
  hash checks later recovery; it is not an original upload signature.
- `--include-report` requires a confirmed closed report mapping. Back up
  its native Docs JSON and editable DOCX. Verify the same tagged Doc and
  scope, and require an unchanged Google revision across the two exports.
  Google supplies this revision to an editor: the export does not write, but
  a report reader without that revision cannot do this check. These exports
  capture the current stable Doc, not a historical Doc revision from SQLite.
  Avoid manual changes while taking the snapshot. The current verifier rejects
  unsupported structures such as inline images and multiple tabs. Without
  `--include-report`, the archive contains file versions only.
- No more than 200 versions and 1 GiB of blob bytes in one export. DOCX uses
  Google's export limit (10 MiB). A failure is not a usable archive; never
  treat an incomplete directory without a manifest as a successful copy.

## Verify and recover without Google

To check the copy and its SQLite snapshot, and then recover an exact version
into a new protected local file:

```sh
python -m agent_artifacts.drive_archive verify \
  --archive /private/archive/artifact-copy --backup /private/snapshot/meta.sqlite
python -m agent_artifacts.drive_archive extract-version \
  --archive /private/archive/artifact-copy --version EXACT_VERSION_ID \
  --output /private/restore/recovered.bin
```

`verify` without `--backup` checks the manifest and files but not their match
to a SQLite copy. Compare a known digest when doing a restore drill. Open
`report.docx` in a trusted editor and compare its outcome and citations with
`report.json`. Google Docs identity, permissions and history are not restored
by a DOCX import. The manifest hashes detect damaged or mismatched copies;
they are not a signature against someone who can alter the whole archive.

No command here changes a production Drive ID, creates a new Google Doc,
or resumes an app link that Google can no longer serve. Verify a recovery
copy independently before an explicit remap or report recreation. Archive
frequency, retention period, storage ownership and a real two-account access
check remain release decisions.

## Scratch recovery rehearsal on real Google

A separate scratch app used the approved My Drive test folder. It uploaded
one synthetic file in two exact versions, closed a native review Doc and
exported both versions, the Docs JSON and the editable DOCX. It moved only
its synthetic file, Doc and emptied folder to Trash and verified each state.

The offline process disabled network access, checked the archive against
its SQLite backup, recovered both exact byte sequences and checked the
chosen/missing reasons in DOCX and both exact links in the native snapshot.
No production database, routes or grants were copied. This is evidence of
recoverable offline content, not of recreated Google IDs or live link recovery.
