---
name: agent-artifacts
description: Share a file with a person as a link — an HTML page, report, prototype, Markdown note, text, PDF, image or archive — through the agent-artifacts service. Use when the result is better opened in a browser than read in the chat, when the user asks for a link, a page, a report, a prototype or a file to download, or to update a file already shared in this task.
---

# agent-artifacts

Upload a file, get a link. The person opens it in a browser, downloads it
or forwards it. Anyone with the link can open it, without
logging in. Files are deleted some time after the last upload (48 hours by
default; the service reports `expires_at`).

Needs two environment variables: `AGENT_ARTIFACTS_URL` (for example
`https://art.example.org`) and `AGENT_ARTIFACTS_KEY`. If either is missing, tell
the user and stop.

## Rules

- **One link per piece of work.** Upload once, keep the `id`, and replace the
  file at that id for every later revision. Don't upload a new file for each
  edit.
- **Nothing secret.** The link is the only protection. No passwords, tokens,
  keys, private data or internal URLs in the file.
- **Allowed files:** `.html` `.htm` `.md` `.txt` `.pdf` `.png` `.jpg` `.jpeg`
  `.gif` `.webp` `.svg` `.zip` `.tar.gz` `.tgz`, up to 25 MB by default;
  Markdown up to 2 MB.

## Upload, replace, delete

```bash
API="$AGENT_ARTIFACTS_URL/api/v1/artifacts"
AUTH="Authorization: Bearer $AGENT_ARTIFACTS_KEY"

# Upload a real file (not stdin). `name` is optional, sets the download name
# and must end in an allowed extension.
curl -sS --fail-with-body -H "$AUTH" -F "file=@report.html" -F "name=Q3 report.html" "$API"

# Replace the file at the same link (the type may change); the lifetime restarts.
curl -sS --fail-with-body -X PUT -H "$AUTH" -F "file=@report.html" "$API/<id>"

# Your live files, if you lost the id.
curl -sS --fail-with-body -H "$AUTH" "$API"

# Delete now.
curl -sS --fail-with-body -X DELETE -H "$AUTH" "$API/<id>"
```

Upload and replace answer with JSON:

```json
{"id": "Vq3…", "url": "https://…/a/Vq3…", "name": "Q3 report.html", "type": "html",
 "size": 48213, "created_at": "…", "expires_at": "…"}
```

Give the person `url` and say until when it works (`expires_at`). Errors come
as `{"error": "<code>", "message": "…"}`:

| Code | What to do |
| --- | --- |
| `bad_request` | send `file` as multipart from a file path |
| `unauthorized` | the key is wrong or revoked; tell the user |
| `not_found` | the id expired or isn't yours; upload anew |
| `too_large` | shrink the file or zip it |
| `unsupported_type`, `type_mismatch` | fix the extension or the content |
| `storage_full` | the service is full; tell the user |

## Writing HTML that works there

The page is shown in a sandbox that blocks every network request. What is not
inside the file does not exist.

- **One self-contained file.** CSS in `<style>`, JS in `<script>`, images as
  `data:` URIs or inline `<svg>`. No CDN scripts, web fonts, external images or
  `fetch()` calls: they will not load. Use system fonts
  (`font-family: system-ui, sans-serif`). No `eval`, `new Function` or
  string `setTimeout`: they are blocked.
- **Navigation inside the page** with `#anchor` links to element ids: a table of
  contents, back-to-top, tabs that switch on `location.hash`. These work.
- **Interactive prototypes work:** scripts, buttons, dialogs, form fields
  handled in JS (a form that really submits goes nowhere). Keep the state in
  JS variables: `localStorage`, `sessionStorage`, cookies and
  IndexedDB are unavailable and throw.
- **Links to other sites** open only in a new tab: use `target="_blank"`.
- **PDF:** there is none on the server. If the person may want one, add a
  Print button (`onclick="window.print()"`): the browser's print dialog
  saves to PDF. Add `@media print` rules that hide the button and avoid
  layouts tied to the window height.

Markdown (GitHub style, tables included) is rendered in the browser; headings
get ids, so `[see](#heading-text)`
links work, and links to other sites open in a new tab. For anything richer,
write HTML.
