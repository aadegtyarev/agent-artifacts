# Plan: sign-in and storage providers (Google)

Status: idea, not started. To be designed after the desktop interface.

A company with Google Workspace accounts wants to use them to open and comment
on artifacts, and to keep the files on Google Drive, while the service's own
interface stays as it is. The current way (agent keys, anonymous viewers, files
on the server's disk) must keep working. No migration: each installation picks
its providers in its settings.

## Two independent providers

| Part | Now (default) | Google |
| --- | --- | --- |
| Who you are | Agents by key; people anonymous, the browser makes an author key and the viewer asks for a name | People sign in with a company Google account (OAuth, the company's domain only). Name and email come from Google, so comments carry real names on every device and settings can live on the server. A signed-in person gets an agent key themselves ("get a key"), so invites are not needed for colleagues. Admins: a list of emails or a Google group. Agents still use keys. |
| Where file contents are | `files/` on the server | A shared drive of the company, a folder per project. Comments, projects and drafts stay in the service's database; Drive holds only the bytes. The server still serves files through its sandbox; an "Open in Drive" link may be added. |

In the code, storage becomes one small interface (put, open/stream, delete,
size) with two implementations; sign-in becomes a second way to identify a
person next to the author key. The OAuth client secrets stay on the server,
never in the repository.

## Without migration

The public installation keeps keys and disk. A company installation starts with
"Google sign-in + Drive". Moving an existing installation's files would be a
separate one-off tool, only if ever needed.

## Open questions

1. Who views artifacts on a company installation: employees only (sign-in
   required) or sometimes outsiders by link? Decides whether anonymous links
   stay next to Google sign-in.
2. Drive: the company's shared drive under a service account (recommended:
   files survive people leaving, no personal tokens to keep) or each person's
   own Drive?
3. Lifetime on Drive: as now (a set time outside a project) or longer?
4. Is the public installation still needed on the same code, or does the
   company one become the main one?
