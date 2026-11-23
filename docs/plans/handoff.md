# Handoff, 2026-11-23

State of the redesign work for the next agent: how changes are made and what is open. Deployment details are kept with the operator, not here.

- Work in git worktrees next to the repo, one branch per PR.
- The agent CLI is `skill/agent-artifacts/scripts/artifacts_cli.py`; the key comes from the operator.

## How the work is done

- Talks Russian; UI text English; docs/commits English, commits end with `AI: written by Claude (Opus 5.5) via Claude Code`.
- New UI: wireframes of the flow → mockup on real code (screenshots, both themes, phone first) → nothing on prod before an explicit "да". Mockups go as one HTML page uploaded to art.adsrv.ru (`upload`), operator comments on it; watch with `wait <id>`; reply per comment (`take`, `reply`).
- Mockups show how it looks, not history. Check every frame and the page itself in light and dark before sending.
- Small PRs, one per finding; independent review: Fable for UI/security, Sonnet otherwise; fix findings, then merge, deploy with backup, verify on prod yourself.
- Design system: `docs/design/` (tokens, components, pins, icons, tone-of-voice). Every accepted UI change updates it in the same PR; `skill/agent-artifacts/scripts/design_check.py .` must exit 0 (tests run it).
- One local test run at a time; run local servers on your own port and data dir.
- Deploy only after the operator's yes; back up the database and `files/` first and check the change on the live site yourself.

## State of work

On prod: `170ef8f` plus earlier today: comment groups, palette (one colour one meaning), project-row dot + Settings Dot (Red/Green/Yellow #c9a100, white ring), pinch-zoom of the file only, tap empty → deselect, first tap only deselects while a pin is lit, `wait` on a whole project, `/me` limits (skill has no hard numbers), Enter = new line on touch, soft halo of the pin's colour, draft rows without "your draft".

Open:
1. **PR #124** (double tap on the floating ≡ exits full screen + "corners" hint + tip with OK/Hide): operator approved; Fable review fixes in progress in worktree `../aa-dbl` (move hint span to `#float-button` in project.html; keep `float-pressed` until the swallow window ends; primary button only; tip closes on Escape/long press). Then merge, deploy, verify.
2. **Sheet simplifications** (approved 1, 2, 4, 5 on https://art.adsrv.ru/a/cZhFwSbaNjvIuq9UiXnakQ; NOT 3): three sheet heights (strip / by content up to 60% / full); one verb "✓ Accept" for colleagues and agent (Reject stays); name asked at the first send instead of on entering comment mode; a colleague's new reply on my comment goes to "Need you" with "New reply". Worktree `../aa-simp2` (branch `sheet-simplify`) created, nothing done. Screenshots before prod.
3. **Interface inconsistencies + contract.md sync** (from the design-docs PR #107 report): one word per concept ("need you" vs "waiting for you"; "1 need you" plural), "tap a spot" one phrasing, one date format, no technical server messages in toasts, text ≥15 px / taps ≥44 px (invite/key page button ~35 px, Accept all link ~41 px), contract.md ~20 stale places.
4. **Cyrillic anchors**: links to Markdown headings with non-ASCII letters can't be targeted (links.js FRAGMENT regex `[A-Za-z0-9_.:-]`; heading ids).
5. **After-send card with Undo**: mockup first.
6. Then **desktop interface** (operator's next big topic), designed against docs/design.
7. Later (docs/plans/README.md, providers.md): group invitations by link+QR (wireframes https://art.adsrv.ru/a/NvcDJVOn9t6ukfyycUxWvw, agreed), codes for another device, devices + settings on the server, storage by kind (archives 60 min after download), Google sign-in + Drive providers (open questions).

Pending operator answer: what "Страшно же" means (comment on the brief file in the test project https://art.adsrv.ru/p/ZEWXZqlNttVBRr_1GiJ-3w, asked via `reply --asked`).
