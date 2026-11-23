# Design

The look and the words of the service's own interface (the viewer, the
comments sheet, the project page and the invite pages) as the code has them.
Every mockup and every UI change follows these files; a change that alters the
look or the words updates them in the same pull request. Behaviour is in
[contract.md](../contract.md); these files do not repeat it.

## Principles

- Phone first: designed at phone width, then the computer gets a side panel.
- The file is the content; the service's chrome stays calm: soft fills, no
  outlines, hairline separators, muted secondary text.
- The dark theme is its own palette (dark grey surfaces, lighter raised ones,
  a lighter blue), not an inversion. The file is shown as its author made it.
- Text at least 15 px on a phone; the default text size is Large (×1.15).
- Every control is at least 44 px to a finger, whatever it shows (32 px is
  the usual visual size).
- One primary button per view: dark in the light theme, blue in the dark.
- Every action gets feedback: a pressed state, a toast, a status line or a
  changed row.

## Files

| File | What it holds |
| --- | --- |
| [tokens.md](tokens.md) | colours (light and dark), type, text size, spacing, radii, control sizes, layout, motion |
| [components.md](components.md) | header, menu, floating ≡, dialogs, toast, buttons, segmented control, comments sheet, list groups, rows, item view, compose, Finish review, project page, file list, empty states |
| [pins.md](pins.md) | pin tone, fill, mark, badge, states, sizes and shapes |
| [icons.md](icons.md) | every icon with its size and place; the SVGs are in [icons/](icons/) |
| [tone-of-voice.md](tone-of-voice.md) | who we talk to, voice rules, glossary, status words, buttons, empty states, toasts, errors, counts |

## Keeping it true

`skill/agent-artifacts/scripts/design_check.py` compares the code with these
files: every colour in the CSS and in the UI files' styles must be in
[tokens.md](tokens.md), no phrase from the "Avoid" column of the
[tone-of-voice.md](tone-of-voice.md#glossary) glossary may appear in the
interface's text, and every icon in [icons/](icons/) must be in
[icons.md](icons.md). `tests/test_design.py` runs it on this repository and
also checks that every pin class in `pin.css` is described in
[pins.md](pins.md). The rule, in the skill: when a person accepts a change to
the interface, the same pull request updates these files, the reply says
what changed in them, and the reviewer checks that they match the code.
