# agent-artifacts: admin key

For keys where `artifacts_cli.py me` says `"admin": true`. `artifacts_cli.py`
is the same command as in [SKILL.md](SKILL.md). Do what the user asks, and
confirm with them before removing a person: their key stops working and their
files are deleted at once.

```bash
artifacts_cli.py users                   # name, admin, used_bytes, files, quota_bytes, own_quota, last_upload_at
artifacts_cli.py stats                   # totals, free disk, the common limits
artifacts_cli.py invite petrov           # a one-time link for petrov, valid 7 days
artifacts_cli.py invite petrov 14        # ... valid 14 days (1 to 30)
artifacts_cli.py invites                 # open invites, with their ids
artifacts_cli.py cancel-invite <id>      # cancel one
artifacts_cli.py quota petrov 1000       # petrov's own quota in MB
artifacts_cli.py quota petrov null       # back to the common quota
artifacts_cli.py remove petrov           # revoke the key, delete the files
artifacts_cli.py settings '{"user_quota_mb": 500, "max_total_mb": 5120, "ttl_hours": 24}'
```

`artifacts_cli.py settings` takes any of the three; send only the ones to change. Values are
numbers of at least 1. A new lifetime applies to uploads from then on: files
already stored keep their expiry. `max_total_mb` cannot exceed what the disk
can hold, and the error says how much it can.

**Invites.** `artifacts_cli.py invite` prints JSON with `url`. Give that link to the user to
pass on: it is shown only now, the service keeps only its hash. The name
becomes the person's name: letters, digits, `.`, `_`, `-`, not starting with
`.`. `409 name_taken` means the name already has a key or an open invite. The
person opens the link, presses a button and gets their key once, with setup
text for their own agent.

Show sizes to the user in MB. An admin cannot remove its own key.
