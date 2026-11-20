# Running agent-artifacts for a team

How to give people keys, see who uses how much space, and change the limits.
All of it goes through an admin key: ask your agent, which uses the skill's
`artifacts_cli.py` tool, or run `artifacts_cli.py` yourself. Installing the service is in
[deploy.md](deploy.md).

## Get an admin key

On the server, once:

```bash
docker compose exec app agent-artifacts agent add me --admin
```

It prints the key once. Connect your agent with it like any other key: give
the agent the URL, the key and [agent-setup.md](agent-setup.md#connect). The
skill sees `"admin": true` in `artifacts_cli.py me` and reads its admin part,
[skill/agent-artifacts/admin.md](../skill/agent-artifacts/admin.md).

To make an existing key admin, or take the role back:

```bash
docker compose exec app agent-artifacts agent admin codex
docker compose exec app agent-artifacts agent admin codex --revoke
docker compose exec app agent-artifacts agent list    # admins are marked "(admin)"
```

## Invite a person

Ask your agent: "invite petrov to agent-artifacts". It creates a one-time link,
valid 7 days (1 to 30 if you say so), and gives it to you. Send the link to the
person. They open it, press a button and get their key with setup
instructions: [user guide](user-guide.md).

The name you give becomes the person's name in the service: letters, digits,
`.`, `_`, `-`. A name that already has a key or an open invite is refused.

Each link works once. The service keeps only a hash of it, so a lost link
cannot be shown again: cancel it and make a new one. "Show open invites" and
"cancel the invite for petrov" do that (`artifacts_cli.py invites`, `artifacts_cli.py cancel-invite <id>`).

The same by hand, with `artifacts_cli.py` from the skill folder:

```bash
artifacts_cli.py invite petrov
```

```json
{
  "id": "vLL1JJPG",
  "name": "petrov",
  "created_by": "me",
  "expires_at": "2026-11-27T12:55:45Z",
  "url": "https://art.example.org/join/A4EBm7VinxRRa9ITqC13diKORTV0Ev3OAgqDXzQOCeY"
}
```

## See who uses what

"Who uses agent-artifacts and how much space?" lists everyone with their used
space, number of files, quota and last upload (`artifacts_cli.py users`).

"Show agent-artifacts stats" gives the totals, free disk and current limits
(`artifacts_cli.py stats`):

```json
{
  "users": 1,
  "files": 1,
  "used_bytes": 11,
  "disk_free_bytes": 6425055232,
  "limits": {"user_quota_mb": 500.0, "max_total_mb": 2048.0, "ttl_hours": 48.0}
}
```

## Change the limits

| Ask the agent | Changes | Bounds |
| --- | --- | --- |
| "keep files for 24 hours" | lifetime of new uploads: `artifacts_cli.py settings '{"ttl_hours": 24}'` | 1 to 720 hours |
| "give everyone 300 MB" | the common per-person space: `user_quota_mb` | at least 1 MB |
| "raise the total limit to 5 GB" | all stored files together: `max_total_mb` | at least 1 MB, at most what the disk can hold less a reserve for the database |
| "give petrov 1 GB" / "put petrov back on the common limit" | one person's own space: `artifacts_cli.py quota petrov 1024` / `artifacts_cli.py quota petrov null` | at least 1 MB |

These are kept in the service's database and survive restarts; the
environment variables in [deploy.md](deploy.md#settings) only give the values a
new installation starts with. A new lifetime applies to uploads from then on:
files already stored keep their expiry.

## Remove a person

"Remove petrov from agent-artifacts" (`artifacts_cli.py remove petrov`). Their key stops working and their files
are deleted at once, so the agent asks you to confirm first. An admin cannot
remove its own key; another admin, or `agent remove` on the server, can.

If a person only leaked their key, there is no need to remove them: they ask
their agent for a new key and keep their files.
