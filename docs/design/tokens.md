# Tokens

Colours, type, text size, spacing, radii and control sizes of the service's own
interface, as `src/agent_artifacts/static/site.css` and `pin.css` define them.
Pins have their own page: [pins.md](pins.md).

## Colour

One colour, one meaning. Blue is you and actions, orange a colleague, purple
the agent, green done, red what needs you or went wrong; everything else is
grey. Nothing in `site.css` or the templates is written as a colour value
outside these tokens (`design_check.py` checks it), with two exceptions: the
content frame's `#fff` canvas and the white of pin letters and rings.

Defined on `:root` in `site.css`. The dark values apply under
`@media (prefers-color-scheme: dark)` unless Settings chose Light, and always
with `:root[data-theme="dark"]`. A token without a dark value keeps its light
one.

### Hues

A hue used as text has a value per theme that reads on that theme's
background. Under white text or a white mark it is the `-fill` value, which
holds white at 4.5:1 or more. Orange is only ever a fill.

| Token | Light | Dark | Means, used for |
| --- | --- | --- | --- |
| `--blue` | `#0a64d8` | `#4c9bff` | you and actions: links, link buttons, draft status, decision line, Accept all, project title and "Project page ›", focus outlines, comment-mode ring on the floating ≡ |
| `--blue-fill` | `#0a64d8` | `#0a6edc` | blue under white: send arrow, primary button (dark theme), pressed comment bubble and menu Comment, banner Show, own avatar (the own pin keeps the light value, see below) |
| `--orange` | `#e8890c` | — | a colleague: their pins and avatars |
| `--purple` | `#5e5ce6` | `#9d9bff` | the agent as text: "Agent is working" |
| `--purple-fill` | `#5e5ce6` | — | the agent under white: its avatar, its pins |
| `--green` | `#1e7d36` | `#30d158` | done: "Done · check it", "Done, wrote" |
| `--red` | `#cf291d` | `#ff6b5e` | needs you or went wrong, as text: "Asks you", "spot not found" |
| `--red-fill` | `#cf291d` | — | red under white: error toast |

### One set, one brightness

Colours shown side by side as a set match in lightness and saturation
(OKLCH L and C) wherever that makes sense, so no one of them shouts or
fades: the dot's colours are matched to each other and to the pins' fills;
the text variants of a hue differ only where contrast needs it. To add or
change a colour, match the set it joins (L, C to two decimals:
`uv run --with coloraide`, `Color(hex).convert("oklch")`). A hue that
cannot hold the set's L and stay itself (yellow is muddy at 0.62) takes the
nearest L that keeps it.

| Set | Token | Value | L | C | H |
| --- | --- | --- | --- | --- | --- |
| text, light | `--blue` | `#0a64d8` | 0.53 | 0.19 | 259 |
| | `--purple` | `#5e5ce6` | 0.56 | 0.20 | 278 |
| | `--green` | `#1e7d36` | 0.52 | 0.14 | 148 |
| | `--red` | `#cf291d` | 0.55 | 0.20 | 29 |
| text, dark | `--blue` | `#4c9bff` | 0.69 | 0.17 | 256 |
| | `--purple` | `#9d9bff` | 0.73 | 0.14 | 283 |
| | `--green` | `#30d158` | 0.76 | 0.21 | 147 |
| | `--red` | `#ff6b5e` | 0.71 | 0.18 | 28 |
| pins (fills) | `--blue-fill` (own) | `#0a64d8` | 0.53 | 0.19 | 259 |
| | `--orange` (colleague) | `#e8890c` | 0.72 | 0.16 | 63 |
| | `--purple-fill` (agent) | `#5e5ce6` | 0.56 | 0.20 | 278 |
| the dot | `--dot-red` | `#de4f44` | 0.62 | 0.18 | 28 |
| | `--dot-green` | `#23a136` | 0.62 | 0.18 | 145 |
| | `--dot-yellow` | `#c9a100` | 0.72 | 0.15 | 91 |

### The dot

One notification dot, one colour everywhere it shows: the badge of a pin (on
the content, in a page, in the list, in the Settings preview) and the dot of
a project row. Settings → Dot picks it; theme.js sets `data-dot` on `<html>`
before the page is drawn (`localStorage` `agent-artifacts:dot`: `green` or
`yellow`; no value, or anything else such as an earlier `blue`, is Red).
No blue: blue is you, and a blue dot would vanish on the own pin.

| Token | Value, both themes | Picked by |
| --- | --- | --- |
| `--dot-red` | `#de4f44` | Red, the default |
| `--dot-green` | `#23a136` | `data-dot="green"` |
| `--dot-yellow` | `#c9a100` | `data-dot="yellow"` |
| `--dot` | one of the three | `:root[data-dot]` |

