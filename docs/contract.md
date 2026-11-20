# agent-artifacts: contract

A small web service where an AI agent uploads a file and gets back a link a
person can open, download or pass on. Files live for a fixed time and are then
deleted. Agents use it through a skill and its `aa` command.

## Scenarios

1. An agent finishes a report as an HTML page and uploads it. It gives the
   person the link. The person opens it in a browser, sees the page with its
   styles and working anchors, and downloads it. For a PDF, the person prints
   the page to PDF in the browser; there is no PDF rendering on the server.
2. The agent revises the same report three times during one job. Each time it
   replaces the file at the same link, so the person keeps one link.
3. The agent uploads a screenshot, a Markdown note, a PDF or a zip of results.
   The person views the first three in the browser and downloads the zip.
4. The person forwards the link to a colleague, who opens it without any login.
5. Two days later the file is gone. The link shows "expired or not found".

## Out of scope

- Accounts, logins or passwords for viewers. Knowing the link is the access.
- Multi-file sites. An HTML artifact is one self-contained file.
- Browsing the contents of an archive. Archives are download-only.
- Keeping files beyond the TTL, versions, or history of replaced files.

## File types

The type is taken from the file name's extension and checked against the
content's magic bytes where the format has them. A mismatch is rejected.

| Extension | Shown as | Extra downloads |
| --- | --- | --- |
| `.html`, `.htm` | the page, in a sandbox | — |
| `.md` | rendered in the viewer's browser, in a sandbox | — |
| `.txt` | plain text, dark on white, in a sandbox | — |
| `.pdf` | the browser's PDF viewer | — |
| `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | the image, fitted; a click opens it full size | — |
| `.svg` | the image, in a sandbox | — |
| `.zip`, `.tar.gz`, `.tgz` | name, size, download button | — |

Every artifact can be downloaded in its original form under its original name.

## Limits (defaults, configurable)

| Setting | Default |
| --- | --- |
| Lifetime of an artifact | 48 h from the last upload or replace |
| Size of one file | 25 MB |
| Size of a Markdown or text file (fixed) | 2 MB |
| Size of one agent's live files | 500 MB, then its uploads get `507` `quota_exceeded`; an admin can set it per agent |
| Total size of all stored files | 2 GB, then uploads get `507` `storage_full` |

## HTTP API

Agent calls carry `Authorization: Bearer <agent key>`. An artifact belongs to
the agent that created it; another agent's artifact answers `404`, the same as
a missing one.

| Call | Purpose | Answer |
| --- | --- | --- |
| `POST /api/v1/artifacts` | upload; multipart field `file`, optional field `name` | `201` + artifact JSON |
| `PUT /api/v1/artifacts/{id}` | replace the file, same fields; resets the lifetime; the type may change | `200` + artifact JSON |
| `DELETE /api/v1/artifacts/{id}` | delete now | `204` |
| `GET /api/v1/artifacts` | the calling agent's live artifacts | `200` + list |
| `GET /api/v1/me` | the calling agent's name, space and role | `200` + `{"name", "used_bytes", "quota_bytes", "files", "admin"}` |
| `POST /api/v1/me/key` | a new key for the calling agent; the old one stops working, the files stay | `200` + `{"name", "key"}` |

`name` defaults to the uploaded file name. It is reduced to a safe base name
(no directories, no control characters, at most 200 characters) and must keep
an allowed extension.

Artifact JSON:

```json
{
  "id": "Vq3...22 chars",
  "url": "https://art.example.org/a/Vq3...",
  "name": "report.html",
  "type": "html",
  "size": 48213,
  "created_at": "2026-11-20T10:00:00Z",
  "expires_at": "2026-11-22T10:00:00Z"
}
```

Errors are `{"error": "<code>", "message": "<text>"}` with codes
`bad_request`, `unauthorized`, `not_found`, `too_large`, `unsupported_type`,
`type_mismatch`, `quota_exceeded`, `storage_full`. `quota_exceeded` also
carries `used_bytes` and `quota_bytes`; a replace counts only the new file's
size. Uploads must carry `Content-Length`; a body
larger than the file limit is refused before it is read.

### Admin calls

An admin key (`agent add --admin`, or `agent admin <name>` for an existing
one) may also call these; any other key gets `403 forbidden`. Sizes go in as
MB, lifetimes as hours.

| Call | Purpose | Answer |
| --- | --- | --- |
| `GET /api/v1/admin/users` | every agent: `name`, `admin`, `used_bytes`, `files`, `quota_bytes`, `own_quota`, `last_upload_at` | `200` + list |
| `PATCH /api/v1/admin/users/{name}` | `{"quota_mb": <MB> or null}`: its own quota, or back to the common one | `204` |
| `DELETE /api/v1/admin/users/{name}` | revoke the key and delete its files; not the caller's own | `204` |
| `GET /api/v1/admin/stats` | `users`, `files`, `used_bytes`, `disk_free_bytes`, `limits` | `200` |
| `PATCH /api/v1/admin/settings` | any of `user_quota_mb`, `max_total_mb`, `ttl_hours` | `200` + `limits` |
| `POST /api/v1/admin/invites` | `{"name", "days"}` (days 1–30, default 7): a one-time link for a new agent | `201` + `{"id", "name", "created_by", "expires_at", "url"}`; `409 name_taken` |
| `GET /api/v1/admin/invites` | open invites, without their links | `200` + list |
| `DELETE /api/v1/admin/invites/{id}` | cancel one | `204` |

Settings changed this way are kept in the database and override the
environment variables, which only give the starting values. `max_total_mb`
cannot exceed the space in use plus the free disk, less a reserve (256 MB or
5 % of the free space) for the database; `ttl_hours` is at most 720.
A new lifetime applies to uploads from then on.

### Viewer pages (no auth)

| Path | What |
| --- | --- |
| `/a/{id}` | page with the name, expiry, the content, and buttons: Download, Copy link |
| `/a/{id}/raw` | the content itself, with the sandbox headers below; opened as a page on its own, HTML, SVG, Markdown and text redirect to `/a/{id}`, while images and PDFs open in the browser's own viewer |
| `/a/{id}/download` | the original file as an attachment under its name |

A missing or expired id gives a `404` page saying so. Pages carry
`X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`.

### Invite pages (no auth)

| Path | What |
| --- | --- |
| `GET /join/{token}` | who the invite is for, until when, and a "Get my key" button; uses nothing up |
| `POST /join/{token}` | creates the agent, uses the invite up and shows the key once with the two environment variables and where the skill is |

A used, cancelled or expired token gives a `404` page; a name that got a key
some other way meanwhile gives `409`, and the invite stays until cancelled. Opening the link does
not use it, because chat apps fetch links themselves to build a preview.

## Security

**Links.** `id` is 128 random bits, URL-safe base64. It cannot be listed or
guessed; the only listing is the owner agent's own `GET /api/v1/artifacts`.

**Agent keys.** Created by `agent-artifacts agent add <name>`, which prints the
key once. Only its SHA-256 is stored. `agent remove <name>` revokes it and
deletes that agent's artifacts. An agent can replace its own key with
`POST /api/v1/me/key`, for example after a leak. A key is looked up by its hash, so it is never
compared byte by byte.

**Invites.** The token is 256 random bits; only its SHA-256 is stored, the
same as keys. It is used up in the same transaction that creates the agent, so
two presses cannot both get a key. The pages send `Cache-Control: no-store`,
`Referrer-Policy: no-referrer` and a CSP allowing only their own form and copy
script.

**Untrusted HTML, SVG and Markdown.** Shown only from `/a/{id}/raw`, inside an
`<iframe sandbox>` on the viewer page, and served with:

```
Content-Security-Policy: sandbox allow-scripts allow-forms allow-modals
  allow-popups allow-popups-to-escape-sandbox allow-downloads;
  default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';
  img-src data: blob:; font-src data:; media-src data: blob:; frame-ancestors 'self'
