# agent-artifacts

Your AI agent makes a report, a page or a prototype and gives you a link. You
open it on any device, download it, forward it or comment on it. A single file
is gone after a set time; files the agent keeps in a project stay until deleted.

A small Python web service. Agents upload files over an HTTP API with a key;
people open the links without logging in. Untrusted HTML, SVG and Markdown
open in a sandbox: they cannot read the service, load anything from the
network or submit forms, though a link or a script can still navigate to
another site.

Agents learn the service from a skill. It comes with `artifacts_cli.py`, a
Python script with no dependencies, so any agent that runs shell commands can
use it: Claude Code, Codex, pi and others, on Linux, macOS or Windows.

```mermaid
flowchart LR
    agent["Agent with a key<br/>(Claude Code, Codex, pi)"] -- "upload, replace, delete<br/>HTTPS API" --> svc["agent-artifacts<br/>behind nginx"]
    svc -- "link" --> agent
    agent -- "link in the chat" --> person["Person"]
    person -- "opens in a browser,<br/>no login" --> svc
    admin["Admin key"] -- "invites, limits" --> svc
    svc -. "one-time invite link" .-> newcomer["New person"]
```

## What you get

- One link per piece of work: the agent updates the file and the link stays.
- HTML pages with working scripts, Markdown, text, PDF, images and archives,
  viewable on a phone.
- Files outside a project delete themselves after 48 hours by default.
- Projects: a job's files under one link (`/p/{id}`), grouped by section and
  linking to each other, kept until deleted.
  [Contract](docs/contract.md#projects).
- Comments: people pin remarks to spots on a page or an image, finish a
  review and send it to the agent; the agent takes them, fixes the file,
  replies, and waits for the next round. The person accepts the work.
  [User guide](docs/user-guide.md#what-to-ask-for),
  [contract](docs/contract.md#comments).
- Invites: a one-time link gives a colleague their own key and setup steps.
- Space per person. When it runs out, the agent deletes only files it
  clearly superseded in the same project, and asks otherwise.

## Quick start

- **Got an invite?** Open it, press the button and paste the text it gives you
  into your agent. Details: [user guide](docs/user-guide.md).
- **Running the service?** [Deploy it](docs/deploy.md), or give your agent this
  repository and [agent-setup.md](docs/agent-setup.md#deploy). Then create an
  admin key and invite people as the [admin guide](docs/admin-guide.md) shows.

## Documentation

| Page | Read it when |
| --- | --- |
| [User guide](docs/user-guide.md) | you have an invite or a key and want your agent to share links |
| [Agent setup](docs/agent-setup.md) | you are an agent asked to connect to the service or deploy it |
| [Admin guide](docs/admin-guide.md) | you invite people, check who uses how much, or change limits |
| [Deploy](docs/deploy.md) | you install, configure, back up or upgrade the service |
| [Contract](docs/contract.md) | you need the exact API, file types, limits or the security model |
| [Skill](skill/agent-artifacts/SKILL.md) | you want to see what the agent is told, or run `artifacts_cli.py` by hand |

## What it does not do

- No logins for people who open links. Knowing the link is the access, so it
  is not for secrets.
- No multi-file sites. A page is one self-contained file and cannot load
  anything from the internet; files of a project link to each other instead.
- No browsing inside archives, no versions or history of replaced files.
- No storage beyond the lifetime for files outside a project.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
AA_DATA_DIR=./data uv run agent-artifacts serve     # http://127.0.0.1:8090
```

## License

[MIT](LICENSE). Author: Alexander Degtyarev <adegtyarev.ap@gmail.com>.
