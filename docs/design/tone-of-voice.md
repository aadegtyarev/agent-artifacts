# Tone of voice

How the service's own interface speaks. The examples show current copy from
the templates, `pages.py`, and the viewer, comments and project scripts.
Reuse these words for the same action rather than writing a second label.

## Who we talk to

People who opened a link an agent shared: colleagues and clients reviewing a
page, a mockup or a document, mostly on a phone, often in a chat app's
browser. They know the file, not the service. They do not know what an
artifact, a draft API or an agent state is.

The interface is in English. The agent writes to people in chat in their
own language, replies included ([SKILL.md](../../skill/agent-artifacts/SKILL.md)).

## Voice

1. Plain words people say out loud: "Finish review", "Need you", "Show",
   "Copy link". No system terms (artifact, anchor, resolve, author key).
2. Short. A status is two or three words; a toast is one sentence, two at
   most.
3. Sentence case everywhere: "Finish review", "Exit full screen". Section
   titles are uppercased by CSS, not in the text.
4. Numbers as digits, joined to their word by " · " in titles ("Comments · 4")
   and as "N word" in running text ("2 drafts").
5. Say the state, not the mechanism: "in progress", not "with the agent";
   "Asks you", not "awaiting input".
6. No exclamation marks or emoji. Use ✓ and ✕ for review decisions and ↩ for a draft reply.
7. An error says what happened, then what to do: "Could not load the new
   file. Try again."

## Glossary

