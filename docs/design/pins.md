# Pins

A pin marks the spot a comment is about, on an image or on an element of a
page. The same rules (`static/pin.css`, `static/pin-marks.js`) draw pins on
the content and the small pins in the comments list; the state comes from
`stateOf()` in `static/comments-model.js`.

A pin has a tone (whose), a fill (how far along), a mark inside and at most
one badge.

Pins take the light values of the tokens in both themes ([tokens.md](tokens.md#where-tokens-do-not-reach)):
they are drawn inside a page's sandbox, where no tokens reach, and sit on the
file's own canvas. Letters, marks and the ring are white.

## Tone: whose

| Tone | Class | Colour | Means |
| --- | --- | --- | --- |
| own | `.own` | `#0a64d8` blue (`--blue-fill`) | written from this browser, approved, or decided by this person in the current review |
| colleague | (none) | `#e8890c` orange (`--orange`) | someone else's |
| agent | `.ag` | `#5e5ce6` purple (`--purple-fill`) | sent to the agent and not decided yet |

## Fill: how far along

| Fill | Class | Look | Means |
| --- | --- | --- | --- |
| line | `.line` | white with a 2 px edge and mark in the tone | not done: a draft, awaiting approval, `waiting`/`working`/`asked`, or answered and replied to by this person |
| solid | (none) | the tone, white mark, 2 px white ring | done: in force, `done`/`wrote` |
| rejected | `.rej` | grey `#8e8e93` (`--faint`) | rejected, or a reject decision in this review |

## Mark inside

| Mark | When |
| --- | --- |
| first letter of the author's name, uppercase | a person's comment; "?" when the name is empty or starts with "*" |
| star (4-point, [icons/star.svg](icons/star.svg)) | the agent has it, or this person accepted it in the current review |
| ≡ (text) | list pin of a person's comment about the whole file |

## Badge

One only: the dot, `--dot` (red `#de4f44` unless Settings → Dot chose green
or yellow, see [tokens.md](tokens.md#the-dot)), 14 px with a 2 px white ring
at the top right (11 px with a 1.5 px white ring in the list); the ring is
white everywhere, in both themes. In a page the viewer sends the colour's
name with the pins (`dot`); pins.js takes only `red`, `green` or `yellow`,
anything else is red, and sets it on its layer even when red, so the page's
own `--dot` never reaches the badge. It shows for the agent's question (`asked`) and for a
colleague's reply not yet seen in this browser.

## States

| Comment | Tone | Fill | Mark | Badge |
| --- | --- | --- | --- | --- |
| spot of the draft being written | own | line | empty | — |
| new draft | own | line | letter | — |
| a colleague's, awaiting approval | colleague | line | letter | — |
| mine or approved, no agent state | own | solid | letter | — |
| `waiting`, `working` | agent | line | star | — |
| `asked` | agent | line | star | dot |
| `done`, `wrote` | agent | solid | star | — |
| answered, with my reply draft | own | line | star | — |
| rejected | own if mine or approved, else colleague | rejected | letter | — |
| my approve draft | own | solid | letter | — |
| my reject draft | own if mine or approved, else colleague | rejected | letter | — |
| my accept draft | own | solid | star | — |
| any, with an unseen colleague reply | unchanged | unchanged | unchanged | dot |

## Sizes

| Pin | Normal | Large (Settings) |
| --- | --- | --- |
| on the content | 30 × 30, mark 20, letter 14/600 | 38 × 38, mark 25, letter 17 |
| selected (`.lit`) | 36 × 36, mark 23, letter 16, a soft halo of the pin's own colour (35 %, 7 px; a rejected pin's grey) | 45 × 45, mark 29, letter 20 |
| in the list | 28 × 28, mark 18, letter 13 | 35 × 35, mark 22, letter 16 |

Every pin on the content has a 44 px+ touch area (`::before`, 10 px around).
Pins keep these sizes when the file is zoomed; only their places follow it.

## Shape

The sharp 4 px corner marks the spot. Near the right or top edge a pin turns
(`.left`, `.down`) and the sharp corner moves with it.

| Settings → Mine / others | own | colleague | agent |
| --- | --- | --- | --- |
| Color | droplet `50% 50% 50% 4px` | droplet | droplet |
| Color + shape (default) | droplet `50% 50% 50% 4px` | two cut corners `50% 4px 50% 4px` | rounded square `6px 6px 6px 2px` |

The setting is kept as `agent-artifacts:pin-shapes` (`color`, or no value for
Color + shape) and applied as the class `pins-shaped` on `<html>`. Settings →
Pins: Large is the class `pins-big` (`agent-artifacts:pin-size` `large`).
Settings shows a preview: A (own), B (colleague), the agent's star with
the dot, so a change of Dot shows there too, with "mine · a colleague's ·
the agent's".

## Elsewhere

| Where | Look |
| --- | --- |
| Agent avatar in a comment's chat | 28 px circle in `--purple-fill`, white star 19 |
| Person avatar | 28 px circle, initial 13/600 white; `--orange`, `--blue-fill` for this browser; "↩" on an unsent reply |
| Project page | no pins: a row has only the dot, see [components.md](components.md#project-page) |
| Floating ≡ in comment mode | 12 px `--blue` bubble at the top right |
