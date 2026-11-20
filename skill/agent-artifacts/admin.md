# agent-artifacts: admin key

For keys where `aa me` says `"admin": true`. `aa` is `scripts/aa` in this
skill's folder. Do what the user asks, and confirm with them before removing a
person: their key stops working and their files are deleted at once.

```bash
aa users                   # name, admin, used_bytes, files, quota_bytes, own_quota, last_upload_at
aa stats                   # totals, free disk, the common limits
aa invite petrov           # a one-time link for petrov, valid 7 days
aa invite petrov 14        # ... valid 14 days (1 to 30)
aa invites                 # open invites, with their ids
aa cancel-invite <id>      # cancel one
aa quota petrov 1000       # petrov's own quota in MB
aa quota petrov null       # back to the common quota
aa remove petrov           # revoke the key, delete the files
aa settings '{"user_quota_mb": 500, "max_total_mb": 5120, "ttl_hours": 24}'
```

`aa settings` takes any of the three; send only the ones to change. Values are
numbers of at least 1. A new lifetime applies to uploads from then on: files
already stored keep their expiry. `max_total_mb` cannot exceed what the disk
can hold, and the error says how much it can.

**Invites.** `aa invite` prints JSON with `url`. Give that link to the user to
pass on: it is shown only now, the service keeps only its hash. The name
becomes the person's name: letters, digits, `.`, `_`, `-`, not starting with
`.`. `409 name_taken` means the name already has a key or an open invite. The
person opens the link, presses a button and gets their key once, with setup
text for their own agent.

Show sizes to the user in MB. An admin cannot remove its own key.
