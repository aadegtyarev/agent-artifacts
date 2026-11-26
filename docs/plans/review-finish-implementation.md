# Separate review publication and completion

## Browser and evidence

- **Review drafts** opens the draft preview. The existing recipient control
  switches **Send comments** to **Publish comments** without moving the actions.
  Only the first requests edits. Neither closes the review.
- A reviewable Workspace artifact has a separate **Finish review…** action, even
  without drafts. Confirm in the same panel; show open comments and drafts that
  will be published, and accept an optional final note. Finishing never requests
  edits. Back keeps both note fields and the recipient. Maintain two 48 CSS px
  footer slots at a given viewport and text size.
- Record the displayed version and touched threads with the publication. For
  each new ordinary round, save the decision and reply boundary of each touched
  thread in the same transaction. For completion, capture the evidence boundary
  across the reviewed versions. Older rounds without a capture cannot be
  reconstructed from today's discussion. Discussion after closing is not a
  further notes update. Do not expose draft text or copy a later reply into an
  earlier round.

## One managed document

1. Notify the artifact owner of pending notes (including **Only publish**),
   independently of the edit queue. The notice contains IDs only. Recover pending
   notes after acknowledgment or restart; they stay pending until verified.
2. The owning agent reads the next captured round and its evidence, then submits
   a short summary for that round. Verify ownership and the exact stored round
   on the server. Never borrow the browser reviewer's grant. Use the current
   agent key's linked grant and the existing same-folder Docs mapping.
3. Journal each write before dispatch. Keep a single section per exact version,
   with its exact link and notes in publication order. Keep the same Doc ID. A
   lost response must be reconciled by reading the complete intended content and
   links back. A read that does not prove the new content must not replay the
   write blindly. Use the live revision as the precondition; refuse changes to
   an externally edited document instead of overwriting them.
4. Offer **Read review notes** only after the first verified write. Keep that
   same link available while another round or completion is pending; show the
   pending state separately. Google still controls access to the Doc.
5. On completion, verify queued ordinary notes, then add the recorded outcome
   at the top, keeping the per-version sections and their links. Validate final
   rows and thread citations against the closing capture. Do not turn a close
   notice into presumed acceptance or a new edit task. A review with only old
   uncaptured rounds keeps the original closure-only report path. Existing
   finalized reports remain linked and unchanged.

## Release checks

Check mobile and desktop geometry, focus return, keyboard and touch, both themes
and text sizes. Exercise normal submission and publish-only on one version, a
second exact version, no-draft completion, and completion with drafts/open
points. Verify initial pending, last verified link during an update, duplicate
and changed retry payloads, loss of create/update responses, external Doc edits,
and the same ID after completion. Verify that an independent participant can
publish a captured round but cannot finalize another agent's review or write its
Doc. Use a disposable Workspace artifact and verify retained bytes and links
before claiming live acceptance.
