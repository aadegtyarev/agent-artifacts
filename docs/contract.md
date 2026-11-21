# agent-artifacts: contract

A small web service where an AI agent uploads a file and gets back a link a
person can open, download or pass on. Files live for a fixed time and are then
deleted. Agents use it through a skill and its `artifacts_cli.py` command.

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
6. The agent uploads a mockup. The person and colleagues click spots on it and
   leave comments there. The person tells the agent to look at the comments;
   the agent reads them with the spot each one points at, fixes the mockup,
   replaces it at the same link and marks those comments resolved.

## Out of scope

- Accounts, logins or passwords for viewers. Knowing the link is the access.
- Multi-file sites. An HTML artifact is one self-contained file.
- Browsing the contents of an archive. Archives are download-only.
- Keeping files beyond the TTL, versions, or history of replaced files.
- Comment threads, mentions, notifications, editing a posted comment, and
  pins on PDFs, SVG and archives (those get comments without a pin).

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
| `GET /api/v1/me` | the calling agent's name, space and role | `200` + user JSON, see [Admin calls](#admin-calls) |
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
  "expires_at": "2026-11-22T10:00:00Z",
  "comments": 3,
  "open_comments": 1
}
```

Errors are `{"error": "<code>", "message": "<text>"}` with codes
`bad_request`, `unauthorized`, `not_found`, `too_large`, `unsupported_type`,
`type_mismatch`, `quota_exceeded`, `storage_full`, `too_many_comments`. `quota_exceeded` also
carries `used_bytes` and `quota_bytes`; a replace counts only the new file's
size. Uploads must carry `Content-Length`; a body
larger than the file limit is refused before it is read.

Comments on the caller's own artifact (see [Comments](#comments)):

| Call | Purpose | Answer |
| --- | --- | --- |
| `GET /api/v1/artifacts/{id}/comments` | all comments, oldest first | `200` + list of comment JSON |
| `PATCH /api/v1/artifacts/{id}/comments/{n}` | `{"resolved": true or false}` | `200` + comment JSON |
| `DELETE /api/v1/artifacts/{id}/comments/{n}` | delete one; the other numbers stay | `204` |

### Admin calls

An admin key (`agent add --admin`, or `agent admin <name>` for an existing
one) may also call these; any other key gets `403 forbidden`. Sizes are bytes
and times are seconds, as in the rest of the API.

| Call | Purpose | Answer |
| --- | --- | --- |
| `GET /api/v1/admin/users` | every agent | `200` + list of user JSON |
| `PATCH /api/v1/admin/users/{name}` | `{"quota_bytes": N or null}`: its own quota, or back to the common one | `200` + user JSON |
| `DELETE /api/v1/admin/users/{name}` | revoke the key and delete its files; not the caller's own | `204` |
| `GET /api/v1/admin/stats` | `{"users", "files", "used_bytes", "disk_free_bytes"}` | `200` |
| `GET /api/v1/admin/settings` | `{"user_quota_bytes", "max_total_bytes", "ttl_seconds"}` | `200` |
| `PATCH /api/v1/admin/settings` | any of those three | `200` + all three |
| `POST /api/v1/admin/invites` | `{"name", "ttl_seconds"}` (one hour to 30 days, default 7 days): a one-time link for a new agent | `201` + `{"id", "name", "created_by", "expires_at", "url"}`; `409 name_taken` |
| `GET /api/v1/admin/invites` | open invites, without their links | `200` + list |
| `DELETE /api/v1/admin/invites/{id}` | cancel one | `204` |

User JSON, the same in `/api/v1/me`:

```json
{"name": "petrov", "admin": false, "used_bytes": 48213, "files": 1,
 "quota_bytes": 524288000, "own_quota": false, "last_upload_at": "2026-11-20T10:00:00Z"}
