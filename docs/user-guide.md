# Using agent-artifacts

You got an invite link. At the end of this page your agent uploads a page and
gives you a link that opens in any browser. Any agent that runs shell commands
works: Claude Code, Codex and others. It takes a few minutes.

## Before you start

| You need | Where it comes from |
| --- | --- |
| An invite link, `https://<service>/join/…` | the person who runs the service |
| An AI agent that can run shell commands | your usual setup |
| `git`, and `uv` or Python 3.10+ | Linux, macOS or Windows |

The examples use `https://art.example.org` for the service.

## 1. Get your key

Open the invite link and press **Get my key**. The next page shows a short
text:

```
Set up agent-artifacts for me: follow https://raw.githubusercontent.com/aadegtyarev/agent-artifacts/main/docs/agent-setup.md
AGENT_ARTIFACTS_URL=https://art.example.org
AGENT_ARTIFACTS_KEY=aa_…
```

Press **Copy**. The page is shown once: after a reload the invite answers
"used". Lost the key before saving it? Ask the admin to remove your name and
invite you again: the name already has a key, so a second invite for it is
refused until then.

Opening the link does not use it up, so it is safe to send through a chat that
builds link previews. Only the button does.

## 2. Give it to your agent

Paste the text into your agent. It follows
[agent-setup.md](agent-setup.md): copies the skill into its skills folder and
saves the key to `~/.config/agent-artifacts/env`. Some agents pick up a new
skill only after a restart.

## 3. Or set it up by hand

Copy the `skill/agent-artifacts` folder of this repository into your agent's
skills folder: `~/.claude/skills/` for Claude Code, `~/.agents/skills/` for
Codex. Then save the key:

```bash
uv run ~/.claude/skills/agent-artifacts/scripts/artifacts_cli.py setup
```

Paste the two `AGENT_ARTIFACTS_` lines and press Ctrl-D (Ctrl-Z, Enter on
Windows). `python3` works instead of `uv run`. `setup` checks the key and
prints where it saved it. The key can also come from the environment
variables `AGENT_ARTIFACTS_URL` and `AGENT_ARTIFACTS_KEY`; they win over the
file.

## 4. Check it

Ask the agent:

> Make a small HTML page with today's date and give me a link to it.

It answers with a link like `https://art.example.org/a/dqzQi3GkThdt_QSnKh4Dkg`
and says until when it works. Open it on your phone: you see the page under a
one-line header with a download button and a copy-link button.

If the agent says there is no key, it did not run step 2 or 3: paste the text
again.

## What to ask for

- "Write the report as an HTML page and give me a link." Interactive pages
  work: tabs, buttons, small calculators.
- "Show me this diagram as a link", "share this screenshot", "zip the results
  and give me a link".
- "Update the page at the same link." The agent replaces the file, the link
  stays the same and its lifetime starts again.
- "Delete that link now."
- "How much space do I have?"

Anyone with a link can open it, without logging in. Don't share anything
secret this way: passwords, tokens, private data, internal addresses.

## Limits

| What | Limit |
| --- | --- |
| Lifetime of a link | set by the service, 48 hours unless changed; restarts on each update |
| One file | 25 MB; Markdown and text 2 MB |
| All your live files | 500 MB unless the admin gave you another limit |
| File types | `.html` `.htm` `.md` `.txt` `.pdf` `.png` `.jpg` `.jpeg` `.gif` `.webp` `.svg` `.zip` `.tar.gz` `.tgz` |

When your space is full, an upload fails with `quota_exceeded`. The agent then
lists your files with their sizes and asks which to delete. Files also go away
on their own when their lifetime ends.

A page cannot load anything from the internet: no CDN scripts, web fonts or
external images. The skill already tells the agent to put everything into one
file.

## If your key leaked

Tell the agent: "my agent-artifacts key leaked, get a new one". It asks the
service for a new key and saves it; the old one stops working at once and
your files stay. If you keep the key in environment variables, the agent shows
the new key for you to put there.
