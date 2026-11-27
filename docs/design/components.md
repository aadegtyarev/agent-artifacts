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
| Buttons, right, gap 12 | Comments (desktop only), Full screen, ≡ | Full screen, ≡ |

**Square button** (`.actions .sq`): 32 × 32, radius 9, `--surface` fill, no border,
icon 19. Pressed: scale 0.95. Focus: 2 px `--blue` outline.

| Button | States |
| --- | --- |
| Comments | Opens or closes the panel (`aria-expanded`), not pin placement; see [desktop review attention](#desktop-review-attention). |
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
| 1 | Show comments / Hide comments | yes | yes | — | — |
| 2 | Add comment | desktop | desktop | — | — |
| 3 | Full screen / Exit full screen | Full screen on desktop | Exit full screen | — | Exit full screen |
| 4 | Project files | file in a project | file in a project | — | — |
| 5 | Open separately | when the raw origin is set | same | — | — |
| 6 | Share… | yes | yes | yes | yes |
| 7 | Settings… | yes | yes | yes | yes |

Show comments opens the panel without enabling placement. **Add comment**
starts or stops placement and is checked while active. The desktop menu
shows Shift+C (panel), C (start placement) and F (full screen). Latin C and
Cyrillic С work, including for starting placement from a focused page.
Clicking an existing pin opens its thread without enabling placement.

### Desktop review attention

At ≥900 px, the dot indicates a nonempty **Need you** group, not all pins or
this person's drafts. It is on the header's Comments button in the normal
viewer. In full screen it moves from the closed floating ≡ to the panel row
when the menu opens. The dot and the blue comment-mode mark are separate.

## Floating ≡ (full screen)

44 × 44, radius 12, `--glass` with a 12 px blur, white 22 px icon.
In the desktop viewer it only opens or closes the menu. In the narrow viewer
and on the project page it can be dragged and snaps to the nearer side edge,
8 px in.

| State | Look |
| --- | --- |
| rest | translucent ≡; in the narrow viewer and project page, the `corners` icon hints that a double tap leaves full screen |
| dragged | scale 1.08, `--ink` |
| menu open | `--ink`, ✕ icon, no corners; the desktop Need you dot is on the panel item instead |
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

In the account, deletion uses the same toast box and **Undo**, pinned at the
top centre in both layouts without covering the heading on arrival. Start with
what and how many were deleted (one file, or one project and its file count),
then the title. The count must be readable even if the title is long. Stack
active messages if needed; pressing one Undo restores only that item. Show
each for the remaining server Undo time,
not a fresh minute on reload. On a just-completed deletion, focus its Undo;
when it expires, move focus only if the discarded message had it.

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
right with a hairline on its left and a [resize handle](#desktop-panel-handle).
Sizes: see [tokens.md](tokens.md#layout). The narrow sheet: grip (36 × 5 bar,
22 high), body
(scrolls; its title line sticks to the top and is a drag handle), footer.
On a phone every title line ends with the fold button (⌄).

| View | Holds | Footer (phone) |
| --- | --- | --- |
| strip | one line, see below | — |
| list | title "Comments · N" (or "No comments yet"), groups, "+ About the whole file" in comment mode; Workspace review notes status and verified link | Finish review… where eligible; Review drafts · N with drafts |
| item | navigation, one comment as a chat or one draft, then "Others" with the rest when pulled to full | reply field; Review drafts · N with drafts |
| compose | caption "New draft" (or "New draft · about the whole file"), field with send | the field itself, so it stays above the keyboard |
| draft preview | title "Review drafts · N", Not decided yet, In this review, general comment field | Send to agent / Only publish, Back, Send comments / Publish comments |
| finish | title "Finish review", open comments, drafts to publish, optional final note | Back, Finish review |

**Add comment** sits next to the list title. It is primary if there are no
drafts. During placement the same button says **Stop adding comments** and
uses the plain sheet-button style; the keyboard hint changes from `C` to
`Esc` on a computer with a fine pointer. It is also available in the title
area of a selected comment or new draft while placing pins. The title and
actions can wrap. Stopping leaves the input and the panel in place.
With drafts, **Review drafts** is the primary action at the bottom right of
the list or thread on both layouts. **Discard** in a new draft offers Undo.

### Desktop panel handle

An 18 × 54 px vertical tab centred on the boundary, with a muted 4 × 24 px
grip and a blue focus outline. Click, Enter or Space opens or closes the panel.
Left and Right resize while the open handle has focus. Drag to the right edge
to fold. The tab stays there and restores the previous width. Closing does not
discard typed text.

### Strip

One line, 14 px, words only, parts joined by " · ", zeros left out. Tap: the
list at middle height; drag: the sheet follows the finger.

| Part | Shown when |
| --- | --- |
| **N need you** (bold) | the [Need you group](#groups-in-the-list) is nonempty |
| N drafts | the person has drafts; **Finish review** (primary) at the right |
| N in progress | sent to the agent and not answered, and no drafts |
| N comments | none of the above |
| Tap a spot to comment. / Click a spot to comment. | active placement on a folded narrow sheet, next to Stop adding comments instead of counts |
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

Published comments and replies render basic Markdown in the item view.
Links use the message's size and wrap with the text; underline them in
`--blue`, reuse the sheet's focus outline and open them in a new tab to
keep the discussion in place. List rows and unsubmitted drafts stay plain
text; a row is a button, not a group of links. Keep the same compose field.

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
scrolls internally at its height limit. In a new comment on a computer,
Enter does what the arrow does: save a draft without publishing it.
Shift+Enter, Ctrl+Enter and Meta+Enter insert a newline; plain Enter on a touch
screen also inserts a newline. Put the save and new-line hints together in one
muted note below the field on desktop with a fine pointer. The placeholder is
always "Comment". The note stays visible while typing, wraps if needed and is
not shown in the narrow or touch composer.
Replies and edits still save with Ctrl/Meta+Enter. The item header and reply
stay visible while the thread scrolls. Text limit: 2,000 characters. An empty
field disables send. Focus matches the [plain field](#text-fields).

### Review drafts and finish review

| Part | Look |
| --- | --- |
| Title | "Review drafts · N"; link to the exact reviewed version when available |
| Not decided yet · N | section title with the "Accept all · N" link at the right when two or more; rows of what waits for a yes |
| In this review | section title (only after Not decided yet); new drafts with where they are, then decided and replied comments, accepted last |
| Empty | "No drafts: you can publish a general comment only." |
| General comment | field, min h 60, placeholder "General comment"; on a narrow screen, growing the focused field preserves the reading position and keeps a newly wrapped line at the end in view |
| Footer | Send to agent / Only publish; Back and primary Send comments / Publish comments, disabled with nothing to send |

The linked owner of an eligible open Workspace review has a separate
**Finish review…** action even without drafts. It opens a confirmation in
this panel, with an optional final note and counts for open comments and
unpublished drafts. **Finish review** publishes those drafts without
requesting edits. Back restores the opener, note and recipient choice.
The discussion shows **Read review notes** after the first verified write;
a pending update or outcome does not hide the verified link.

The main list and both confirmation views share two fixed footer slots. The
visible action boxes are 48 CSS px high at every Text size, on phone and
computer. An empty slot or changed label does not move the other slot.
While typing the general comment the recipient control is out of the way.
The name dialog appears at the first publication, not when starting placement
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
| List, no comments | title "No comments yet"; "Tap a spot to comment." for touch or "Click a spot to comment." for mouse (comment mode), "Nobody has commented yet." (comment mode, no spot possible), "Use Add comment to start." (mode off) |
| Finish review, no drafts | "No drafts: you can publish a general comment only." |
| Project, no files | "No files yet" |
| Archive in the viewer | "Archive: download it to see the contents." |
| No storage | "This browser keeps nothing: your drafts last only while this page is open." |

Outside full screen the narrow strip stays visible, even with no comments.
Entering full screen hides the panel and the strip at both widths. Show
comments opens the panel for reading. Folding it keeps existing pins visible.

## Invite, key and message pages

A single column (`.join`), max 560, padding 24 × 16, title 20. Use the plain
site button: 15 px text, padding 8 × 16, radius 6, `--surface` with a `--line`
border. The desktop height fits the label and padding. At ≤640 px the button
is at least 44 × 44 before [Text scaling](tokens.md#text-size). The read-only
key field uses the same surface, border and radius, with monospaced text.
Theme follows the shared palette. Text scales `.join` only at ≤640 px; it does
not scale message pages.

The company sign-in page uses the same column and site button. It names the
allowed domain and has one action, **Sign in with Google**. A failed sign-in
keeps that action and explains how to retry. At the home page after sign-in,
show the signed-in email and **Sign out**; this is not a file list or account
settings page.

On a Workspace installation with Drive enabled, **Sign in with Google** asks
for sign-in and Drive access together. An existing session that needs Drive
permission sees **Google Drive access needed** in the same column, with one
primary action, **Continue with Google**. Explain the Google scope and the
configured folder in the page text. Keep the return link and use the account
button's keyboard focus and hit area. Do not add a permanent second sign-in
button to the account page.

## Account and device connection

When account pages are enabled, the signed-in landing becomes **Your
account**. Both entry modes use the same account page. With files or
projects, **Projects** and **Files without a project** come first, with real
links to the work. **Agent keys** and **Devices** follow in reading and
keyboard order; from 900 px they sit in a narrower column to the right.
An empty account stays in one column, starts with agent setup and doesn't
reserve a column for an empty file list.

Each project or file has a three-dot **Actions for …** button (32 visual,
44 × 44 touch). **Share…** and **Delete** are 44 px rows. They open in a
popover next to the button from 900 px; on a phone use a bottom sheet. Opening
or dismissing actions does not move the list; Escape returns focus to the
trigger. Without the menu script the two actions remain in the row.
**Share…** reuses the Share dialog, returns focus to the trigger and copies
the existing link. Show whether sign-in is required.
**Delete** on a file or project hides it immediately and uses the top-centred
[Undo toast](#toast). Show the project name and number of files there, not in
an extra confirmation. Keep independent deletions separate. If the project
changed since the account was shown, refresh the list without deleting it.
Use the `.join` column for other account forms, the compact `.btn` from the
sheet and the existing short dialog field. Keep the same pre-Text dimensions
in both entry modes and the account at narrow and desktop widths. A larger tap target is not a larger
drawn button. The key setup text is the shared read-only multi-line field.
With Google, use the company sign-in screen above. Without Google, **Connect
a device** shows the code field when a connection code is active, or explains
how to get one. Keep the setup-code confirmation separate from the short
connection code. In **Devices**, label this session **This device** and sign
out one session at a time. Without Google, **Account recovery** explains
why to save the separate recovery code; the code display reuses the read-only
field and Copy control. The recovery form names what will be signed out before
the person presses **Restore account**.

Under **Agent keys**, show the agent label, not its credential. **Add an
agent** opens one panel; it is already open in an account with no agents. The
**New agent** and **Existing agent** choices stay in place as the fields
change. Prefill a free name that the person can edit. **Create key** shows
**Copy setup** in the same panel, with **Show setup text** for a manual copy.
**Key issued** is not a claim that the agent has used the key.

A link from an existing agent opens the code form directly. Check a complete
code in place; **Continue** is only the form fallback when the script is not
available. **Link agent** confirms the named agent, the account and the files
and projects that will join it. Editing the code clears the preview; a failed
request can be retried. No second sign-in or new key is part of this path.

Confirm **Revoke key** because the old credential cannot be restored. After
revocation offer **Issue new key** without changing the files or projects.
The muted **GitHub** footer appears on the account and device entry pages.