```

Values are whole numbers: sizes at least 1 MB (1048576), times at least one
hour, `ttl_seconds` at most 30 days. Settings changed this way are kept in the
database and override the environment variables, which only give the starting
values. `max_total_bytes` cannot exceed the space in use plus the free disk,
less a reserve (256 MB or 5 % of the free space) for the database. A new
lifetime applies to uploads from then on.

### Viewer pages (no auth)

| Path | What |
| --- | --- |
| `/a/{id}` | page with the name, expiry, the content, and buttons: Download, Copy link |
| `/a/{id}/raw` | the content itself, with the sandbox headers below; opened as a page on its own, HTML, SVG, Markdown and text redirect to `/a/{id}`, while images and PDFs open in the browser's own viewer |
| `/a/{id}/download` | the original file as an attachment under its name |
| `GET /a/{id}/comments` | the comments, as for the owner | `200` + list of comment JSON |
| `POST /a/{id}/comments` | `{"author", "text", "anchor"}`, JSON only | `201` + comment JSON; `409 too_many_comments` |
| `PATCH /a/{id}/comments/{n}` | `{"resolved": true or false}`, JSON only | `200` + comment JSON |

A missing or expired id gives a `404` page saying so. Pages carry
`X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`.

### Invite pages (no auth)

| Path | What |
| --- | --- |
| `GET /join/{token}` | who the invite is for, until when, and a "Get my key" button; uses nothing up |
| `POST /join/{token}` | creates the agent, uses the invite up and shows the key once, in a text to paste into an agent: a link to `docs/agent-setup.md` and the two settings |

A used, cancelled or expired token gives a `404` page. If the name got a key
some other way in the meantime, the answer is `409` and the invite stays until
cancelled.

Opening the link does not use it up, because chat apps and link checkers fetch
links themselves (GET or HEAD) to build a preview. Only the POST does.

## Comments

Anyone with the link can read comments, post one and mark one resolved or
open again, the same people who can see the file. Only the owner agent can
delete a comment. Comments are deleted with the artifact.

**On the viewer page.** The header has a Comments button with the number of
open comments, on every file type. It opens a panel at the side on a
computer and at the bottom on a phone: the comments, with resolved ones
hidden behind a toggle, and a form with a name (the browser remembers it) and
the text. Where a pin is possible, the form starts with "click a spot": the
next click on the content places a numbered pin there, and the comment is
tied to it. Pins of open comments show over the content; a click on a pin
opens its comment, a click on a comment scrolls to its pin. Elsewhere the
comment is about the whole file.

| Type | Pin |
| --- | --- |
| HTML, Markdown, text | on an element of the page, see below |
| PNG, JPEG, GIF, WebP | on a point of the image |
| PDF, SVG, archives | none, the comment is about the whole file |

Comment JSON:

```json
{"n": 3, "author": "Petrov", "text": "Make this button green",
 "anchor": {"type": "element", "selector": "main > form > button.primary",
            "text": "Send", "x": 0.42, "y": 0.5, "width": 1280},
 "created_at": "2026-11-20T10:00:00Z", "resolved": false, "before_replace": false}
```

- `n` numbers the artifact's comments from 1 and is never reused.
- `author` is at most 64 characters; empty becomes `anonymous`. `text` is 1 to
  2000 characters of plain text, shown as text, never as HTML or Markdown.
- `anchor` is `null` for a comment about the whole file. On an image it is
  `{"type": "point", "x", "y"}`, the point as fractions (0 to 1) of the
  image's width and height. On a page it is `{"type": "element", "selector",
  "text", "x", "y", "width"}`: a CSS selector of the element clicked (at most
  512 characters), the start of its visible text (at most 200), the point as
  fractions of the element's box, and the window width at the time.
- `before_replace` is true when the file was replaced after the comment was
  posted, so the comment may be about the old version.
- An artifact holds at most 200 comments; the next gets `409
  too_many_comments`.

**Pins on pages.** The content runs in a sandbox the viewer page cannot look
into, so `/a/{id}/raw` appends a small inline script to HTML, and includes it
in its Markdown and text pages. The file itself and its download are not
changed. The script and the viewer page talk only through `postMessage`:

- the viewer turns pin mode on and off and sends the pins to draw: numbers,
  anchors and resolved flags, never comment texts or names;
- the script answers with the anchor of the clicked spot. In pin mode it
  takes that one click in the capture phase and cancels it, so a link or a
  button under it does not fire;
- the script reports when it is ready. If it never does, for example because
  the page's own `<meta>` CSP blocks inline scripts, the viewer offers
  comments without pins.

The viewer accepts messages only from its own iframe's window, checks their
shape and limits, and treats them as untrusted data. The page's own scripts
can forge the anchor of the next comment, but not its text, which the person
types into the viewer's form outside the sandbox.

## Security

**Links.** `id` is 128 random bits, URL-safe base64. It cannot be listed or
guessed; the only listing is the owner agent's own `GET /api/v1/artifacts`.

**Agent keys.** Created by `agent-artifacts agent add <name>`, which prints the
key once. Only its SHA-256 is stored. `agent remove <name>` revokes it and
deletes that agent's artifacts. An agent can replace its own key with
`POST /api/v1/me/key`, for example after a leak. A key is looked up by its
hash, so it is never compared byte by byte.

**Invites.** The token is 256 random bits; only its SHA-256 is stored, the
same as keys. It is used up in the same transaction that creates the agent, so
two presses cannot both get a key. The pages send `Cache-Control: no-store`,
`Referrer-Policy: no-referrer` and a CSP allowing only their own form.

**Comments.** Posting needs no key, so it is limited: the sizes and the count
above, and a request rate per address in the reverse proxy (the nginx example
allows 10 a minute with a burst of 5). The calls accept only
`Content-Type: application/json`, which another site cannot send without a
CORS preflight the service never answers, and the service sets no cookies.
Texts and names are escaped like every other value. The viewer page adds
`connect-src 'self'` to its CSP for these calls.

**The service's own pages.** Rendered from Jinja templates
(`src/agent_artifacts/templates/`) with autoescaping on, so names and other
values are escaped by default. Their CSS and the copy-button script are files
under `/static`; the viewer, invite and message pages allow scripts and styles
from the site only (`script-src 'self'; style-src 'self'`), with no inline
code.

**Untrusted HTML, SVG and Markdown.** Shown only from `/a/{id}/raw`, inside an
`<iframe sandbox>` on the viewer page, and served with:

```
Content-Security-Policy: sandbox allow-scripts allow-forms allow-modals
  allow-popups allow-popups-to-escape-sandbox allow-downloads;
  default-src 'none'; form-action 'none'; script-src 'unsafe-inline';
  style-src 'unsafe-inline';
  img-src data: blob:; font-src data:; media-src data: blob:; frame-ancestors 'self'
