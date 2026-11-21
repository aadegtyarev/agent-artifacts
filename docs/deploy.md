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
curl -s http://127.0.0.1:8090/health      # prints: ok
```

The container listens on `127.0.0.1:8090`. It runs as an unprivileged user,
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

## 3. Block the container's outbound connections

Optional, recommended. The app never connects anywhere, so a firewall rule can
make sure a compromised container cannot either. Give the compose network a
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
| `AA_TTL_HOURS` | `48` | lifetime of a file after its last upload |
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
hashes, invites, settings). To copy the database safely while the service
runs:

```bash
docker compose exec app python -c "import sqlite3; sqlite3.connect('/data/meta.sqlite').backup(sqlite3.connect('/data/backup.sqlite'))"
docker compose cp app:/data/backup.sqlite ./meta-$(date +%F).sqlite
```

Files are short-lived, so a backup of the database alone keeps keys, invites
and settings.

## Upgrade

```bash
git pull
docker compose up -d --build
curl -s http://127.0.0.1:8090/health
```

Back up the database first: a new release may add columns on its first start,
and an older release cannot use the database afterwards.
