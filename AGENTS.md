# Agent Artifacts workflow

- Keep unrelated changes in separate reviewed slices. Do not include local experiments, unapproved designs, or unfinished work in a release.
- Deploy ready changes to production after they merge to `main` unless the operator asks to hold the release. A merge is not a deployment. Back up the production SQLite database and `files/`, verify the health endpoint and the affected live path, and report the deployed commit and the checks that passed. If a release is blocked, report the blocker rather than treating it as deployed.
- If a change updates the Agent Artifacts skill, update the operator's installed copy from the merged revision and verify it. Server deployment does not update other users' installed skills; point them to `docs/agent-setup.md` when they need to update.

## Interface changes

- Before a UI change or mockup, read `docs/design/README.md`, then the relevant parts of `tokens.md`, `components.md`, and `tone-of-voice.md` in that folder. For pins or icons, read `pins.md` or `icons.md`. Check interaction and access rules in `docs/contract.md`. Planned screens and mockups do not override the documented design.
- Find the closest shipped component first. Reuse its CSS, template or macro, tokens, and interaction where possible. Do not create a second control style for the same role or guess at a universal size. Distinguish visible size, hit target, and Text scaling. Use the actual product styles in self-contained mockups.
- Check affected states in a browser at a narrow and a desktop width, in light and dark, and with the default and enlarged Text setting. Verify keyboard focus and operation, not just screenshots. Prefer direct action plus Undo for reversible changes; reserve confirmations for consequential exceptions.
- Update the relevant design reference in the same change as an approved UI change. Run `python3 skill/agent-artifacts/scripts/design_check.py .` and `uv run --locked pytest -q tests/test_design.py`. These checks do not verify geometry or interaction. Show the verified preview and get the operator's approval before publishing a change to the live interface.
- In review, compare changed screens against the cited design components and browser checks. Check control sizes, Text scaling, focus and placement; passing the source guard alone is not enough. Add browser assertions for reusable geometry and interaction rules where the UI can measure them. For the account, `tests/browser/account-devices.cjs` checks compact controls, hit targets and the Undo toast in both entry modes.
