# Deploy

The service runs in Docker and listens on `127.0.0.1:8090`. On the internet,
put it behind a reverse proxy that terminates TLS: the service refuses to start
with a plain `http://` public URL on a public host.

## Run

```bash
git clone https://github.com/aadegtyarev/agent-artifacts.git
cd agent-artifacts
AA_PUBLIC_URL=https://art.example.org docker compose up -d --build
```

Put `AA_PUBLIC_URL` in a `.env` file next to `compose.yaml` to keep it across
restarts. Files and the database live in the `data` volume.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AA_PUBLIC_URL` | required in compose | base of the links handed out |
| `AA_TTL_HOURS` | `48` | lifetime of an artifact after its last upload |
| `AA_MAX_FILE_MB` | `25` | size limit of one file |
| `AA_MAX_TOTAL_MB` | `2048` | size limit of all stored files |
| `AA_USER_QUOTA_MB` | `500` | size limit of one agent's live files |
| `AA_PORT` | `8090` | host port on 127.0.0.1 (compose only) |

Back up `meta.sqlite` before upgrading: a new release may add columns on its
first start, and an older release cannot use the database afterwards.

`AA_TTL_HOURS`, `AA_MAX_TOTAL_MB` and `AA_USER_QUOTA_MB` are starting values:
once an admin changes them through the API, the values in the database win.

Pass the others through `environment:` in `compose.yaml` if you change them.

## Agents

```bash
docker compose exec app agent-artifacts agent add codex     # prints the key once
docker compose exec app agent-artifacts agent list
docker compose exec app agent-artifacts agent remove codex  # also deletes its files
docker compose exec app agent-artifacts agent add me --admin # may manage people and limits
docker compose exec app agent-artifacts agent admin codex    # make an existing one admin
docker compose exec app agent-artifacts agent admin codex --revoke
```

Give the agent `AGENT_ARTIFACTS_URL` (the public URL) and `AGENT_ARTIFACTS_KEY`,
and install the skill from `skill/agent-artifacts/`.

## Reverse proxy

`deploy/nginx.conf` is a starting point for nginx. Keep `client_max_body_size`
a little above `AA_MAX_FILE_MB`. It limits each client IP to 10 requests a
second for viewing (bursts up to 40), 2 a second for the agent API (bursts up
to 10) and 20 connections; over that, nginx answers `429` without reaching the
app. This stops a single noisy client, not a distributed flood: that needs the
hosting provider or a CDN in front. Any proxy that terminates TLS and forwards to
`127.0.0.1:8090` works.

## Update

```bash
git pull && docker compose up -d --build
```
