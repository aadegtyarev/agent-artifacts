# agent-artifacts

A small web service where an AI agent uploads an HTML page, a Markdown note, a
PDF, an image or an archive, and gets back a link a person can open, download
or forward. Files are deleted after a fixed lifetime (48 h by default).

Agents use it through a skill, so any harness that can run `curl` works:
Claude Code, Codex and others.

## Use

1. Run the service: [docs/deploy.md](docs/deploy.md).
2. Create a key for each agent: `agent-artifacts agent add <name>`
   (in Docker: [docs/deploy.md](docs/deploy.md#agents)).
3. Give the agent `AGENT_ARTIFACTS_URL` and `AGENT_ARTIFACTS_KEY`, and copy
   [skill/agent-artifacts](skill/agent-artifacts/SKILL.md) into its skills
   folder (`~/.claude/skills/` for Claude Code, `~/.codex/skills/` for Codex).

What the service accepts, how it shows untrusted HTML and how long it keeps
files: [docs/contract.md](docs/contract.md).

## License

MIT
