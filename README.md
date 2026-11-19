# agent-artifacts

A small web service where an AI agent uploads an HTML page, a Markdown note, a
PDF, an image or an archive, and gets back a link a person can open, download
or forward. Files are deleted after a fixed lifetime (48 h by default).

Agents use it through a skill, so any harness that can run `curl` works:
Claude Code, Codex and others.

Status: in development. The design is in [docs/contract.md](docs/contract.md).

## License

MIT
