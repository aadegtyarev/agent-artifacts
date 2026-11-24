# Design

The shipped interface: the viewer, comments, project pages, invitations and
key pages. The files people share have their own design. Plans and mockups for
unshipped screens do not define the shipped interface. The service contract is
in [contract.md](../contract.md).

## Principles

- Start at phone width. The viewer moves comments to a side panel at 900 px.
- Keep the file in focus: soft fills, hairline separators and muted secondary
  text. Keep a visible keyboard focus indicator.
- Use the light and dark palettes for the service, not for the shared file.
- Aim for readable text of at least 15 px on phones and a 44 px touch target.
  Use the existing [component sizes and exceptions](tokens.md#control-sizes):
  the visible control, touch target and rendered size can differ.
- Use one primary action per view where possible: dark in the light theme,
  blue in the dark.
- Give feedback for every action. For reversible changes, prefer
  [act, then Undo](tone-of-voice.md#action-feedback-and-undo) to confirmation.
- Colours of one set share brightness and saturation (OKLCH L and C)
  wherever that makes sense ([tokens.md](tokens.md#one-set-one-brightness)).

## Reuse before adding

1. Find the closest [component](components.md) and use its existing template or
   markup, CSS variant and tokens. Do not recreate the same button, field or
   state under a new selector. If two surfaces need the same new variant,
   extract it once and use it in both.
2. Base new entry pages on the [invite and key pages](components.md#invite-key-and-message-pages)
   and reuse the [fields and actions](components.md#buttons) where those fit.
   Confirm the missing states before introducing a new variant; do not use a
   dialog container on a full page just to inherit its styles.
3. For a self-contained mockup, inline the relevant product styles rather than
   guessing at sizes. Check the result on a phone and a desktop, in both
   themes, with the default Text size and with it enlarged.

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
[pins.md](pins.md). The script does not check control dimensions, interaction
states or contrast. Check those in a browser. Update the matching design files
in the same pull request as an approved UI change.
