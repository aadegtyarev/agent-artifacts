# Agent Artifacts workflow

- Keep unrelated changes in separate reviewed slices. Do not include local experiments, unapproved designs, or unfinished work in a release.
- Deploy ready changes to production after they merge to `main` unless the operator asks to hold the release. A merge is not a deployment. Back up the production SQLite database and `files/`, verify the health endpoint and the affected live path, and report the deployed commit and the checks that passed. If a release is blocked, report the blocker rather than treating it as deployed.
- If a change updates the Agent Artifacts skill, update the operator's installed copy from the merged revision and verify it. Server deployment does not update other users' installed skills; point them to `docs/agent-setup.md` when they need to update.
