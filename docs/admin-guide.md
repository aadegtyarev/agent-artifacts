# Running agent-artifacts for a team

How to give people keys, see who uses how much space, and change the limits.
All of it needs an admin key. Usually you ask your agent in plain words; the
commands it runs are shown next to each request, in case you want to run them
yourself. Installing the service is in [deploy.md](deploy.md).

Below, `artifacts_cli.py` stands for the full command, for example
`uv run ~/.claude/skills/agent-artifacts/scripts/artifacts_cli.py`. Other
agents keep skills elsewhere: see the table in
[agent-setup.md](agent-setup.md#connect).

## Get an admin key

On the server, once:

```bash
docker compose exec app agent-artifacts agent add me --admin
```

It prints the key once. Connect your agent with it like any other key: give
the agent the URL, the key and [agent-setup.md](agent-setup.md#connect). When
the key is an admin key, the skill also reads its admin part,
[admin.md](../skill/agent-artifacts/admin.md).

To make an existing key admin, or take the role back:

```bash
docker compose exec app agent-artifacts agent admin codex
docker compose exec app agent-artifacts agent admin codex --revoke
docker compose exec app agent-artifacts agent list    # admins are marked "(admin)"
```

## Invite a person

Ask your agent: "invite petrov to agent-artifacts" (`artifacts_cli.py invite
petrov`). It gives you a one-time link, valid for 7 days; you can ask for 1
to 30 (`artifacts_cli.py invite petrov 14`). Send the link to the person. They
open it, press a button and get their key with setup instructions, as the
[user guide](user-guide.md) describes.

The command prints:

```json
{
  "id": "vLL1JJPG",
  "name": "petrov",
  "created_by": "me",
  "expires_at": "2026-11-27T12:55:45Z",
  "url": "https://art.example.org/join/A4EBm7VinxRRa9ITqC13diKORTV0Ev3OAgqDXzQOCeY"
}
```

The name becomes the person's name in the service. It may use letters,
digits, `.`, `_` and `-`, and may not start with a dot. A name that already
has a key or an open invite is refused.

The service keeps only a hash of the link, so a lost link cannot be shown
again. Cancel it and make a new one: "show open invites"
(`artifacts_cli.py invites`), then "cancel the invite for petrov"
(`artifacts_cli.py cancel-invite <id>`).

If someone pressed the button but lost the key, remove the name first
(`artifacts_cli.py remove petrov`), then invite again.

## See who uses what

- "Who uses agent-artifacts and how much space?" lists everyone with their
  space in use, number of files, quota and last upload (`artifacts_cli.py
  users`).
- "Show agent-artifacts stats": totals and free disk (`artifacts_cli.py
  stats`).
- "Show the agent-artifacts settings": the current limits (`artifacts_cli.py
  settings`).

The service answers in bytes and seconds; the agent turns them into MB and
hours.

## Change the limits

| Ask the agent | Command | Bounds |
| --- | --- | --- |
| "keep files for 24 hours" | `settings ttl-hours 24` | 1 to 720 hours |
| "give everyone 300 MB" | `settings user-quota-mb 300` | at least 1 MB |
| "raise the total limit to 5 GB" | `settings max-total-mb 5120` | at least 1 MB; no more than the disk holds, less a reserve for the database |
| "give petrov 1 GB" | `quota petrov 1024` | at least 1 MB |
| "put petrov back on the common limit" | `quota petrov null` | — |

The values are kept in the service's database and survive restarts. The
environment variables in [deploy.md](deploy.md#settings) only give the values a
new installation starts with.

A new lifetime applies to new uploads and replaces. Files already stored keep
their expiry, and files in a project have none: they stay until deleted, so
the space limits, not the lifetime, bound them.

## Remove a person

"Remove petrov from agent-artifacts" (`artifacts_cli.py remove petrov`). The
key stops working and the files and projects are deleted at once, so the
agent asks you to confirm first.

An admin cannot remove its own key. Another admin can, or `agent remove` on
the server.

If a person's key leaked, there is no need to remove them. They ask their
agent for a new key and keep their files.
