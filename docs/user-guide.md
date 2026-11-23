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

Press **Copy**. The page is shown only once; a reload says the invite is used.

Lost the key before you saved it? Ask the admin to remove your name and invite
you again. Until then a new invite for the same name is refused.

Opening the link does not use it up. Only the button does, so the link is safe
in a chat that builds link previews.

## 2. Give it to your agent

Paste the text into your agent. It follows
[agent-setup.md](agent-setup.md): it copies the skill into its skills folder
and saves the key to `~/.config/agent-artifacts/env`
(`%APPDATA%\agent-artifacts\env` on Windows). Some agents see a new skill
only after a restart.

## 3. Or set it up by hand

Copy the `skill/agent-artifacts` folder of this repository into your agent's
skills folder: `~/.claude/skills/` for Claude Code, `~/.agents/skills/` for
Codex and pi. Then save the key:

```bash
uv run ~/.claude/skills/agent-artifacts/scripts/artifacts_cli.py setup
```

Paste the two `AGENT_ARTIFACTS_` lines and press Ctrl-D (on Windows Ctrl-Z,
then Enter). Without uv, use `python3` (`python` on Windows) instead of `uv run`. `setup` checks the
key with the service and prints where it saved it. If the file already held a
different key, the old file is kept next to it as `env.bak-<date>-<time>`.

You can also keep the key in the environment variables `AGENT_ARTIFACTS_URL`
and `AGENT_ARTIFACTS_KEY`. Set both: together they win over the file, and one
of them alone is an error.

## 4. Check it

Ask the agent:

> Make a small HTML page with today's date and give me a link to it.

It answers with a link like `https://art.example.org/a/dqzQi3GkThdt_QSnKh4Dkg`
and says until when it works. Open it on your phone: you see the page under a
one-line header with buttons for comments, full screen and the "≡" menu:
open separately (where the service offers it), **Share…** (copy the link,
download the file) and **Settings…**: the name your comments are signed
with, the theme (auto, light or dark), the text size, larger pins,
whether whose a pin is shows by its shape as well as its colour, and the
colour of the dot that marks what needs you (red, green or yellow).
Pinch the page or an image, or tap it twice, to zoom it; the header and the
comments stay as they are (the text size in Settings makes them larger).

If the agent says there is no key, it did not run step 2 or 3: paste the text
again.

## What to ask for

- "Write the report as an HTML page and give me a link." Interactive pages
  work: tabs, buttons, small calculators.
- "Show me this diagram as a link", "share this screenshot", "zip the results
  and give me a link".
- "Update the page at the same link." The agent replaces the file, the link
  stays the same and its lifetime starts again.
- "Put the contracts and the mockups in one project." You get one link to a
  project page listing the files by section; they stay until deleted, and
  the files can link to each other. A dot on a file's line means something
  there needs you: a comment to decide on, or your review not finished.
- Comments: press the bubble, tap a spot, write and press **Add**. When
  done, **Finish review** sends the comments to the agent; then tell it
  "look at the comments". It fixes the file at the same link and answers
  each one; you accept the work or reply for another round.
- "Delete that link now."
- "How much space do I have?"

Anyone with a link can open it, without logging in. Don't share anything
secret this way: passwords, tokens, private data, internal addresses.

## Limits

| What | Limit |
| --- | --- |
| Lifetime of a link | 48 hours unless the service is set otherwise; starts again on each update; none for files in a project |
| One file | 25 MB; Markdown and text 2 MB |
| All your live files | 500 MB unless the admin gave you another limit |
| File types | `.html` `.htm` `.md` `.txt` `.pdf` `.png` `.jpg` `.jpeg` `.gif` `.webp` `.svg` `.zip` `.tar.gz` `.tgz` |

When your space is full, an upload fails with `quota_exceeded`. The agent then
deletes a file only if it clearly superseded it in the same project (an
earlier variant, a duplicate) and tells you which; otherwise it asks what to
delete. Files outside a project also go away on their own when their
lifetime ends.

A page cannot load anything from the internet: no CDN scripts, web fonts or
external images. The skill already tells the agent to put everything into one
file.

## If your key leaked

Tell the agent: "my agent-artifacts key leaked, get a new one". It asks the
service for a new key and saves it; the old one stops working at once and
your files stay. If you keep the key in environment variables, the agent shows
the new key for you to put there. If the key file cannot be written, the key
is not changed; should saving fail after the change, the new key is shown so
it is not lost.
