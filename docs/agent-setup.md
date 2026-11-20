# agent-artifacts: setup for an AI agent

This page is written for an AI agent that a person asked to set up
agent-artifacts. There are two jobs; do the one you were asked for.

- **Connect**: the person gave you two lines, `AGENT_ARTIFACTS_URL=…` and
  `AGENT_ARTIFACTS_KEY=…`. Install the skill and save the key.
- **Deploy**: the person wants to run the service on their own server.

## Connect

You need `git`, and `uv` or Python 3.10 or newer. Works on Linux, macOS and
Windows. Below, `run` means `uv run` if uv is installed, otherwise `python3`
(`python` on Windows).

1. Fetch the repository into a temporary folder:

   ```bash
   git clone --depth 1 https://github.com/aadegtyarev/agent-artifacts.git <tmp>/agent-artifacts
   ```

2. Copy `skill/agent-artifacts` into the folder your harness loads personal
   skills from:

   | Harness | Folder |
   | --- | --- |
   | Claude Code | `~/.claude/skills/` |
   | Codex | `~/.agents/skills/` |
   | pi | `~/.agents/skills/` (it also reads `~/.pi/agent/skills/`; use one) |
   | anything else | its personal skills folder, if it has one |

   On Windows, `~` is your user folder (`%USERPROFILE%`).

   If your harness has no skills, copy the folder to `~/.agents/skills/` and add
   this line to your global instructions file (`AGENTS.md` or what your
   harness reads):
   `To share a file as a link, follow ~/.agents/skills/agent-artifacts/SKILL.md.`

3. Save the key. Pass the two lines the person gave you on stdin, not as
   arguments, so the key stays out of the process list:

   ```bash
   printf 'AGENT_ARTIFACTS_URL=%s\nAGENT_ARTIFACTS_KEY=%s\n' "<url>" "<key>" \
     | run <skills folder>/agent-artifacts/scripts/artifacts_cli.py setup
   ```

   In PowerShell:

   ```powershell
   "AGENT_ARTIFACTS_URL=<url>`nAGENT_ARTIFACTS_KEY=<key>" |
     run <skills folder>\agent-artifacts\scripts\artifacts_cli.py setup
   ```

   It checks the key against the service, then writes it to
   `~/.config/agent-artifacts/env` (`%APPDATA%\agent-artifacts\env` on
   Windows) and prints `saved to …`. A wrong key prints
   `{"error":"unauthorized",…}`, exits with 1 and saves nothing.

4. Check it:

   ```bash
   run <skills folder>/agent-artifacts/scripts/artifacts_cli.py me
   ```

   It prints the person's name, used space and quota, for example
   `{"name":"petrov","used_bytes":0,"quota_bytes":524288000,"files":0,"admin":false}`.

5. Delete the temporary clone and tell the person it is ready. Suggest a
   first try: "make a small HTML page and give me a link". Some harnesses load
   new skills only after a restart; say so if yours does.

Do not repeat the key in your answer, and do not write it anywhere except
through `setup`.

## Deploy

Follow [deploy.md](deploy.md) step by step. Ask the person, before you start:

- which server, and whether you may use `sudo` there;
- the domain name for the service, already pointing at that server;
- how they get TLS certificates (an existing certificate, certbot, acme.sh).

Confirm with the person before each change outside the service's own folder:
the nginx site, the firewall rules, the certificate. At the end, create the
admin key with `agent add <name> --admin`, then connect yourself or the
person's agent with it as in [Connect](#connect). From then on, invites and
limits go through `artifacts_cli.py`; see [admin-guide.md](admin-guide.md).
