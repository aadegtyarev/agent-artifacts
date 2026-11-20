# agent-artifacts: setup for an AI agent

This page is written for an AI agent that a person asked to set up
agent-artifacts. There are two jobs; do the one you were asked for.

- **Connect**: the person gave you two lines, `AGENT_ARTIFACTS_URL=…` and
  `AGENT_ARTIFACTS_KEY=…`. Install the skill and save the key.
- **Deploy**: the person wants to run the service on their own server.

## Connect

You need `git`, `bash` and `curl`.

1. Fetch the skill:

   ```bash
   git clone --depth 1 https://github.com/aadegtyarev/agent-artifacts.git /tmp/agent-artifacts
   ```

2. Copy `skill/agent-artifacts` into the folder your harness loads skills from:

   | Harness | Folder |
   | --- | --- |
   | Claude Code | `~/.claude/skills/` |
   | Codex | `~/.agents/skills/` |
   | anything else | its personal skills folder, if it has one |

   ```bash
   mkdir -p ~/.claude/skills && cp -r /tmp/agent-artifacts/skill/agent-artifacts ~/.claude/skills/
   ```

   If your harness has no skills, copy the folder to `~/.agents/skills/` and add
   this line to your global instructions file (`AGENTS.md` or what your
   harness reads):
   `To share a file as a link, follow ~/.agents/skills/agent-artifacts/SKILL.md.`

3. Save the key with the skill's `aa` tool. Pass the two lines the person gave
   you on stdin, not as arguments:

   ```bash
   printf 'AGENT_ARTIFACTS_URL=%s\nAGENT_ARTIFACTS_KEY=%s\n' "<url>" "<key>" \
     | ~/.claude/skills/agent-artifacts/scripts/aa setup
   ```

   It writes `~/.config/agent-artifacts/env` with mode 600, checks the key
   against the service and prints `saved to …`. A wrong key prints
   `{"error":"unauthorized",…}` and exits with 1.

4. Check it:

   ```bash
   ~/.claude/skills/agent-artifacts/scripts/aa me
   ```

   prints the person's name, used space and quota, for example
   `{"name":"petrov","used_bytes":0,"quota_bytes":524288000,"files":0,"admin":false}`.

5. Remove `/tmp/agent-artifacts` and tell the person it is ready. Suggest a
   first try: "make a small HTML page and give me a link". Some harnesses load
   new skills only after a restart; say so if yours does.

Do not repeat the key in your answer, and do not write it anywhere except the
env file.

## Deploy

Follow [deploy.md](deploy.md) step by step. Ask the person, before you start:

- which server, and whether you may use `sudo` there;
- the domain name for the service, already pointing at that server;
- how they get TLS certificates (an existing certificate, certbot, acme.sh).

Confirm with the person before each change outside the service's own
folder: the nginx site, the firewall rules, the certificate. At the end,
create the admin key with `agent add <name> --admin`, then connect yourself
or the person's agent with it as in [Connect](#connect). From then on,
invites and limits go through `aa`; see [admin-guide.md](admin-guide.md).