X-Content-Type-Options: nosniff
```

There is no `allow-same-origin`, so the content runs in an opaque origin: it
cannot read the service's pages, other artifacts, or browser storage. It also
cannot load anything from the network, which is why the skill requires
self-contained files. Scripts, forms handled by scripts, `#anchor` links and
links opening in a new tab work, so interactive prototypes do. A form that
really submits goes nowhere (`form-action 'none'`), and `localStorage` is not
there.

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
one and the nginx example turns it off, and logs errors, which carry the
request line too, only at `crit`.

**Transport.** The service itself speaks plain HTTP on `127.0.0.1`. It refuses
to start when its `public_url` is `http://` and the host is not loopback, a
private-range address or a `.local` / `.lan` name: on the internet it must sit
behind a TLS reverse proxy.

## Storage and cleanup

Files under `<data>/files/<id>`,
metadata, comments and key hashes in `<data>/meta.sqlite`. A background task deletes
expired artifacts every 10 minutes, and expired ids already answer `404` before
that.

## Deployment

Docker image with the app, `docker compose` binding
`127.0.0.1:<port>` and a volume for `<data>`. In front, the host's reverse proxy
terminates TLS, with `client_max_body_size` above the size limit
([deploy.md](deploy.md)).

## Skill

`skill/agent-artifacts/`: `SKILL.md`, `admin.md` and `scripts/artifacts_cli.py`,
a command line over the API in Python's standard library (3.10+; `uv run`
reads its inline metadata). Usable from any agent that runs shell commands.
It reads `AGENT_ARTIFACTS_URL` and `AGENT_ARTIFACTS_KEY` from the environment,
or else from the file `setup` writes with mode 600:
`~/.config/agent-artifacts/env`, `%APPDATA%\agent-artifacts\env` on Windows,
or `AGENT_ARTIFACTS_ENV`. The key never appears in a command line. Comments
get the commands `comments ID`, `resolve ID N`, `reopen ID N` and
`delete-comment ID N`; `list` shows each file's open comments. The skill
teaches:

- upload, replace and delete with `artifacts_cli.py`, and how to give the
  person the link;
- one link per piece of work: keep the id and replace, don't upload again;
- HTML must be one self-contained file: inline CSS and JS, images as `data:`
  URIs, no CDN or web fonts, internal navigation by `#anchor`, no
  `localStorage`;
- what the person can do with the link (view, download, forward, comment)
  and that it expires;
- on "look at the comments": read them with `artifacts_cli.py comments ID`,
  find each spot by its anchor's selector and text, fix the file, replace it,
  mark what was done with `resolve ID N`, and tell the person what changed
  and what was left open.