"Avoid" lists words that never appear in the interface's text: no string or
text node in the UI code may contain them (`design_check.py` from the skill
checks it, see [README.md](README.md#keeping-it-true)). "Not for this" lists
words that are wrong for the concept but fine elsewhere ("Done" closes a
dialog; "address" is the network address in the 429 error).

| Concept | The UI says | Avoid | Not for this |
| --- | --- | --- | --- |
| The file shown | file ("Download file", "About the whole file", "The file was updated") | artifact | document, upload |
| A group of files | project ("Project files", "Project page ›") | workspace | folder, collection |
| The page address | link ("Copy link", "Link copied.") | URL | address |
| A remark on the file | comment ("Comment", "3 comments") | annotation | note, feedback |
| A comment not published yet | draft ("your draft", "New draft", "2 drafts") | unsent comment | pending |
| Publishing all drafts at once | **Review drafts**, **Send comments** or **Publish comments**, "Review published", "Review sent to the agent" | send all | submit, commit |
| Waiting for this person's decision | need you ("2 need you", "Need you") | action required, awaiting input | pending, to do |
| Sent and not answered yet | in progress ("1 in progress", "In progress") | with the agent, queued | |
| The agent's finished work, taken | accept ("✓ Accept", "✓ Accepted") | resolve, resolved | close, done |
| A colleague's comment let through | accept ("✓ Accept", "✓ Approved by Anna") | | confirm, allow |
| A colleague's comment stopped | reject ("✕ Reject", "rejected") | decline, dismiss | delete |
| Asking the agent for more | reply ("Reply to the agent…") | send back, reopen | return |
| The AI that made the file | agent ("Agent", "Send to agent") | bot, assistant | AI, Claude (fine as the product name: "Claude Code, Codex or any other" on the key page) |
| Another person | their name; "a colleague's" in Settings | reviewer | user, author |
| This person | you, your ("Your draft", "Your reply") | author key | me, my, user |
| Personal options | Settings… | Preferences | Options |
| Passing the link on | Share… | Export | Send |
| Panel and placement | "Show comments" / "Hide comments" for the panel; "Add comment" to start placement, "Stop adding comments" to leave it; the menu's "Add comment" stays checked during placement; "Commenting on" / "Commenting off" for the touch gesture | annotate mode, edit mode | |
| The comment's spot | spot ("Tap a spot", "spot not found") | pin point | anchor, element |
| Time | "just now", "5 min ago", "2 h ago", "Nov 3", "Nov 3, 2025" | timestamps | seconds |

## Status words

A row's second line starts with a lead (coloured, bold) and " · ", then the
author, "you" when it is mine, "N replies" or the time
(`status()` in `comments-model.js`). The first that applies wins.

| Comment state | Lead | Colour |
| --- | --- | --- |
| my draft of a new comment | when, e.g. "2 min ago" (the "Your drafts" group names it) | muted |
| my decision in this review | ✓ Accept / ✕ Reject | blue |
| my decision with a reply draft | ✓ Accept · your reply (or ✕ Reject · your reply) | blue |
| my reply draft without a decision | ↩ Your reply | blue |
| unread reply from a colleague on my comment | New reply | red |
| rejected | rejected | muted |
| a colleague's comment or an agent proposal, not approved | awaiting approval | muted |
| approved agent proposal, no work in progress | ✓ Approved | green |
| `waiting` | sent, not taken yet | muted |
| `working` | Agent is working | purple |
| `done` | Done · check it | green |
| `wrote` | Done, wrote | green |
| `asked` | Asks you | red (it needs you) |
| none of these | (no lead) | — |

Where the spot is, after the rest: "spot not found", "on “Checkout”",
"on another screen". In the item view: "about the whole file", "on the
image", "“Send”", "on the page".

## Titles and headers

| Place | Text |
| --- | --- |
| Strip | "**N need you** · N drafts" or "**N need you** · N in progress"; omit absent counts. Use "N comments" if nothing needs attention or the server count is still loading. The narrow strip adds "›" where it leads to the list and has no Finish review button. |
| List title | "Loading comments…" / "Comments · N" / "No comments yet" |
| Groups | "Need you · N", "Your drafts · N", "In progress · N", "Discussion · N", "Closed · N" |
| Item navigation | "‹ All", "2 of 7 · on the image"; "Others" over the rest |
| Compose | "New draft", "New draft · about the whole file" |
| Review drafts | "Review drafts · N", "Not decided yet · N", "In this review" |
| Final Workspace round | "Finish review…", "Finish review", "No new edit request is sent." |
| Workspace notes and outcome | "Review notes update pending.", "Review finished. The outcome is pending.", "Read review notes" |
| Dialogs | "Share", "Settings", "What should we call you?" |
| Project page header | "3 files · kept until deleted" |
| Project row's dot (title, `aria-label`) | "Needs you" (the file needs you) |
| Viewer header | "12.3 KB · expires Nov 24, 2026 at 10:00 UTC+03:00" (device time zone) |
| Invite deadline | "This invite works once, until Nov 24, 2026 at 10:00 UTC+03:00." (device time zone) |
| Full-screen tip | "Hold — comment mode" / "Double-tap — exit full screen" (a gesture, a dash, what it does) |

## Patterns

### Buttons

Verb first, one or two words: **Copy link**, **Download file**, **Finish
review**, **Show**, **Save**, **Edit**, **Delete**, **Undo**, **Back**.
Decisions carry their mark: **✓ Accept**, **✕ Reject**,
**✓ All · N** (pill), **Accept all · N** (link). Closing a dialog is
**Done**; skipping the name is **No name**. The full-screen tip closes with
**OK**, or with **Hide** for good. A menu item that opens a dialog
ends with "…": **Share…**, **Settings…**. A count goes after " · ":
**Finish review · 3**. **Discard** is for typed work not yet saved as a draft;
**Delete** is for a saved draft. Use "Comment" in the new comment field.
On a computer, put both shortcuts below it: "Enter: save draft. Shift+Enter:
new line." Saving is not publication. No keyboard hint on a phone or touch
screen.

### Empty states

Say what is missing and give a next step when there is one. Use "No comments
yet", "Use Add comment to start.", "Nobody has commented yet.",
"No files yet", or "No drafts: you can publish a general comment only."
For a spot on the file use "Tap a spot to comment." or "Click a spot to
comment." according to the pointer, not the screen width.

### Action feedback and Undo

Apply a reversible action without a confirmation prompt. Show the result
as a short past-tense message and offer **Undo**. Use a confirmation only
when the stakes justify interrupting the person; say what will happen and
what cannot be undone. Do not offer an Undo that cannot safely restore the
previous state. The table shows feedback already in the product:

| Action | Toast |
| --- | --- |
| Draft about the whole file added | Added about the whole file |
| Draft edited | Saved |
| Draft deleted | Draft deleted + Undo |
| Account file deleted | Deleted 1 file · Launch notes + Undo |
| Account project deleted | Deleted 1 project, 10 files · Product launch + Undo |
| New draft dropped with text in it | Draft discarded + Undo |
| Accept all | Accepted 2, approved 1 + Undo |
| Finish review | Review sent to the agent / Review published |
| Long press on the floating ≡ | Commenting on / Commenting off |
| Copy in Share | Link copied. (in the dialog) |

### Errors and refusals

What happened, then what to do; a full stop after each sentence.

| Situation | Text |
| --- | --- |
| No network | Could not save the draft. Check the connection and try again. |
| 429 | Could not save the draft. Wait a minute and try again. |
| Missing object | Could not save the draft. It may no longer be available. Refresh and try again. |
| Draft limit | Could not save the draft. This file has too many drafts. Delete a draft and try again. |
| Comment limit | Could not finish the review. This file cannot hold more comments. Remove a new comment and try again. |
| Reply limit | Could not finish the review. This comment cannot hold more replies. Remove a reply and try again. |
| Other failure | Could not save the draft. Try again. |
| Comments did not load | Could not load comments. + a local next step |
| Accept all failed | Nothing accepted. + a local next step |
| Undo of Accept all failed | Not undone. + a local next step |
| Draft delete failed | Could not delete the draft. + a local next step |
| Before Finish review | Could not delete a draft. Try again. |
| Busy | Still saving. Try again. / Still saving your decisions. Try again. |
| Name refused | That name cannot be used. Give another one. |
| Empty edit | Write something, or delete the draft. |
| Leaving unsaved text | Send or clear the reply first / Save or cancel the edit first |
| New file did not load | Could not load the new file. Try again. |
| Copy failed | Could not copy: select the link above and copy it. |
| Expired link (page) | This link has expired or never existed. |
| Used invite (page) | This invite was used, has expired or never existed. Ask for a new one. |
| Name taken (page) | This name is already taken. Ask for a new invite. |

The action and next step are local UI copy. Never show the server's message,
status, exception or field names. Unknown errors use the action and “Try again.”
Name validation uses only known responses internally to reopen name entry.

### Counts

| Count | Form |
| --- | --- |
| comments | 1 comment / 2 comments; "Comments · N" in titles |
| drafts | 1 draft / 2 drafts |
| replies | 1 reply / 2 replies |
| files | 1 file / 2 files |
| need you | "N need you" for every N |
| in progress | "N in progress" |
| list position | "N of M" |
| zero | left out of the strip; a title with nothing shows its empty state |

Use **✓ Accept** for a colleague’s pending comment, an agent proposal or
completed agent work. Use **✕ Reject** for a colleague’s pending comment.