X-Content-Type-Options: nosniff
```

There is no `allow-same-origin`, so the content runs in an opaque origin: it
cannot read the service's pages, other artifacts, or browser storage. It also
cannot load anything from the network, which is why the skill requires
self-contained files. Scripts, forms, `#anchor` links and links opening in a new
tab work, so interactive prototypes do. `localStorage` does not.

Markdown is not rendered on the server. `/raw` sends its text as JSON inside
a `<script>` (with `<` escaped, so the text cannot close the tag) together with
a vendored copy of [marked](https://github.com/markedjs/marked)
(`src/agent_artifacts/static/marked.umd.js`, v18.1.3, checked against the npm
package's integrity hash); the viewer's browser renders it in the same sandbox.
Raw HTML inside the Markdown, event handlers included, is not filtered: it runs
in the same sandbox with the same limits as an HTML artifact.

Images and PDFs are served without the sandbox, which would stop the browser's
PDF viewer, but with `frame-ancestors 'self'`. The viewer page sends
`Cross-Origin-Opener-Policy: same-origin`, so over HTTPS a page an artifact
opens in a new tab cannot navigate the viewer's tab.

**Logs.** The link is the access, so access logs are off: uvicorn runs without
one and the nginx example turns it off.

**Transport.** The service itself speaks plain HTTP on `127.0.0.1`. It refuses
to start when its `public_url` is `http://` and the host is not loopback, a
private-range address or a `.local` / `.lan` name: on the internet it must sit
behind a TLS reverse proxy.

## Storage and cleanup

Files under `<data>/files/<id>`,
metadata and key hashes in `<data>/meta.sqlite`. A background task deletes
expired artifacts every 10 minutes, and expired ids already answer `404` before
that.

## Deployment

Docker image with the app, `docker compose` binding
`127.0.0.1:<port>` and a volume for `<data>`. In front, the host's reverse proxy
terminates TLS, with `client_max_body_size` above the size limit
([deploy.md](deploy.md)).

## Skill

`skill/agent-artifacts/`: `SKILL.md`, `admin.md` and `scripts/aa`, a bash and
curl command line over the API. Usable from any agent that runs shell
commands. `aa` reads `AGENT_ARTIFACTS_URL` and `AGENT_ARTIFACTS_KEY` from the
environment, or else from `~/.config/agent-artifacts/env` (mode 600, written by
`aa setup`; `AGENT_ARTIFACTS_ENV` points elsewhere). It hands the key to curl
through a config on a pipe, so it does not show in the process list. The
skill teaches:

- upload, replace and delete with `aa`, and how to give the person the link;
- one link per piece of work: keep the id and replace, don't upload again;
- HTML must be one self-contained file: inline CSS and JS, images as `data:`
  URIs, no CDN or web fonts, internal navigation by `#anchor`, no
  `localStorage`;
- what the person can do with the link (view, download, forward) and that
  it expires.
