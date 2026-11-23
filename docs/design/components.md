# Components

The parts of the viewer (`/a/{id}`), the project page (`/p/{id}`) and the
invite pages, with their states. Sizes and colours refer to
[tokens.md](tokens.md); the words are in [tone-of-voice.md](tone-of-voice.md).
Behaviour (what a tap does, what the server keeps) is in
[contract.md](../contract.md#the-viewer-page) and is not repeated here.

## Header

One row over the content, hairline `--line` at the bottom, zoomed by text
size. Hidden in full screen.

| Part | Viewer | Project page |
| --- | --- | --- |
| Title (h1, 14 bold, one line, ellipsis) | file title | project title |
| Second line (12, muted; deadline wraps as needed) | "12.3 KB · expires Nov 24, 2026 at 10:00 UTC+03:00"; in a project: the project title button | "3 files · kept until deleted" |
| Buttons, right, gap 12 | bubble, Full screen, ≡ | Full screen, ≡ |

**Square button** (`.actions .sq`): 32 × 32, radius 9, `--surface` fill, no border,
icon 19. Pressed: scale 0.95. Focus: 2 px `--blue` outline.

| Button | States |
| --- | --- |
| Bubble | off: `--surface`, title "Comment"; on: white icon on `--blue-fill`, inset shadow, `aria-pressed="true"`, title "Stop commenting" |
| Full screen | one state |
| ≡ | menu open: `--line` fill, `aria-expanded="true"` |

**Project title button** (`.pmeta`, file in a project): 12/600 in `--blue`,
then the 10 px chevron; the title is cut, the chevron stays. Its touch area
reaches up over the file title (44 px). Tap: the [file list](#file-list-drop-down).

## Menu (≡)

A column of items fanned out from its button over a scrim
`--scrim`. Each item: a dark label pill (`--ink`, white
14 px, radius 10, padding 7 11) and a 40 × 40 square (`--raised`, radius 12,
icon 20), 44 high, 10 apart. Opened from the header it drops down, right
aligned; from the floating ≡ it opens up or down, whichever has room, on the
side the button keeps to (label on the right when the button is on the left).
It scrolls only when it does not fit.

| Order | Item | Viewer | Viewer, full screen | Project page | Project, full screen |
| --- | --- | --- | --- | --- | --- |
| 1 | Comment / Commenting | — | yes | — | — |
| 2 | Exit full screen | — | yes | — | yes |
| 3 | Project files | file in a project | file in a project | — | — |
| 4 | Open separately | when the raw origin is set | same | — | — |
| 5 | Share… | yes | yes | yes | yes |
| 6 | Settings… | yes | yes | yes | yes |

Comment in comment mode: label "Commenting", square white on `--blue-fill` with an
inset shadow (`aria-checked="true"`).

## Floating ≡ (full screen)

44 × 44, radius 12, `--glass` with a 12 px blur, white 22 px icon.
Dragged anywhere, it snaps to the nearer side edge, 8 px in.

| State | Look |
| --- | --- |
| rest | translucent, ≡, and two thin corners of Exit full screen (top left, bottom right; the `corners` icon): a double tap leaves full screen |
| dragged | scale 1.08, `--ink` |
| menu open | `--ink`, ✕ icon, no corners |
| comment mode | 2 px `--blue` ring inside the edge, 12 px `--blue` bubble top right |
| long press 500 ms | toggles comment mode, toast "Commenting on" / "Commenting off" |
| double tap (second press within 300 ms and 20 px of the first tap) | closes the menu the first tap opened and leaves full screen |

**Tip** (`.float-tip`, `role="status"`): on entering full screen, beside the
button, above it when there is room, else under it, its edge in line with the
button's side, 8 px from it and from the window's edges. Toast colours
(`--ink`, white), 15 px, radius 14, max w 300, padding 10 10 10 14. Two
lines: "Hold — comment mode" (viewer only) and "Double-tap — exit full
screen"; under them **Hide** (plain, transparent) and **OK** (`--blue-fill`,
600), h 32 with a 44 px touch area. OK closes it; it shows on three entries
in all. Hide closes it for good in this browser. Escape, a long press, opening the
menu or leaving full screen close it too.

## Dialogs

A centred card (`.modal`): max 380 wide, padding 16, radius 16, `--raised`,
shadow `0 12px 40px` `--shadow-1`, over `--scrim`.
Title 16/600. Buttons at the bottom right, gap 8; the closing one is primary.
Inputs and plain buttons use `--field`. A tap on the scrim or Escape closes.

| Dialog | Content | Buttons |
| --- | --- | --- |
| Share | read-only link field + **Copy link** (primary, h 40); status line ("Link copied." or the failure); **Download file** row button with icon, 44 high (viewer only) | Done (plain) |
| Settings | rows: label (muted 14, 4.4em) + control. Name (text field, placeholder "No name"); Theme: Auto / Light / Dark; Text: Normal / Large / Larger; Pins: Normal / Large; Mine / others: Color / Color + shape; Dot: Red / Green / Yellow (the colour of every notification dot, [tokens.md](tokens.md#the-dot)); pin preview, the agent's pin with the dot; "Saved in this browser." | Done (primary) |
| Name | near the top (padding-top 60, so the keyboard leaves it in sight): "What should we call you?", field "Name" | No name (plain), Done (primary) |

Settings controls apply at once; there is no Cancel.

## Toast

One per page, white text on `--ink`, 13 px, radius 20, min h 36,
max w 440, blur 20, two lines at most. On a phone 8 px above the sheet (under
the header when the sheet is full); on a computer at the bottom left of the
content, beside the panel.

| State | Look | Time |
| --- | --- | --- |
| info | as above | 2.6 s (2.5 s from the menu and the project page) |
| with Undo | **Undo** at the right, 600, `--blue` lightened (60% with white) | at least 5 s |
| error | `--red-fill` background, Undo white | 8 s (10 s from the menu and the project page; 6 s for a refused name) |

Inside an open dialog the dialog's own status line speaks instead, because the
toast sits under the scrim.

## Banner

"The file was updated" + **Show** (`--blue-fill` pill, white 600, radius 16). Toast
colours, radius 14, centred just under the header; `role="status"`.

## Buttons

| Kind | Class | Look | Used for |
| --- | --- | --- | --- |
| Primary | `.btn.pri` | `--pri` with `--on-color`, 600; dark in light theme, blue in dark | Finish review, Accept, Save, Done, Copy link, Show |
| Plain | `.btn` | `--surface` (in dialogs `--field`), 14, h 32, radius 10, padding 0 12 | Back, Edit, Delete, Cancel, Reject, No name, + About the whole file |
| Big | `.btn.big` | h 36, padding 0 16 | Finish review in the Finish review view |
| Pill | `.all-pill` | `--blue` on `--tint`, 13/600, h 28, radius 14 | "✓ All · N" in the Need you title |
| Link | `.all-link`, `.undo`, `.drop-foot a` | `--blue`, no fill | "Accept all · N", Undo on a decision, "Project page ›" |
| Round icon | `.chev` | 32, radius 16, muted, `--surface` when pressed | ‹ › and fold |
| Send | `.send` | 30 circle, white arrow on `--blue-fill`; disabled `--line` | inside comment and reply fields |
| Site button | `button` | `--surface` with a `--line` border, radius 6 | invite and key pages |

States: pressed `scale(0.97)` and `--surface-2`; disabled opacity 0.35; focus
2 px `--blue` outline, 2 px offset. One primary per view; Accept
and Finish review can share a sheet (one in the body, one in the footer).

## Segmented control

| Where | Track | Option | Selected |
| --- | --- | --- | --- |
| Finish review: Send to agent / Only publish | `--surface`, padding 2, radius 10 | h 36, 15 px, radius 8 | `--seg-on`, 600, `--shadow-1` |
| Settings | `--field` | radio buttons drawn as segments, h 40, 14 px | same |

## Comments sheet (phone) and panel (computer)

Under 900 px a bottom sheet under the content; from 900 px a panel at the
right, `360px × --ts`, with a hairline on its left, no grip. Heights: see
[tokens.md](tokens.md#layout). The sheet: grip (36 × 5 bar, 22 high), body
(scrolls; its title line sticks to the top and is a drag handle), footer.
On a phone every title line ends with the fold button (⌄).

| View | Holds | Footer (phone) |
| --- | --- | --- |
| strip | one line, see below | — |
| list | title "Comments · N" (or "No comments yet"), groups, "+ About the whole file" in comment mode | Finish review · N, with drafts |
| item | navigation, one comment as a chat or one draft, then "Others" with the rest when pulled to full | reply field; Finish review · N with drafts |
| compose | caption "New draft" (or "New draft · about the whole file"), field with send | the field itself, so it stays above the keyboard |
| finish | title "Finish review · N", Not decided yet, In this review, general comment field | Send to agent / Only publish, Back, Finish review |

On a computer the footer's parts sit at the end of the body, and Finish
review sits in the strip line that heads the panel.

### Strip

One line, 14 px, words only, parts joined by " · ", zeros left out. Tap: the
list at middle height; drag: the sheet follows the finger.

| Part | Shown when |
| --- | --- |
| **N need you** (bold) | the agent answered, a colleague’s comment awaits approval, or a colleague replied unread on this person’s comment, with no draft on it |
| N drafts | the person has drafts; **Finish review** (primary) at the right |
| N in progress | sent to the agent and not answered, and no drafts |
| N comments | none of the above |
| Tap a spot to comment. | comment mode on, a spot can be tapped: alone without comments, otherwise beside a plain comment count |
| No comments yet | no comments otherwise |
| › | at the end on a phone when there is no Finish review |

A draft once added folds the sheet to the strip, whose line pulses once
(opacity 0.4, 0.6 s).

### Groups in the list

Each group starts with a section title "Title · N" (13/600, uppercase, muted).
Empty groups are left out. ‹ › in the item view follow this order.

| Group | Holds | Start | Title extra |
| --- | --- | --- | --- |
| Need you | the agent's finished work, then its questions, then colleagues' comments awaiting approval and unread colleague replies on own comments | open | "✓ All · N" pill when two or more can be accepted |
| Your drafts | new comments, and comments with this person's decision or reply draft | open | — |
| In progress | `waiting`, `working` | open | — |
| Discussion | published, nothing waits for this person | folded | title is a button with › / ⌄, min h 44 |
| Closed | rejected, unless moved to Your drafts by a personal draft | folded | same |

On your comment, an unread colleague reply leads with “New reply” in Need you.
Local drafts and Closed take precedence; existing agent ordering is unchanged.
Opening the thread marks the current replies read; a reply arriving later stays
new until the thread is opened again.

A folded group opens while one of its items is selected, and all fold again
when the sheet folds to the strip.

### Comment row

A button, min h 44, padding 8 6, radius 10, hairline below (none after the
last).

| Part | Look |
| --- | --- |
| Pin | list pin 28 px, see [pins.md](pins.md) |
| First line | text, 16, one line with ellipsis; "About the whole file: " before it when there is no spot |
| Second line | 14 muted: lead word in colour and bold, " · ", then author, "you" when mine, "N replies" or the time, where the pin is when it is not shown |

| State | Look |
| --- | --- |
| pressed, selected | `--surface` |
| draft (not in Finish review) | `--tint`, no hairline; status in `--muted` like other rows: when, and where if its pin is not on what is shown (the group says it is a draft); selected `--tint-2` |
| spot not found | status in `--red` |
| on another screen | status muted, not bold |

### Item view

| Part | Look |
| --- | --- |
| Navigation | "‹ All" (computer only), "N of M · where" (13 muted, tap: the list at middle height), ‹ ›, fold (phone) |
| Comment | pin, author 14/600 + time 13 muted, text 16; notes ("written before the file was replaced", where the spot is); "✓ Approved by X", "✕ Rejected by X" or "Awaiting approval · not sent to the agent" (13 muted) |
| Replies | avatar 28 (agent: white star on `--purple-fill`; person: initial on `--orange`, `--blue-fill` when mine), "Agent", "You" or the name |
| Decision | one line under the thread: "✕ Rejected" / "✓ Accepted" (600, `--blue`; Rejected muted), "goes with your review" or "closes with your review", **Undo** at the right |
| Unsent reply | `--tint`, ↩ avatar, "Your reply", Edit / Delete |
| Actions | awaiting approval: **✓ Accept** (primary), **✕ Reject** for a colleague's comment; agent done: **✓ Accept** (primary) |
| Own draft | `--tint`, "Your draft" + time, text, Edit / Delete; editing: field, Save (primary), Cancel |
| Reply field | pill field in the footer: "Reply to the agent…", "Add a reply…" or "Reply to {name}…"; not shown on a rejected comment or with an accept draft |

An agent-authored proposal says "Awaiting approval" and offers only **✓ Accept**
when it can be approved. Approval keeps the thread and its pin. It does not
send work to the agent unless the same review sends a reply.

### Compose

Caption 13 muted, then a pill field (radius 20, 16 px, grows to 120 px) with
the round send arrow inside at the bottom right; the arrow is disabled while
the field is empty. With a hardware keyboard Enter sends and Shift+Enter
breaks the line; on a touch screen Enter breaks the line and the arrow sends.
2000 characters at most. Focus: `--bg` with a hairline and a 4 px `--tint-2` ring.

### Finish review

| Part | Look |
| --- | --- |
| Title | "Finish review · N" |
| Not decided yet · N | section title with the "Accept all · N" link at the right when two or more; rows of what waits for a yes |
| In this review | section title (only after Not decided yet); new drafts with where they are, then decided and replied comments, accepted last |
| Empty | "No drafts: you can publish a general comment only." |
| General comment | field, min h 60, placeholder "General comment" |
| Footer | segmented Send to agent / Only publish; Back and **Finish review** (big, primary), disabled with nothing to send |

While a reply or the general comment is typed in, the footer hides the
segmented control and the buttons under the field. The name dialog appears at
the first nonempty Finish review submission, not when entering comment mode
or saving drafts. Dismissing it publishes nothing.

## Project page

The project's files in its order, on `--bg`, zoomed by text size.

| Part | Look |
| --- | --- |
| Section heading | as a group title, margin 16 0 4 |
| Row (`.pf`) | link, min h 60, padding 8 6, gap 14, radius 10, hairline below; pressed or hovered `--surface`; focus 2 px inset outline |
| Type square | 40 × 40, radius 9, `--surface`, muted 11/700 type ("HTML", "MD", "PNG"…) |
| Title | 16, one line, ellipsis |
| Detail | 14 muted, "12.3 KB · 2 h ago" |
| Dot, right | 12 × 12 circle in `--dot`, no number, no icon, vertically centred, 4 from the right edge of the row's padding; title and `aria-label` "Needs you" (`role="img"`, so the link reads "… Needs you"). Shown when a comment on the file waits for this person's decision or they have drafts there, their review not finished; nothing else on the row |
| Current file (in the drop-down) | `--surface` (`aria-current="page"`) |

The dots appear only after the summary call answers; none while it loads or
when it fails. 12 px zoomed by the default Large text is about the pin
badge's 14.

## File list drop-down

In a file of a project: the project page's rows in a sheet hanging from under
the header, over `--scrim`; max h 80%, bottom radius 16,
full width on a phone, 440 px from 641 px. The current file is marked and
scrolled into view. At the bottom right "Project page ›" (16/600, `--blue`,
44 high). Closes on the scrim, Escape or the title again.

## Empty states

| Where | Text |
| --- | --- |
| Strip, no comments | "No comments yet", or in comment mode "Tap a spot to comment." |
| List, no comments | title "No comments yet"; "Tap a spot to comment." (comment mode), "Nobody has commented yet." (comment mode, no spot possible), "Turn on the speech bubble to comment." (mode off) |
| Finish review, no drafts | "No drafts: you can publish a general comment only." |
| Project, no files | "No files yet" |
| Archive in the viewer | "Archive: download it to see the contents." |
| No storage | "This browser keeps nothing: your drafts last only while this page is open." |

The sheet is hidden while comment mode is off and the file has no comments and
no drafts.

## Invite, key and message pages

A single column, max 560, padding 24 16; title 20; plain site buttons
(padding 8 16, 15 px; at ≤640 px at least 44 × 44). Theme applies;
On phones Text scales invite and key pages, but not message pages; desktop
invite and key metrics remain unchanged.
