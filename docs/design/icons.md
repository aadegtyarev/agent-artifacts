# Icons

Line icons on a 24 × 24 grid: no fill, `currentColor` stroke 2, round caps and
joins (`.icon` in `site.css`), drawn 18 px unless the place sets another size.
Sources: `templates/icons.html` (Jinja macros), the `ICONS` map in
`static/comments.js` and the star in `static/pin-marks.js`. Each file in
[icons/](icons/) is the same path and viewBox as the code.

| Icon | Name | Source | Size | Where |
| --- | --- | --- | --- | --- |
| ![bubble](icons/bubble.svg) | `bubble` | icons.html | 19 header, 20 menu, 12 filled blue on the floating ≡ | comment mode: header button, menu Comment (full screen), comment-mode mark on the floating ≡ |
| ![expand](icons/expand.svg) | `expand` | icons.html | 19 | header Full screen |
| ![shrink](icons/shrink.svg) | `shrink` | icons.html | 20 | menu Exit full screen |
| ![menu](icons/menu.svg) | `menu` | icons.html | 19 header, 22 floating | ≡ in the header and the floating ≡ |
| ![corners](icons/corners.svg) | `corners` | icons.html | 44 (the whole button), stroke 1, 70% | floating ≡ at rest: two corners of Exit full screen, the hint that a double tap leaves full screen |
| ![close](icons/close.svg) | `close` | icons.html | 22 | floating ≡ while its menu is open |
| ![files](icons/files.svg) | `files` | icons.html | 20 | menu Project files (dots drawn with stroke 3) |
| ![external](icons/external.svg) | `external` | icons.html | 20 | menu Open separately |
| ![share](icons/share.svg) | `share` | icons.html | 20 | menu Share… |
| ![settings](icons/settings.svg) | `settings` | icons.html | 20 | menu Settings… |
| ![download](icons/download.svg) | `download` | icons.html | 18 | Share dialog: Download file |
| ![check](icons/check.svg) | `check` | icons.html | 18 | key page: replaces "Copy" for a moment after copying |
| ![chevron](icons/chevron.svg) | `chevron` | icons.html | 10, 10 × 10 grid, stroke 1.6 | after the project title in the header |
| ![send](icons/send.svg) | `send` | comments.js | 18, stroke 2.4 | the round send button in the comment and reply fields |
| ![left](icons/left.svg) | `left` | comments.js | 18 | item view: previous |
| ![right](icons/right.svg) | `right` | comments.js | 18 item view, 15 group title | item view: next; a folded group title |
| ![down](icons/down.svg) | `down` | comments.js | 18 fold, 15 group title | fold button (⌄) on a phone; an open group title |
| ![star](icons/star.svg) | `star` | pin-marks.js | 12 × 12 grid, filled `currentColor`; 20 in a pin, 18 in a list pin, 19 in the agent avatar | the agent: pins, avatar, Settings preview |

## Text glyphs

Some marks are characters in the label, not icons.

| Glyph | Where |
| --- | --- |
| ‹ › | "‹ All" in the item view on a computer; "›" at the end of the strip line, after "Project page" |
| ≡ | list pin of a person's comment about the whole file |
| ✓ ✕ | Approve, Reject, Accept and their decision lines; "✓ All · N" |
| ↩ | "↩ Your reply" status, avatar of an unsent reply |
| + | "+ About the whole file" |
| · | separator in titles, counts and status lines |
| … | menu items that open a dialog (Share…, Settings…), field placeholders |
| “ ” | quoting an element's text or a screen's name |
