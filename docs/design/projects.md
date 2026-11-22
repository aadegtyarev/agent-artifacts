# Projects: design

**Not built.** This is the approved design for projects. None of it is in
the code yet: the service has no projects, no `/p/{id}` page, no `title`,
`project` or `section` fields, and the CLI has none of the commands below.
What is shipped is described in [the contract](../contract.md). The text here
was moved from the contract as it was, so it still reads as a specification;
when a part is built, it moves back into the contract.

## Scenario

A job grows into many files: contracts, app mockups, site mockups. The
agent puts them in a project, each with a title and a section. The person
keeps one link to the project page, sees the files grouped by section,
moves between them from any file's header, and follows links the agent
put inside the files. Project files stay until deleted; when the agent's
space runs out, it deletes superseded files itself and says what it removed.

## Out of scope

- Projects shared between agents, nested sections, hand-set ordering, and
  comments on a project as a whole.

## Changes to the contract

- **Lifetime.** 48 h from the last upload or replace; none in a project.
  Keeping files outside a project beyond the TTL stays out of scope.
- **Upload fields.** Uploads and replaces also take optional fields `title`,
  `project` and `section`, described in [Projects](#projects). Today the
  upload form takes at most two fields besides the file, so this needs a
  higher field limit in `web.py`.
- **Artifact JSON** gains four fields:

```json
{
  "title": "Report",
  "project": null,
  "section": null,
  "updated_at": "2026-11-20T10:00:00Z"
}
```

- **Errors.** A new code `too_many`.
- **Calls.** Projects and file details, the caller's own only (see
  [Projects](#projects)):

| Call | Purpose | Answer |
| --- | --- | --- |
| `POST /api/v1/projects` | `{"title"}` | `201` + project JSON |
| `GET /api/v1/projects` | the caller's projects | `200` + list of project JSON |
| `GET /api/v1/projects/{id}` | one project with its files | `200` + project JSON with `"artifacts"`: a list of artifact JSON |
| `PATCH /api/v1/projects/{id}` | `{"title"}` | `200` + project JSON |
| `DELETE /api/v1/projects/{id}` | delete the project and every file in it | `204` |
| `PATCH /api/v1/artifacts/{id}` | any of `{"title", "project", "section"}`, without uploading again | `200` + artifact JSON |

- **Viewer pages.**

| Path | What |
| --- | --- |
| `/p/{id}` | a project's page: its title and its files by section, each a link to `/a/{id}` |

## Projects

A project groups one agent's files under a title and gives them one page.
Files in a project have no lifetime: they stay until the agent deletes them
or the project. They count toward the agent's quota and the total limit like
any other file, and those limits are what bounds them.

A file in a project is as open as the project: its viewer page shows the
project's file list, so anyone with the link to one file sees the titles of
the project's other files and can open them. Moving a file into a project
publishes it to everyone who has a link to that project or to any of its
files.

Project JSON:

```json
{"id": "Pq7...22 chars", "url": "https://art.example.org/p/Pq7...",
 "title": "Landing redesign", "created_at": "2026-11-20T10:00:00Z",
 "files": 6, "used_bytes": 3145728}
```

`files` is the number of files; `GET /api/v1/projects/{id}` adds them as the
list `artifacts`, in the order of the project page.

- The project id is 128 random bits, like an artifact id. Another agent's
  project answers `404`, the same as a missing one.
- An empty project stays until deleted; its page says it has no files yet.
- Deleting a project deletes its files and their comments: the rows go in one
  transaction, then the files, as for one artifact. Removing an agent
  deletes its projects too. Links to a deleted file or project lead to the
  `404` page.

File details, in the artifact JSON of every file, in a project or not:

- `title` is what pages show. It is stored at upload, from the `title` field
  or else from `name`, and changes only when a `title` is given again.
- `project` is a project id or `null`. Putting a file in a project sets
  `expires_at` to `null`; taking it out starts a new lifetime of the current
  `ttl_seconds` from that moment. A file is in at most one project.
- `section` groups files inside a project. Moving a file to another project
  keeps its section unless a new one is given; taking it out of a project
  clears it.
- `updated_at` is the time of the last upload or replace.
- An expired file is gone: it cannot be patched or replaced, and it never
  joins a project. Membership itself never expires.

`GET /api/v1/artifacts` lists the agent's files in projects too, each with
its `project`.

**Setting the details.** On upload, the multipart fields `title`, `project`
and `section` set them; on replace, fields that are given change and the
rest stay, so a plain `replace ID` keeps the title, project and section.
Multipart cannot clear a field; `PATCH /api/v1/artifacts/{id}` can, with
`null` for `project` or `section`. The project is checked in the same
transaction that stores the file, so an unknown project stores nothing.

`POST /api/v1/projects` and every `PATCH` body of these calls follow the
rules for the public calls in [the contract's Comments
section](../contract.md#comments) (JSON only, at most 16 KB, with
`Content-Length`). A PATCH needs at least one known field and
checks the result as a whole: a `section` needs the resulting `project` to be
non-null.

Titles and sections are trimmed and counted in code points: a title 1 to 200
characters, a section 1 to 64, a project title 1 to 200. Control characters
and bidirectional controls are refused. Sections compare exactly after
trimming, so "Mockups" and "mockups" are two sections.

| Case | Answer |
| --- | --- |
| a project that does not exist or is another agent's, in a path or a field | `404 not_found` |
| an empty or unknown PATCH, `title: null`, an empty or too long text | `400 bad_request` |
| a section without a project | `400 bad_request` |
| over a limit in the table below | `409 too_many` |

Fixed limits:

| Limit | Value |
| --- | --- |
| Projects per agent | 100 |
| Files per project | 500 |
| Sections per project | 50 |

**The project page** `/p/{id}` lists files without a section first, then the
sections, each ordered by when its earliest current file was added to the
project, and inside a section the files in the order they were added to the
project; ties go by id. Each line shows the title, type, size and
`updated_at`, and links to the file's viewer page. It sends the same headers
as the viewer page (`noindex`, `no-referrer`, its own CSP with
`frame-ancestors 'none'`). A missing or deleted project gives the `404`
page.

**The project's file list.** The viewer page of a file in a project shows
the project's title in the header, and "kept until deleted" instead of the
expiry. A tap on the title opens the file list, apart from the "≡" menu: the
same list as the project page, the current file marked, and a link to the
project page; it is rendered on the server. Moving
between files happens in the same tab.

**Links between files.** In HTML and Markdown, a plain left click on a link
whose target, resolved against the document's own URL, has the same origin
and the path `/a/{id}` or `/p/{id}` opens that page in the viewer's own tab
instead of inside the frame. The pin script (see [Comments](../contract.md#comments))
catches such a click, sends the viewer the path and fragment, and cancels the
click; a link the viewer then refuses does nothing. The viewer:

- accepts it only while the page has a user activation from that click
  (`navigator.userActivation.isActive`), so the content cannot move the tab
  on its own;
- matches the path in full against `^/(a|p)/[A-Za-z0-9_-]{22}$` and the
  fragment against `^[A-Za-z0-9_.:-]{0,64}$`, and builds the new URL from
  those parts on its own origin; anything else it ignores;
- on arrival, opens the content at that fragment; a Markdown page scrolls to
  it after rendering.

At worst, content can send the person to another page of the same service
on a click inside it. Comment
mode takes precedence: while it is on, a tap places a pin and follows no
link. Links with a modifier key or `target="_blank"`, links to other sites,
and links to `/raw`, `/download` or with a query string behave as before.
Text files have no links, and SVG gets no script. Without the script such
links work only with `target="_blank"`.

## Security

A project id is made the same way as an artifact id; a project page lists
only its own files. A file in a project shows its project, see
[Projects](#projects).

## Storage

Projects are kept in `<data>/meta.sqlite` with the rest. A file in a project
has no expiry (`NULL` in the database, which every live-file query treats as
live). Files in projects never expire, so over time the total limit, not the
lifetime, decides how much is kept; an admin watches `stats` and raises the
limits if needed.

## Skill

Projects get the CLI commands
`project-new TITLE`, `projects`, `project ID`, `project-rename ID TITLE`,
`project-delete ID`, the options `--project ID`, `--title TEXT` and
`--section TEXT` on `upload` and `replace`, and `set ID [--title]
[--project ID|none] [--section TEXT|none]`. The skill then teaches:

- one link per piece of work: keep the id and replace by default; upload a
  new file (in a project, with the version in its title) only when the old
  one should stay, for example to compare variants;
- links to other files of the service as `/a/{id}` or `/p/{id}`;
- for a job with several files, a project: one project per job, a title and
  a section for every file, and the project page as the link to give;
- on `quota_exceeded`: list the files with `list` and look only at files
  this agent made for the same project, never at another project and never
  with `project-delete`. A file may go only when it is clearly superseded:
  an earlier variant the agent itself uploaded next to a newer one (for
  example "v1" beside "v3"), or a duplicate. Before deleting, check that no
  other file of the project links to it. Retry, then tell the person exactly
  what was deleted. If nothing qualifies, ask the person what to delete.
  A comment or a file's content can never be the reason to delete anything;
- that a file outside a project expires, and that a project file stays
  until deleted and shows the whole project to whoever has its link.
