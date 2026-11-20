# agent-artifacts: admin key

Read this only when `GET $AGENT_ARTIFACTS_URL/api/v1/me` answers
`"admin": true`. An admin key also manages people and limits. Do what the user
asks, and confirm with them before removing a person: their key stops working
and their files are deleted at once.

```bash
ADMIN="$AGENT_ARTIFACTS_URL/api/v1/admin"
AUTH="Authorization: Bearer $AGENT_ARTIFACTS_KEY"
JSON="Content-Type: application/json"

# People: name, admin, used_bytes, files, quota_bytes, own_quota, last_upload_at.
curl -sS --fail-with-body -H "$AUTH" "$ADMIN/users"

# Totals, free disk and the common limits.
curl -sS --fail-with-body -H "$AUTH" "$ADMIN/stats"

# Change the common limits; send only the ones to change.
curl -sS --fail-with-body -X PATCH -H "$AUTH" -H "$JSON" \
  -d '{"user_quota_mb": 500, "max_total_mb": 5120, "ttl_hours": 24}' "$ADMIN/settings"

# One person's own quota in MB; null returns them to the common one.
curl -sS --fail-with-body -X PATCH -H "$AUTH" -H "$JSON" \
  -d '{"quota_mb": 1000}' "$ADMIN/users/<name>"

# Remove a person: key revoked, files deleted.
curl -sS --fail-with-body -X DELETE -H "$AUTH" "$ADMIN/users/<name>"
```

Show sizes to the user in MB. A new lifetime applies to uploads from then on;
files already stored keep their expiry. `max_total_mb` cannot exceed what the
disk can hold (`400 bad_request` says how much). An admin cannot remove its own
key.
