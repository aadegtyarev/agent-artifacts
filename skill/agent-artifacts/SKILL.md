---
name: agent-artifacts
description: Share a file with a person as a link — an HTML page, report, prototype, Markdown note, text, PDF, image or archive — through the agent-artifacts service. Use when the result is better opened in a browser than read in the chat, when the user asks for a link, a page, a report, a prototype or a file to download, or to update a file already shared in this task.
---

# agent-artifacts

Upload a file, get a link. The person opens it in a browser, downloads it
or forwards it. Anyone with the link can open it, without
logging in. Files are deleted some time after the last upload (48 hours by
default; the service reports `expires_at`).

Everything goes through `scripts/artifacts_cli.py` in this skill's folder, a
Python script with no dependencies. Run it by its full path with `uv run`, or
with `python3` (`python` on Windows) when uv is not installed:

```bash
uv run ~/.claude/skills/agent-artifacts/scripts/artifacts_cli.py me
```

Below, `artifacts_cli.py` stands for that whole command. It finds the service
and the key in `AGENT_ARTIFACTS_URL` and `AGENT_ARTIFACTS_KEY`, or else in the
file its `setup` wrote. If it says there is no key, ask the user for the two
lines from their invite page and save them through stdin:

```bash
printf 'AGENT_ARTIFACTS_URL=%s\nAGENT_ARTIFACTS_KEY=%s\n' "<url>" "<key>" | artifacts_cli.py setup
```

## Rules

- **One link per piece of work.** Upload once, keep the `id`, and replace the
  file at that id for every later revision. Don't upload a new file for each
  edit.
- **Nothing secret.** The link is the only protection. No passwords, tokens,
  keys, private data or internal URLs in the file.
- **Allowed files:** `.html` `.htm` `.md` `.txt` `.pdf` `.png` `.jpg` `.jpeg`
  `.gif` `.webp` `.svg` `.zip` `.tar.gz` `.tgz`, up to 25 MB by default;
  Markdown and text up to 2 MB.

## Upload, replace, delete

```bash
artifacts_cli.py upload report.html                  # a real file; prints the artifact JSON
artifacts_cli.py upload report.html "Q3 report.html" # optional download name, same extension rules
artifacts_cli.py replace <id> report.html            # same link, new content; the lifetime restarts
artifacts_cli.py list                                # your live files, if you lost the id
artifacts_cli.py rm <id>                             # delete now
artifacts_cli.py me                                  # used_bytes of quota_bytes, files, admin or not
```

Upload and replace print JSON:

```json
{"id": "Vq3…", "url": "https://…/a/Vq3…", "name": "Q3 report.html", "type": "html",
 "size": 48213, "created_at": "…", "expires_at": "…"}
```

Give the person `url` and say until when it works (`expires_at`). On an error
`artifacts_cli.py` exits with 1 and prints `{"error": "<code>", "message": "…"}`:

| Code | What to do |
| --- | --- |
| `bad_request` | check the file path and name |
| `unauthorized` | the key is wrong or revoked; tell the user |
| `not_found` | the id expired or isn't yours; upload anew |
| `too_large` | shrink the file or zip it |
| `unsupported_type`, `type_mismatch` | fix the extension or the content |
| `quota_exceeded` | the user's space is used up; see below |
| `storage_full` | the service is full; tell the user |
| HTTP 429 | too many requests; wait a few seconds and retry |

## When the space runs out

Each key has its own space (500 MB by default). An upload that does not fit
gets `quota_exceeded`, with `used_bytes` and `quota_bytes` in the answer.
Don't delete anything on your own. Run `artifacts_cli.py list`, show the user each file's
name, size and `expires_at`, and ask which to delete. Delete those, then
upload again.

## If the key leaked

When the user says the key leaked or asks for a new one, run `artifacts_cli.py new-key`.
The old key stops working at once; the files stay. If the key came from the
env file, `artifacts_cli.py` saves the new one there and nothing else is needed. If it came
from `AGENT_ARTIFACTS_KEY`, `artifacts_cli.py` prints the new key: tell the user to put it
where that variable is set. Never write a key into files you upload or into
logs.

## Admin key

If `artifacts_cli.py me` says `"admin": true`, the key also manages people and limits: read
[admin.md](admin.md) when the user asks to invite someone, see usage, change
limits or remove a person.

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
