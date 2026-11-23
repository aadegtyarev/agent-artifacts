# Tone of voice

How the service's own interface speaks. The strings below are the ones in the
code (`static/comments.js`, `comments-model.js`, `menu-dialogs.js`,
`viewer.js`, `project.js`, `templates/`, `pages.py`).

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
6. No exclamation marks, no emoji. ✓ ✕ ↩ are marks on decisions only.
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
| Publishing all drafts at once | review: **Finish review**, "Review published", "Review sent to the agent" | send all | submit, commit |
| Waiting for this person's decision | need you ("2 need you", "Need you") | action required, awaiting input | pending, to do |
| Sent and not answered yet | in progress ("1 in progress", "In progress") | with the agent, queued | |
| The agent's finished work, taken | accept ("✓ Accept", "✓ Accepted") | resolve, resolved | close, done |
| A colleague's comment let through | approve ("✓ Approve", "✓ Approved by Anna") | | confirm, allow |
| A colleague's comment stopped | reject ("✕ Reject", "rejected") | decline, dismiss | delete |
| Asking the agent for more | reply ("Reply to the agent…") | send back, reopen | return |
| The AI that made the file | agent ("Agent", "Send to agent") | bot, assistant, Claude | AI |
| Another person | their name; "a colleague's" in Settings | reviewer | user, author |
| This person | you, your ("Your draft", "Your reply") | author key | me, my, user |
| Personal options | Settings… | Preferences | Options |
| Passing the link on | Share… | Export | Send |
| Comment mode | "Comment", "Stop commenting", "Commenting", "Commenting on" / "Commenting off" | annotate mode, edit mode | |
| The comment's spot | spot ("Tap a spot", "spot not found") | pin point | anchor, element |
| Time | "just now", "5 min ago", "2 h ago", "Nov 3", "Nov 3, 2025" | timestamps | seconds |

## Status words

A row's second line starts with a lead (coloured, bold) and " · ", then the
author, "you" when it is mine, "N replies" or the time
(`status()` in `comments-model.js`). The first that applies wins.

| Comment state | Lead | Colour |
| --- | --- | --- |
| my draft of a new comment | your draft (no lead) | blue, 600 |
| my decision in this review | ✓ Approve / ✕ Reject / ✓ Accept | blue |
| the same, with my reply | ✓ Accept · your reply (etc.) | blue |
| my reply draft | ↩ Your reply | blue |
| accepted | accepted | muted |
| rejected | rejected | muted |
| a colleague's, not approved | awaiting approval | muted |
| `waiting` | sent, not taken yet | muted |
| `working` | Agent is working | purple |
| `done` | Done · check it | green |
| `wrote` | Done, wrote | green |
| `asked` | Asks you | orange |
| none of these | (no lead) | — |

Where the spot is, after the rest: "spot not found", "on “Checkout”",
"on another screen". In the item view: "about the whole file", "on the
image", "“Send”", "on the page".

## Titles and headers

| Place | Text |
| --- | --- |
| Strip | "**N need you** · N drafts · N in progress", or "N comments", ending "›" on a phone without Finish review |
| List title | "Comments · N" / "No comments yet" |
| Groups | "Need you · N", "Your drafts · N", "In progress · N", "Discussion · N", "Closed · N" |
| Item navigation | "‹ All", "2 of 7 · on the image"; "Others" over the rest |
| Compose | "New draft", "New draft · about the whole file" |
| Finish review | "Finish review · N", "Not decided yet · N", "In this review" |
| Dialogs | "Share", "Settings", "What should we call you?" |
| Project page header | "3 files · kept until deleted" |
| Viewer header | "12.3 KB · expires 2026-11-24 10:00 UTC" |

## Patterns

### Buttons

Verb first, one or two words: **Copy link**, **Download file**, **Finish
review**, **Show**, **Save**, **Edit**, **Delete**, **Undo**, **Back**.
Decisions carry their mark: **✓ Approve**, **✕ Reject**, **✓ Accept**,
**✓ All · N** (pill), **Accept all · N** (link). Closing a dialog is
**Done**; skipping the name is **No name**. A menu item that opens a dialog
ends with "…": **Share…**, **Settings…**. A count goes after " · ":
**Finish review · 3**.

### Empty states

Say what is missing, then how to start, in one sentence each:
"No comments yet", "Tap a spot on the page to leave a comment.",
"Turn on the speech bubble to comment.", "Nobody has commented yet.",
"No files yet", "No drafts: you can publish a general comment only."

### Confirmations

A short past-tense toast, no full stop, Undo where it can be taken back:

| Action | Toast |
| --- | --- |
| Draft about the whole file added | Added about the whole file |
| Draft edited | Saved |
| Draft deleted | Draft deleted + Undo |
| New draft dropped with text in it | Draft discarded + Undo |
| Accept all | Accepted 2, approved 1 + Undo |
| Finish review | Review sent to the agent / Review published |
| Long press on the floating ≡ | Commenting on / Commenting off |
| Copy in Share | Link copied. (in the dialog) |

### Errors and refusals

What happened, then what to do; a full stop after each sentence.

| Situation | Text |
| --- | --- |
| No network | Could not reach the server. Check the connection and try again. |
| 429 | Too many requests from your address. Wait a minute and try again. |
| Other failure, no message | The server answered 502. Try again later. |
| Comments did not load | Could not load comments. + the reason |
| Accept all failed | Nothing accepted. + the reason |
| Undo of Accept all failed | Not undone. + the reason |
| Draft delete failed | Could not delete the draft. + the reason |
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

"The reason" is the server's message with its first letter raised and a full
stop added, for example "There are too many drafts on this file." or "This
comment has the most replies it can hold."

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
