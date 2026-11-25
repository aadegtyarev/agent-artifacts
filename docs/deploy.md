# Deploying agent-artifacts

How to run the service on a Linux server with Docker, behind nginx with TLS,
and hand out the first key. The examples use `art.example.org`. An agent can
do this for you: give it this repository and
[agent-setup.md](agent-setup.md#deploy).

## Before you start

| You need | Why |
| --- | --- |
| A Linux server with Docker and the compose plugin | the service runs in one container |
| A domain name pointing at the server | links are handed out under it |
| nginx and a TLS certificate for that name | the service speaks plain HTTP on `127.0.0.1` only |
| Some free disk | 2 GB of files by default, plus the database |

The service refuses to start when its public URL is `http://` on a host
reachable from the internet. Only `https://`, or `http://` on a loopback,
private-range or `.local`/`.lan` address, passes.

## 1. Run the container

```bash
git clone https://github.com/aadegtyarev/agent-artifacts.git
cd agent-artifacts
echo "AA_PUBLIC_URL=https://art.example.org" >> .env
docker compose up -d --build
docker compose exec -T app python -m agent_artifacts.healthcheck  # prints: ok
```

The health check connects to loopback inside the container and sends the authority
from `AA_PUBLIC_URL` as its Host header, including any non-default port. This is
also the probe used by Docker. The container listens on `127.0.0.1:8090` on the
host. It runs as an unprivileged user,
with a read-only root filesystem and no capabilities. Files, the database and
uploads in progress live in the `data` volume.

## 2. Put nginx in front

[deploy/nginx.conf](../deploy/nginx.conf) is a complete vhost. Copy it, replace
`art.example.org` and the certificate paths, and reload nginx:

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/art
sudo ln -s /etc/nginx/sites-available/art /etc/nginx/sites-enabled/art
sudo nginx -t && sudo systemctl reload nginx
```

Besides proxying, the vhost:

- keeps links out of the logs. A request line holds a link, and the link is
  the access to a file, so access logs are off and error logs keep only
  `crit`. An app outage then does not show in nginx's log; watch `/health`
  instead.
- limits each client IP: 10 requests a second for pages (bursts of 40), 2 a
  second for the API (bursts of 10), 10 comment, draft or review writes a minute
  (bursts of 5), 20 connections. Above that nginx answers `429`.
- accepts uploads a little above the 25 MB file limit.

The rate limits stop a single noisy client. A distributed flood needs
protection from the hosting provider or a CDN.

On nginx older than 1.25.1, replace `http2 on;` with `listen 443 ssl http2;`
as the comment in the file says.

### The raw origin (optional)

"Open separately" in the viewer's menu opens a file on a host of its own,
[the raw origin](contract.md#the-raw-origin). To offer it:

1. Point a second name at the same server, for example
   `raw.art.example.org`. A subdomain is same-site with the main name: if the
   parent domain hosts apps with logins, use a host on another registrable
   domain instead.
2. Get a certificate for it, the same way as for the main name.
3. Uncomment the raw origin server block at the end of
   [deploy/nginx.conf](../deploy/nginx.conf), set its name and certificate
   paths, and add the name to the port 80 block's `server_name` for the
   redirect to HTTPS. Reload nginx.
4. Add `AA_RAW_URL=https://raw.art.example.org` to `.env` and run
   `docker compose up -d`.

### Workspace browser sign-in (optional)

By default people open links anonymously. To require a verified account in one
Google Workspace hosted domain, configure all three settings together:
`AA_GOOGLE_CLIENT_ID`, `AA_GOOGLE_CLIENT_SECRET`, and
`AA_GOOGLE_HOSTED_DOMAIN` (the exact lowercase hosted domain, not an email
suffix). Supply the secret through your deployment's secret management, not a
repository file. Both `AA_PUBLIC_URL` and optional `AA_RAW_URL` must be HTTPS
origins. Register `https://art.example.org/auth/callback` as an authorized
redirect URI in the Google OAuth web client; if the raw origin is enabled, also
register `https://raw.art.example.org/auth/callback`. Substitute your actual
configured origins. The container needs outbound HTTPS to Google.

In Compose, pass the three values into the container using an override or your
secret-management integration; merely adding them to `.env` does not pass them
through automatically. In Workspace mode configure the proxy to preserve the
configured Host authority (including a non-default port), reject other hosts,
and keep request URLs and OAuth callback query strings out of logs. On a
failed callback the app logs only a fixed reason and the time the server
spent handling it, never OAuth parameters, cookies, or account details.

If using the optional raw-origin nginx block, it must also proxy exactly the
three authentication endpoints below to the app, in addition to the existing
raw path. Add these locations inside that raw server block:

```nginx
location = /auth/login {
    limit_except GET { deny all; }
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:8090;
}
location = /auth/callback {
    limit_except GET { deny all; }
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:8090;
}
location = /auth/logout {
    limit_except POST { deny all; }
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:8090;
}
```

These examples assume the default HTTPS port; for a non-default port preserve
that port in the Host header. The two origins use separate host-only sessions,
so opening a raw file separately may require another sign-in. Sign-out on one
host does not sign out the other. Workspace membership does not grant admin
rights: agent and admin APIs still require their existing bearer keys, and
invite claims require a Workspace browser session. External reviewers are not
supported. The Workspace session database is `workspace-auth.sqlite` on the
data volume; losing it signs users out. Changing the configured hosted domain or
OAuth client invalidates existing sessions and pending logins for the new policy.

### Browser accounts (optional)

Set `AA_ACCOUNTS=1` to enable account pages. With the supplied Compose file,
set it in the installation's `.env` and recreate the app. The public origin
must be HTTPS; forward the canonical Host header and preserve the browser's
Origin on POST.
With Workspace configured, Google handles browser sign-in; otherwise a
connected browser can issue a one-time device code. Agent setup codes are
returned separately from the claim page address, and both types of code go
in form bodies, not URLs. Do not log request bodies or cookie headers. The
per-caller pairing limit uses the ASGI client address, not a header parsed by
the app; trust proxy headers only from your configured reverse proxy.
Account ownership, device sessions, and hashes of device, setup and recovery
codes live in `meta.sqlite`. Include it in the same backups as the files. On
non-Google installations, save the recovery code outside the current browser
before signing out the last device.

Disabling `AA_ACCOUNTS` hides the account pages but preserves the visibility
and cleanup rules for pending deletions. Do not run an older build that does
not understand pending deletion markers against a database that still has
any: it would expose those files again. Back up `meta.sqlite` and `files/`
together and use a build that still honors these markers for a rollback.

## 3. Block the container's outbound connections

Optional, recommended in public mode. In public mode the app never connects
anywhere, so a firewall rule can make sure a compromised container cannot either.
Workspace sign-in needs outbound HTTPS to Google's token and certificate endpoints;
do not use the blanket outbound block below for a Workspace deployment. Give the compose network a
fixed bridge name in `compose.override.yaml` next to `compose.yaml`:

```yaml
networks:
  default:
    driver_opts:
      com.docker.network.bridge.name: br-artifacts
```

Recreate the network with `docker compose down && docker compose up -d`, then
drop new connections from that bridge. With plain iptables:

```bash
sudo iptables -I DOCKER-USER -i br-artifacts -m conntrack --ctstate NEW -j DROP
```

With ufw, put the same rule into `/etc/ufw/after.rules` so it survives
reboots, and keep the container away from services on the host:

```
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -i br-artifacts -m conntrack --ctstate NEW -j DROP
COMMIT
```

```bash
sudo ufw insert 1 deny in on br-artifacts
sudo ufw reload
```

Replies to incoming requests still pass: they belong to connections nginx
opened.

## 4. Create the first admin key

```bash
docker compose exec app agent-artifacts agent add me --admin
```

It prints the key once. Connect your agent with it as
[agent-setup.md](agent-setup.md#connect) shows; from then on your agent
invites people and manages limits: [admin guide](admin-guide.md).

Keys without invites, for your own agents or scripts:

```bash
docker compose exec app agent-artifacts agent add ci-bot
docker compose exec app agent-artifacts agent list
docker compose exec app agent-artifacts agent remove ci-bot   # also deletes its files
```

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `AA_PUBLIC_URL` | required by `compose.yaml` | base of the links handed out |
| `AA_RAW_URL` | empty | [the raw origin](#the-raw-origin-optional) for "Open separately"; empty hides that menu item |
| `AA_TTL_HOURS` | `48` | lifetime of a file after its last upload; files in a project have none |
| `AA_MAX_FILE_MB` | `25` | size limit of one file |
| `AA_MAX_TOTAL_MB` | `2048` | size limit of all stored files |
| `AA_USER_QUOTA_MB` | `500` | size limit of one key's live files |
| `AA_PORT` | `8090` | host port on `127.0.0.1` |

`compose.yaml` passes `AA_PUBLIC_URL` and `AA_RAW_URL` into the container.
To change another one, add it under `environment:` in `compose.override.yaml`:

```yaml
services:
  app:
    environment:
      AA_MAX_FILE_MB: "50"
```

Raise `client_max_body_size` in nginx to match a bigger file limit.

`AA_TTL_HOURS`, `AA_MAX_TOTAL_MB` and `AA_USER_QUOTA_MB` are starting values.
Once an admin changes one through the API, the database value wins.

## Back up

Everything lives in the `data` volume: `files/` and `meta.sqlite` (keys as
hashes, invites, settings, projects, and a row for every file with its
comments). Back up both. While the service runs, copy the database first, then the files:

```bash
docker compose exec app python -c "import sqlite3; sqlite3.connect('/data/meta.sqlite').backup(sqlite3.connect('/data/backup.sqlite'))"
docker compose cp app:/data/backup.sqlite ./meta-$(date +%F).sqlite
docker compose cp app:/data/files ./files-$(date +%F)
```

In that order, after a restore, a file uploaded between the two copies has
no row and is removed as a leftover once it is an hour old; a file deleted
between them is a lost file, below. A snapshot of the whole volume, or a copy
taken with the service stopped, has neither.

To restore, put `meta.sqlite` and `files/` back into the volume together,
with the service stopped.

If only the database comes back, every file whose own file is missing (files
in projects, which never expire, included) answers 404. On start and every
ten minutes after, the service removes such rows, with their comments, and
logs the owner and the file name of each. Keys, invites and settings stay.
With more than 5 files missing at once it takes the `files/` folder itself
for missing (a restore in progress, an unmounted volume), removes none and
logs the error `N of M files are missing; none removed: check the files
folder`. Put `files/` back.

## Upgrade

```bash
git pull
docker compose up -d --build
docker compose exec -T app python -m agent_artifacts.healthcheck
```

Back up the database first: a new release migrates it on its first start
(numbered versions, `PRAGMA user_version`), and an older release cannot use
it afterwards. A newer database is left untouched and logged, but the old
code's queries fail on it.

To roll back across the migration to version 2 (comments without
`for_agent` and `read_at`) without losing what was written since, add the
columns back before starting the older image:

```bash
docker compose exec app python -c "import sqlite3; d=sqlite3.connect('/data/meta.sqlite'); d.executescript('ALTER TABLE comments ADD COLUMN read_at REAL; ALTER TABLE comments ADD COLUMN for_agent INTEGER NOT NULL DEFAULT 0; PRAGMA user_version = 1;')"
```

Restoring the backup is the fallback; it loses what was written after it.
