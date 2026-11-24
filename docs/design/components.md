# Components

The shipped viewer (`/a/{id}`), project page (`/p/{id}`), and invitation,
key and message pages. Sizes are before [Text scaling](tokens.md#text-size).
Use [tokens.md](tokens.md) for shared dimensions and colours,
[tone-of-voice.md](tone-of-voice.md) for copy, and
[contract.md](../contract.md#the-viewer-page) for service behaviour.

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
| Bubble | Off: `--surface`, title "Comment". On: white icon on `--blue-fill`, inset shadow, `aria-pressed="true"`, title "Stop commenting". See [desktop review attention](#desktop-review-attention). |
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
It scrolls only when it does not fit. At ≥900 px the viewer uses a compact
raised list: 44 px rows with an icon on the left, a 28 px icon box, a plain label,
and `--surface` on hover or keyboard focus. The list is kept within the viewport;
the project page keeps the fan.

| Order | Item | Viewer | Viewer, full screen | Project page | Project, full screen |
| --- | --- | --- | --- | --- | --- |
| 1 | Comment / Commenting | — | yes | — | — |
| 2 | Exit full screen | — | yes | — | yes |
| 3 | Project files | file in a project | file in a project | — | — |
| 4 | Open separately | when the raw origin is set | same | — | — |
| 5 | Share… | yes | yes | yes | yes |
| 6 | Settings… | yes | yes | yes | yes |

Comment mode uses the label "Commenting", a white icon on `--blue-fill`, an
inset shadow and `aria-checked="true"`. Turning it on opens the desktop panel;
turning it off folds the panel. With placement off, selecting a pin opens the
thread. The fold button or Escape can close that thread panel without losing
input. On narrow screens, the strip is hidden in full screen with Comment off.

### Desktop review attention

At ≥900 px, the dot indicates a nonempty **Need you** group, not all pins or
this person's drafts. It is on the header's Comment button in the normal
viewer. In full screen it moves from the closed floating ≡ to the Comment row
when the menu opens. The dot and the blue comment-mode mark are separate.

## Floating ≡ (full screen)

44 × 44, radius 12, `--glass` with a 12 px blur, white 22 px icon.
Dragged anywhere, it snaps to the nearer side edge, 8 px in.

| State | Look |
| --- | --- |
| rest | translucent, ≡, and two thin corners of Exit full screen (top left, bottom right; the `corners` icon): a double tap leaves full screen |
| dragged | scale 1.08, `--ink` |
| menu open | `--ink`, ✕ icon, no corners; the desktop Need you dot is on the Comment item instead |
| desktop, menu closed with Need you | `--dot` at the top right, outside the mode bubble |
| comment mode | 2 px `--blue` ring inside the edge, 12 px `--blue` bubble top right |
| touch long press 500 ms | toggles comment mode, toast "Commenting on" / "Commenting off" |
| double tap or double click (second press within 300 ms and 20 px of the first) | closes the menu the first tap opened and leaves full screen |

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

A card (`.modal`): max 380 wide, padding 16, radius 16, `--raised`, shadow
`0 12px 40px` `--shadow-1`, over `--scrim`. Share and Settings are centred;
Name opens near the top to leave room for the keyboard. Title 16/600. Buttons
at the bottom right, gap 8. Fields and plain buttons use `--field`. A tap on the
scrim or Escape closes. Use the [shared action and field styles](#buttons).

| Dialog | Content | Buttons |
| --- | --- | --- |
| Share | read-only link field + **Copy link** (primary, h 40); status line ("Link copied." or the failure); **Download file** row button with icon, 44 high (viewer only) | Done (plain) |
| Settings | rows: label (muted 14, 4.4em) + control. Name (text field, placeholder "No name"); Theme: Auto / Light / Dark; Text: Normal / Large / Larger; Pins: Normal / Large; Mine / others: Color / Color + shape; Dot: Red / Green / Yellow (the colour of every notification dot, [tokens.md](tokens.md#the-dot)); pin preview, the agent's pin with the dot; "Saved in this browser." | Done (primary) |
| Name | near the top (padding-top 60, so the keyboard leaves it in sight): "What should we call you?", field "Name" | No name (plain), Done (primary) |

Settings controls apply at once; there is no Cancel.

## Toast

One per page, white text on `--ink`, radius 20, min h 36, max w 440, blur 20.
Ordinary messages use at most two lines; errors are not clamped. In the narrow
viewer it sits 8 px above the sheet, or under the header when the sheet is full.
In the desktop viewer it sits at the bottom left, beside the panel.

| State | Look | Time |
| --- | --- | --- |
| info | as above | 2.6 s (2.5 s from the menu and the project page) |
| with Undo | **Undo** at the right, 600, `--blue` lightened (60% with white) | at least 5 s |
| error | `--red-fill` background; Undo is white when offered | Most feedback errors: 8 s; menu and project: 10 s; refused name: 6 s |

Inside an open dialog the dialog's own status line speaks instead, because the
toast sits under the scrim.

## Banner

"The file was updated" + **Show** (`--blue-fill` pill, white 600, radius 16). Toast
colours, radius 14, centred just under the header; `role="status"`.

## Buttons

| Kind | Class | Look | Used for |
| --- | --- | --- | --- |
| Primary | `.btn.pri` | Plain button size; `--pri` with `--on-color`, weight 600 | Finish review, Accept, Save, Done, Copy link |
| Plain | `.btn` | `--surface` (in dialogs `--field`), 14, h 32, radius 10, padding 0 12 | Back, Edit, Delete, Cancel, Reject, No name, + About the whole file |
| Big | `.btn.big` | h 36, padding 0 16 | Finish review in the Finish review view |
| Pill | `.all-pill` | `--blue` on `--tint`, 13/600, h 28, radius 14 | "✓ All · N" in the Need you title |
| Link | `.all-link`, `.undo`, `.drop-foot a` | `--blue`, no fill | "Accept all · N", Undo on a decision, "Project page ›" |
| Round icon | `.chev` | 32, radius 16, muted, `--surface` when pressed | ‹ › and fold |
| Send | `.send` | 30 circle, white arrow on `--blue-fill`; disabled `--line` | inside comment and reply fields |
| Site button | `button` | `--surface` with a `--line` border, radius 6 | invite and key pages |

See [control sizes](tokens.md#control-sizes) for visible and touch dimensions.
Plain sheet and dialog controls have a 2 px `--blue` keyboard focus outline;
pressed buttons shrink to 0.97. A disabled sheet button uses opacity 0.35.
Use the existing variant before adding another. Accept and Finish review can
share a sheet because one acts on the current comment and the other publishes
the review.

### Text fields

Reuse the plain dialog field for a short value: minimum height 40, padding
10 × 12, radius 10, 16/20 text, no border. It uses `--field`, changing to `--bg`
with a hairline and 4 px `--tint-2` ring on focus. Comment and reply fields use
the [compose variants](#compose), not a separate input family.

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
| **N need you** (bold) | the [Need you group](#groups-in-the-list) is nonempty |
| N drafts | the person has drafts; **Finish review** (primary) at the right |
| N in progress | sent to the agent and not answered, and no drafts |
| N comments | none of the above |
| Tap a spot to comment. / Click a spot to comment. | comment mode on, a spot can be selected: alone without comments, otherwise beside a plain comment count |
| No comments yet | no comments otherwise |
| › | at the end on a phone when there is no Finish review |

The server's comment count stays visible while comments load. Adding a draft
returns to the list and pulses the strip once. On a narrow screen it also folds
the sheet to the strip.

### Groups in the list

Each group starts with a section title "Title · N" (13/600, uppercase, muted).
Empty groups are left out. ‹ › in the item view follow this order.

| Group | Holds | Start | Title extra |
| --- | --- | --- | --- |
| Need you | finished work first, then the agent's questions, then other items that need a decision or attention: proposals, colleagues’ comments awaiting approval and unread replies on own comments | open | "✓ All · N" pill when two or more can be accepted |
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

On a narrow screen the 16 px field is a pill (radius 20) with the round send
arrow inside. In the open sheet it grows up to 15% of the available viewport
height, but is at least 40 px before Text scaling. With a hardware keyboard,
Enter sends and Shift+Enter inserts a newline; on touch, Enter inserts a newline
and the arrow sends.

At ≥900 px the field uses radius 10 and a separate adjacent arrow. The field
scrolls internally at its height limit. Enter and Shift+Enter insert a newline;
Ctrl/Meta+Enter saves the draft or reply. The item header and reply stay visible
while the thread scrolls. Text limit: 2,000 characters. An empty field disables
send. Focus matches the [plain field](#text-fields).

### Finish review

| Part | Look |
| --- | --- |
| Title | "Finish review · N" |
| Not decided yet · N | section title with the "Accept all · N" link at the right when two or more; rows of what waits for a yes |
| In this review | section title (only after Not decided yet); new drafts with where they are, then decided and replied comments, accepted last |
| Empty | "No drafts: you can publish a general comment only." |
| General comment | field, min h 60, placeholder "General comment"; on a narrow screen, growing the focused field preserves the reading position and keeps a newly wrapped line at the end in view |
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
| Detail | muted metadata (12 px, 15 px at ≤640 px), "12.3 KB · 2 h ago" |
| Dot, right | 12 × 12 circle in `--dot`, no number, no icon, vertically centred, 4 from the right edge of the row's padding; title and `aria-label` "Needs you" (`role="img"`, so the link reads "… Needs you"). Shown when a comment on the file waits for this person's decision or they have drafts there, their review not finished; nothing else on the row |
| Current file (in the drop-down) | `--surface` (`aria-current="page"`) |

Dots follow the summary while the project page or file list is visible and
update without reloading. There are none before the first answer. A failed
refresh keeps the last result. 12 px zoomed by the default Large text is
about the pin badge's 14.

## File list drop-down

In a file of a project: the project page's rows in a sheet hanging from under
the header, over `--scrim`; max h 80%, bottom radius 16,
full width on a phone, 440 px from 641 px. The current file is marked and
scrolled into view. At the bottom right "Project page ›" (16/600, `--blue`,
44 high). Closes on the scrim, Escape or the title again.

## Empty states

| Where | Text |
| --- | --- |
| Strip, no comments | "No comments yet", or in comment mode the pointer-specific tap/click hint |
| List, no comments | title "No comments yet"; "Tap a spot to comment." for touch or "Click a spot to comment." for mouse (comment mode), "Nobody has commented yet." (comment mode, no spot possible), "Turn on the speech bubble to comment." (mode off) |
| Finish review, no drafts | "No drafts: you can publish a general comment only." |
| Project, no files | "No files yet" |
| Archive in the viewer | "Archive: download it to see the contents." |
| No storage | "This browser keeps nothing: your drafts last only while this page is open." |

The narrow sheet stays out of the way when there is nothing to show and
comment mode is off. The desktop panel can also be folded independently of the
contents, while pins stay visible.

## Invite, key and message pages

A single column (`.join`), max 560, padding 24 × 16, title 20. Use the plain
site button: 15 px text, padding 8 × 16, radius 6, `--surface` with a `--line`
border. The desktop height fits the label and padding. At ≤640 px the button
is at least 44 × 44 before [Text scaling](tokens.md#text-size). The read-only
key field uses the same surface, border and radius, with monospaced text.
Theme follows the shared palette. Text scales `.join` only at ≤640 px; it does
not scale message pages.