The same in both themes: the dot sits on a pin's white ring (2 px) and is
matched to the pins, not to the text hues (see
[One set, one brightness](#one-set-one-brightness)). Against the page,
ring aside: 4.0 / 3.4 / 2.5 on the light `--bg`, 4.3 / 5.0 / 7.0 on the
dark one, 3.5 / 4.1 / 4.8 on the dark `--surface` (non-text, 3:1 needed);
the yellow on white leans on its white ring. The project row's dot has no
ring: yellow there is 2.5 on the light `--bg` and 2.2 on the light `--surface`
(the current row), below 3:1 — an accepted exception, the colour the
operator chose; the row says "Needs you" to screen readers either way. In a page, where no tokens
reach, pin-marks.js writes the values out.

### Neutrals

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--bg` | `#fff` | `#1c1c1e` | page, comments sheet and panel, project page, file list drop-down |
| `--surface` | `#f2f2f7` | `#2c2c2e` | buttons (header squares, sheet, site), fields, pressed and selected rows, segmented track, type square; dialogs and menu squares in the dark |
| `--surface-2` | `#e5e5ea` | `#3a3a3c` | pressed buttons; fields in dialogs, toast and menu labels in the dark |
| `--line` | `#d1d1d6` | `#48484a` | header bottom border, site button border, sheet grip, ≡ while its menu is open, send arrow disabled |
| `--faint` | `#8e8e93` | `#636366` | rejected pin; selected segment in the dark |
| `--muted` | `#6e6e73` | `#a1a1a6` | secondary text: meta, status lines, section titles, chevrons, placeholders |
| `--fg` | `#1c1c1e` | `#f2f2f4` | text; primary button in the light theme |
| `--on-color` | `#fff` | — | text and icons on a fill: primary, send, toast, banner, menu labels, avatars, floating ≡ |

### Overlays and shadows

| Token | Value | Used for |
| --- | --- | --- |
| `--scrim` | `rgb(0 0 0 / 0.3)` | under the menu, dialogs and the file list |
| `--shadow-1` | `rgb(0 0 0 / 0.12)` | soft: sheet on a phone, dialogs, toast, menu labels and squares, selected segment |
| `--shadow-2` | `rgb(0 0 0 / 0.25)` | firm: pins, floating ≡, banner, file list, inset of a pressed bubble |
| `--glass` | `rgb(28 28 30 / 0.55)` | floating ≡ over the file (blurred), both themes |

### Mixed from the tokens

Not colours of their own: `color-mix()` or another token, so they follow the
theme.

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--hair` | `--fg` at 12% | same | hairlines between rows, panel edge, field focus |
| `--tint` | `--blue` at 8% | `--blue` at 12% | draft rows, unsent messages, Accept all pill; outline fill of a pointed element in a page |
| `--tint-2` | `--blue` at 12% | `--blue` at 18% | the same pressed or selected; 4 px focus ring of a field |
| `--ink` | `rgb(28 28 30 / 0.92)` (`--fg` at 92%, written out so it works without `color-mix()`) | `--surface-2` | toast, "file was updated" banner, menu labels, floating ≡ while open or dragged |
| `--raised` | `--bg` | `--surface` | dialogs, menu squares |
| `--field` | `--surface` | `--surface-2` | inputs, plain buttons and the segmented track in dialogs |
| `--pri` | `--fg` | `--blue-fill` | primary button: dark in the light theme, blue in the dark |
| `--seg-on` | `--bg` | `--faint` | selected segment |
| Undo in the toast | `--blue` 60% with `--on-color` | same | the toast is dark in both themes, so its blue is lightened |

### Where tokens do not reach

Pins (`pin.css`, also drawn inside a page's sandbox), the outline of a
pointed element (`pins.js`) and the plain-text and Markdown pages get no
tokens: they write the **light** values out (`--blue-fill`, `--orange`,
`--purple-fill`, `--faint`, the dot's `--dot-*` (in pin-marks.js;
`--dot-red` as pin.css's fallback), `--shadow-2`, `--fg`,
`--surface`, `--line`, `--muted`, `--tint`) in both themes, since they sit
on the file's own white canvas. Change them together with the tokens.

### Contrast

Text is at least 4.5:1 on the background it sits on, in both themes (white
on `--blue-fill` 5.5 light, 4.9 dark; `--blue` on `--bg` 5.5 / 6.0; `--muted`
on `--surface` 4.5 / 5.4; `--red` on `--bg` 5.3 / 6.1; `--green` on `--bg`
5.2 / 8.4). Known short of it: white letters on an orange pin or avatar
(2.6), white on a rejected pin (3.3), `--muted` on `--tint-2` in the light
theme (4.3, a selected draft set aside) and placeholders in dark dialog
fields (4.4).

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
| 15 | 400 | segmented options in Finish review, menu labels at ≤640 px |
| 14 | bold | header title (h1) |
| 14 | 600 | author in a message |
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

Text size is the way to enlarge the viewer's interface: the browser does not
zoom the viewer page of a page, a Markdown or text file or an image (viewport `maximum-scale=1, user-scalable=no`, and
`touch-action: pan-x pan-y` for browsers that ignore it); a pinch zooms the
file alone (see contract.md, The viewer page). Other pages, and the viewer
of an SVG, a PDF or an archive, keep the browser's zoom.

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
| 50% | send button, avatars, the dot |
| 20 | toast, reply and comment field (pill) |
| 16 | sheet top corners, dialogs, file list bottom corners, banner button, ‹ › and fold buttons |
| 14 | banner, Accept all pill |
| 12 | menu squares, floating ≡, message |
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

Browsers: the tints and hairlines use `color-mix()` (Chrome 111, Safari 16.2, Firefox 113). Older ones lose only those soft fills; the toast, banner and menu labels keep their ground.
