# Tokens

Colours, type, text size, spacing, radii and control sizes of the service's own
interface, as `src/agent_artifacts/static/site.css` and `pin.css` define them.
Pins have their own page: [pins.md](pins.md).

## Colour tokens

Defined on `:root`. The dark values apply under
`@media (prefers-color-scheme: dark)` unless Settings chose Light, and always
with `:root[data-theme="dark"]`. A token without a dark value keeps its light
one.

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--fg` | `#1d1d1f` | `#f2f2f4` | text |
| `--bg` | `#fff` | `#1c1c1e` | page background |
| `--muted` | `#6e6e73` | `#a1a1a6` | secondary text: meta, status lines, section titles, chevrons |
| `--line` | `#d2d2d7` | `#3a3a3c` | header bottom border, plain button border, ≡ while its menu is open |
| `--btn` | `#f2f2f4` | `#2c2c2e` | header square buttons, plain buttons, pressed menu square |
| `--accent` | `#0a64d8` | `#4c9bff` | links and link buttons, draft status, decision line, Accept all |
| `--error` | `#c4281c` | `#ff6b5e` | error toast background, "spot not found" |
| `--fill` | `#f2f2f7` | `#2c2c2e` | sheet buttons, fields, pressed and selected rows, segmented track, type square |
| `--fill2` | `#e9e9ee` | `#3a3a3c` | pressed sheet buttons |
| `--hair` | `rgb(60 60 67 / 0.12)` | `rgb(235 235 245 / 0.16)` | hairline separators between rows, panel edge, focus ring of a field |
| `--pri-bg` | `#1c1c1e` | `#0071e3` | primary button: dark in light theme, blue in dark |
| `--pri-fg` | `#fff` | `#fff` | primary button text |
| `--send` | `#0a84ff` | — | send arrow button, focus outlines in the sheet |
| `--send-off` | `#c7c7cc` | `#3a3a3c` | send arrow button disabled |
| `--own` | `#0a84ff` | — | avatar of this browser's replies |
| `--other` | `#e8890c` | — | avatar of a colleague |
| `--sheet-bg` | `#fff` | `#1c1c1e` | comments sheet and panel, project page, file list drop-down |
| `--raised` | `#fff` | `#2c2c2e` | dialogs, selected segment (light) |
| `--field` | `#f2f2f7` | `#3a3a3c` | inputs, plain buttons and the segmented track in dialogs |
| `--grip` | `#d1d1d6` | `#545458` | sheet grip bar |
| `--draft-bg` | `rgb(10 132 255 / 0.08)` | `rgb(10 132 255 / 0.16)` | draft rows, unsent messages, Accept all pill |
| `--draft-bg2` | `rgb(10 132 255 / 0.16)` | `rgb(10 132 255 / 0.26)` | the same, pressed or selected |
| `--toast-bg` | `rgb(28 28 30 / 0.92)` | `rgb(58 58 60 / 0.96)` | toast, "file was updated" banner |
| `--fan-label` | `rgb(28 28 30 / 0.92)` | `#48484a` | menu item labels |
| `--fan-sq` | `#fff` | `#2c2c2e` | menu item squares |
| `--float-open` | `#1c1c1e` | `#3a3a3c` | floating ≡ while its menu is open |
| `--seg-on` | — (falls back to `--raised`) | `#636366` | selected segment |
| `--plink` | `var(--accent)` | `#64b5ff` | project title in the header, "Project page ›" |
| `--ts` | `1.15` | — | text size factor, see [Text size](#text-size) |

### Fixed colours

Written as values, not tokens.

| Value | Where |
| --- | --- |
| `#0a84ff` | header bubble pressed, comment mode ring and bubble on the floating ≡, menu Comment pressed, focus outlines in header and menu, banner Show button |
| `#ff3b30` | red dot badge, "waiting for you" pill on the project page |
| `#5e5ce6` | agent pin, agent avatar, agent counter on the project page (dark: `#9d9bff`) |
| `#e8890c` / `#0a84ff` | colleague / own pin |
| `#8e8e93` | rejected pin, placeholder text |
| `#64b5ff` | Undo in the toast |
| `rgb(28 28 30 / 0.55)` | floating ≡ (`0.75` while dragged) |
| `rgb(0 0 0 / 0.28)` / `0.25` / `0.35` | scrim under the menu / dialogs / file list |
| `#fff` | content frame background, so a file without its own background is never transparent |

Status words in a row's second line (see [tone-of-voice.md](tone-of-voice.md#status-words)):

| Class | Light | Dark | Words |
| --- | --- | --- | --- |
| `.st b.fin` | `#248a3d` | `#30d158` | Done · check it, Done, wrote |
| `.st b.ask` | `#b25e00` | `#ff9f0a` | Asks you |
| `.st b.work` | `#5e5ce6` | `#7d7aff` | Agent is working |
| `.st b.mine` | `--accent` | `--accent` | ✓ Approve, ✕ Reject, ✓ Accept, ↩ Your reply |

## Type

| Font stack | Where |
| --- | --- |
| `system-ui, sans-serif` | body, header, header buttons, menu, project page |
| `-apple-system, BlinkMacSystemFont, "SF Pro Text", Roboto, "Segoe UI", sans-serif` | sheet, dialogs, toast (antialiased) |
| `-apple-system, system-ui, Roboto, sans-serif` | pin letters |
| `ui-monospace, monospace` 13px | setup text on the key page |

Base: `15px/1.4` on `body`; the sheet `15px`, line-height `1.35`.

Sizes in use, in CSS px before [text size](#text-size):

| Size | Weight | Where |
| --- | --- | --- |
| 20 | bold (h1) | invite and key page title |
| 16 | 400 | row text, message text, text fields, project row title, "Project page ›" (600) |
| 16 | 600 | dialog title |
| 15 | 600 | sheet title line (`.ttl`) |
| 15 | 400 | segmented options in Finish review, menu labels at ≤640 px; agent counter (600) |
| 14 | bold | header title (h1) |
| 14 | 600 | author in a message, "waiting for you" counter |
| 14 | 400 | sheet buttons, strip line, status line, empty text, menu labels, Settings labels and segments, banner |
| 13 | 600 | section titles (uppercase, letter-spacing 0.02em), Accept all pill and link |
| 13 | 400 | plain buttons, strip hint, caption, decision line, "Approved by" line, time in a message, item navigation label, toast, notes; avatar letter and list pin letter (600) |
| 12 | 400 | header meta line; project title in the header (600) |
| 11 | 700 | file type square on the project page |

## Text size

Settings → Text sets `data-text-size` on `<html>` (theme.js, before the page is
drawn; kept as `agent-artifacts:text-size`, no value for Large).

| Option | `--ts` |
| --- | --- |
| Normal | 1 |
| Large (default) | 1.15 |
| Larger | 1.3 |

`zoom: var(--ts)` is set on: `body > header`, `.sheet > *`, `.toast-box`,
`.fan-item`, `.modal`, `.banner > *`, `.pj > *`. The desktop panel width is
`360px × --ts`; the banner's top is `15px + 45px × --ts`.

Not zoomed: the file (frame or image), the pins on it, the floating ≡ (44 px
as is), the sheet's own box (its height is set in px by drags), the invite,
key and message pages. A browser without `zoom` keeps `--ts: 1`.

## Spacing

| Where | Value |
| --- | --- |
| Header | padding 4 8 4 12, gap 8; buttons gap 12 |
| Sheet body | padding 0 14 10; desktop top 12 |
| Sheet footer | padding 6 12 (8 + safe area) |
| Strip | padding 4 8 4 14, gap 8 |
| List row | padding 8 6, gap 14 (pin to text) |
| Message | padding 7 8, gap 9 |
| Message actions | gap 8, top 8; under a thread indented 37 |
| Section title | margin 12 0 2 (project page 16 0 4) |
| Menu | 10 between items, 10 between label and square, 8 from its button |
| Dialog | padding 16; buttons gap 8, top 12; scrim padding 60 12 12 |
| Toast | 8 above the sheet; on a computer 12 from the edges, 20 from the bottom |
| Project page | padding 6 14 24; row padding 8 6, gap 14 |

## Radii

| Radius | Where |
| --- | --- |
| 50% | send button, avatars, red dot |
| 20 | toast, reply and comment field (pill) |
| 16 | sheet top corners, dialogs, file list bottom corners, banner button, ‹ › and fold buttons |
| 14 | banner, Accept all pill |
| 12 | menu squares, floating ≡, message, project counter pill |
| 10 | sheet buttons, rows, fields, segmented track, menu labels |
| 9 | header square buttons, file type square |
| 8 | selected segment |
| 6 | plain `button` (invite and key pages) |

## Control sizes

Visual size and touch target. The touch target comes from an invisible
`::before` that overhangs the control.

| Control | Visual | Touch |
| --- | --- | --- |
| Header square button | 32 × 32 | 44 × 44 |
| Sheet button `.btn` | h 32 (36 for `.big`) | h 46 |
| ‹ › and fold | 32 × 32 | 44 × 44 |
| Send arrow | 30 × 30 | 44 × 44 |
| Accept all pill | h 28 | h 44 |
| Segmented option (Finish review) | h 36 | h 44 |
| Settings segment | h 40 | h 44 |
| Menu item | h 44, square 40 × 40 | h 44 |
| Floating ≡ | 44 × 44 | 44 × 44 |
| List row | min h 44 | the row |
| Folded group title | min h 44 | the row |
| Project row | min h 60 | the row |
| Toast Undo | h 30 | h 44 |
| Banner Show | h 32 | h 44 |
| Text field | min h 40 (grows to 120 in a pill) | the field |
| Pin on the content | 30 (Large 38) | +10 on each side |

## Layout

| Breakpoint | Layout |
| --- | --- |
| < 900 px | comments in a bottom sheet under the content |
| ≥ 900 px | comments in a panel at the right, `360px × --ts` wide; no grip |
| ≤ 640 px | image fits the width and scrolls; menu labels 15 px; file list full width |
| ≥ 641 px | file list drop-down 440 px wide, 8 px from the left |

Header: one row, 32 px buttons, two text lines (14 px title, 12 px meta, line
height 1.4); the banner below it assumes 45 px.

Sheet heights on a phone (`data-pos`):

| Height | CSS | Drag snaps to |
| --- | --- | --- |
| strip | min 44 px, one line | 44 px |
| ¼ (`q`) | its content, max 52% | 30% of the stage |
| ½ (`h`) | 52% | 52% |
| full (`f`) | 100%, no rounded top | 100% |

¼ and ½ overlap the content by 16 px (their rounded top). Let go below 75% of
the ¼ height while dragging down, the sheet folds to the strip.

Layers (`z-index`): sheet 5, banner 30, menu 40, floating ≡ 45, file list 46,
toast 60, dialogs 70.

## Motion

| What | Duration |
| --- | --- |
| Background and opacity changes | 0.15 s |
| Scrim fade | 0.18 s |
| Toast in and out | 0.2 s, 6 px rise |
| Menu items | 0.22 s, staggered 0.03 s |
| Press | scale 0.97 (sheet buttons), 0.95 (header), 0.92 (send) |
| Strip pulse | 0.6 s |

`prefers-reduced-motion: reduce` turns off the toast, strip, menu, scrim and
file list animations.
