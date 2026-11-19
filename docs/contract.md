# agent-artifacts: contract

A small web service where an AI agent uploads a file and gets back a link a
person can open, download or pass on. Files live for a fixed time and are then
deleted. Agents use it through a skill that wraps three `curl` calls.

## Scenarios

1. An agent finishes a report as an HTML page and uploads it. It gives the
   person the link. The person opens it in a browser, sees the page with its
   styles and working anchors, and downloads it as HTML or as PDF.
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
| `.html`, `.htm` | the page, in a sandbox | PDF |
| `.md` | rendered Markdown, in a sandbox | — |
| `.txt` | plain text | — |
| `.pdf` | the browser's PDF viewer | — |
| `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | the image | — |
| `.svg` | the image, in a sandbox | — |
| `.zip`, `.tar.gz`, `.tgz` | name, size, download button | — |

Every artifact can be downloaded in its original form under its original name.

## Limits (defaults, configurable)

| Setting | Default |
| --- | --- |
| Lifetime of an artifact | 48 h from the last upload or replace |
| Size of one file | 25 MB |
| Total size of all stored files | 2 GB, then uploads get `507` |
| PDF render timeout | 20 s |

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
`type_mismatch`, `storage_full`. Uploads must carry `Content-Length`; a body
larger than the file limit is refused before it is read.

### Viewer pages (no auth)

| Path | What |
| --- | --- |
| `/a/{id}` | page with the name, expiry, the content, and buttons: Download, Download PDF (HTML only), Copy link |
| `/a/{id}/raw` | the content itself, with the sandbox headers below; opened as a page on its own, it redirects to `/a/{id}` |
| `/a/{id}/download` | the original file as an attachment under its name |
| `/a/{id}/pdf` | HTML rendered to PDF, built on first request and cached until replace |

A missing or expired id gives a `404` page saying so. Pages carry
`X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`.

## Security

**Links.** `id` is 128 random bits, URL-safe base64. It cannot be listed or
guessed; the only listing is the owner agent's own `GET /api/v1/artifacts`.

**Agent keys.** Created by `agent-artifacts agent add <name>`, which prints the
key once. Only its SHA-256 is stored. `agent remove <name>` revokes it and
deletes that agent's artifacts. A key is looked up by its hash, so it is never
compared byte by byte.

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

Markdown is rendered with raw HTML disabled and served the same way.

**PDF rendering.** Headless Chromium with every network request blocked except
`data:` and `blob:`, one render at a time, a timeout, and no access to the
service's own address. Untrusted HTML therefore cannot reach the server's
internal ports.

**Transport.** The service itself speaks plain HTTP on `127.0.0.1`. It refuses
to start when its `public_url` is `http://` and the host is not loopback, a
private-range address or a `.local` / `.lan` name: on the internet it must sit
behind a TLS reverse proxy.

## Storage and cleanup

Files under `<data>/files/<id>`, cached PDFs under `<data>/cache/<id>.pdf`,
metadata and key hashes in `<data>/meta.sqlite`. A background task deletes
expired artifacts every 10 minutes, and expired ids already answer `404` before
that.

## Deployment

Docker image with the app and Chromium, `docker compose` binding
`127.0.0.1:<port>` and a volume for `<data>`. In front, the host's reverse proxy
terminates TLS (on adbluedog: an nginx vhost with an acme.sh certificate,
`client_max_body_size` above the size limit).

## Skill

`skill/SKILL.md`, usable from Claude Code and Codex. Reads
`AGENT_ARTIFACTS_URL` and `AGENT_ARTIFACTS_KEY` from the environment. It
teaches:

- upload, replace and delete with `curl`, and how to give the person the link;
- one link per piece of work: keep the id and replace, don't upload again;
- HTML must be one self-contained file: inline CSS and JS, images as `data:`
  URIs, no CDN or web fonts, internal navigation by `#anchor`, no
  `localStorage`;
- what the person can do with the link (view, download, PDF, forward) and that
  it expires.
