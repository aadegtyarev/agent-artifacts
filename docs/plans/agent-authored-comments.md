# Agent-authored comments

The agent can mark an open question or explain a choice directly on a mockup it owns. The person can reply in that thread or approve the proposal. This is a new top-level comment, not a reply from the agent to a human comment.

## Flow to check

1. The owner agent publishes a comment with the existing element anchor format. With no anchor it comments on the whole file. The viewer shows the agent's existing star and pin style, and puts the proposal in **Need you**.
2. The person opens the comment and replies or uses the existing **✓ Accept** control. Both use the normal draft and **Finish review** flow. **Send to agent** with a reply puts the thread in the agent's existing work queue; **Only publish** does not.
3. **✓ Accept** on a proposal records approval without closing it. The star, pin and thread remain on the file, and the approved proposal is reachable under **Discussion** unless it still has work or an open question. Approval alone does not notify or queue the agent; the agent can read it in `comments`. There is no proposal-specific Reject action in this slice.
4. If a sent reply is waiting or being handled, the thread appears in **In progress**. An agent's `done`/`wrote` reply leaves an unapproved proposal in **Need you** and returns an approved one to **Discussion**. An `asked` reply is a question in **Need you**, including after approval; it does not reset approval. The project dot follows the same rules.

## Smallest change

- Add owner-authenticated creation of a single top-level comment to the artifact comments API and a matching skill CLI command. Reuse ownership, file expiry, the existing comment limit, numbering, text/anchor validation and the existing rendering of untrusted text. Other keys and the public viewer cannot create agent-authored comments.
- Represent who posted the original comment independently of the existing agent work state. A reviewer calling themselves “agent” must not get an agent pin. An agent's own new comment must not enter `take` or emit a `waiting` event on creation. Use `approve` for proposals in the thread and **Accept all**; the owner still uses `reply` to answer and the same work states for human follow-ups. Reject a closing `accept` or a `reject` draft on a proposal in both decision APIs. Keep the existing behavior and wire shape for legacy records.
- Reuse the existing star, list group, thread, decision and review components on mobile and desktop. Do not add a group, a second panel, new settings or another notification mechanism. No desktop-layout changes in this slice.

## Verify before review

- With a disposable HTML mockup, post a proposal at a known element and verify the pin targets it in both layouts. Post a whole-file proposal and verify it is labeled accordingly. The old reply command still only replies to an existing comment.
- Creation by the owner works; a different key, missing key, spoofed author, bad anchor, expired file or exceeded comment limit does not create a proposal. Existing data remains readable after migration.
- A new proposal appears in **Need you** with a star but does not show up in the agent's work queue. A person can send a reply to the agent and retrieve it through the existing flow. **Only publish** leaves an existing work state alone. Approve a proposal alone with **Send to agent** and verify no queue or watcher event, the same pin and approval in **Discussion**, and the project dot no longer needs review for it. Verify a mixed review with a reply and approval does queue the reply. Test later agent replies, mixed **Accept all**, and both individual and batch decision guards.
- Check unchanged behavior for ordinary peer approval, declining, accepting completed work, and commenting on other people's files. Check phone and desktop, both themes, with a disposable local service before showing the result. No production change without approval in chat.
