# HexType

A [Tildagon](https://tildagon.badge.emfcamp.org/) text-entry method that uses the
twelve front-board touch pads and the joystick — packaged as a reusable dialog
(`HexTypeDialog`, in `text_entry.py`) plus a small demo app (`app.py`).

Other apps can drop the dialog in exactly like the firmware's `TextDialog`:

```python
from .text_entry import HexTypeDialog

class MyApp(app.App):
    async def run(self, render_update):
        text = await HexTypeDialog(self, initial="").run(render_update)
        if text is not False:          # False means the user cancelled
            ...
```

The host app only needs `self.overlays` (the `app.App` base provides it) and to
call `self.draw_overlays(ctx)` in its `draw` — the dialog paints itself as a
full-screen overlay while open and returns the entered string from `run()`.

Like the native `TextDialog`, it's **event-driven**: it subscribes to button
events in `__init__`, so it also works in the *callback* style (no `await`), which
is how many apps use it:

```python
self.overlays = [HexTypeDialog(self, on_complete=self._got, on_cancel=self._cancelled)]
```

As with the native dialog, `on_complete` / `on_cancel` are called with **no
arguments** — the handler reads the entered string off the dialog's `.text`.

`HexTypeDialog(app, on_complete=None, on_cancel=None, initial="", keep_groups=True,
use_leds=True, message="", masked=False, left_colour="yellow",
right_colour="green")` — the options are described below and can be set on the
instance between `run()`s. `left_colour`/`right_colour` are palette names (see
`PALETTE` in `text_entry.py`) applied to both the highlight and the LED.

The pads split into two arcs of six. The **left** arc (pads 12, 11, 10, 9, 8, 7)
picks a *group* of six characters; the **right** arc (pads 1–6) then picks one
*character* from that group. Every character is two taps — a left pad, then a
right pad.

## The layout

| Left pad | 12 | 11 | 10 | 9 | 8 | 7 |
|----------|----|----|----|----|----|----|
| Group    | row 1 | row 2 | row 3 | row 4 | row 5 | row 6 |

| Right pad | 1 | 2 | 3 | 4 | 5 | 6 |
|-----------|---|---|---|---|---|---|
| Column    | 1 | 2 | 3 | 4 | 5 | 6 |

**Standard set** (unshifted):

```
abcdef   ghijkl   mnopqr   stuvwx   yz1234   567890
```

So tap **12 then 1 → `a`**, tap **7 then 6 → `0`**.

All six groups are shown around the **left** pads — each sitting on its own pad as
two rows of three characters, so you can see which pad holds which group. When you
tap a group the others dim to grey and the selected one turns yellow (matching its
LED), and that group's six characters appear out on the **right** pads, one per
pad, so you can see which pad enters which character. Tapping the selected group's
pad again deselects it. The text you've typed sits in the middle of the screen,
with the shift arrow just above it.

## Layouts

The character groups come from a **layout** defined in `layouts.py`. The default
is **alphabetical** (the `abcdef / ghijkl / …` groups above). Each layout has a
`standard` set and, optionally, `upper` (joystick up) and `symbol` (joystick down)
sets; a missing shifted set falls back to `standard`. Add your own by appending a
dict to `LAYOUTS` in `layouts.py` — the order there is the cycle order in the
settings menu, and the first is the default. `frequency` and `numeric` ship as
examples.

Rows may be shorter than six characters. When they are, the characters sit on the
**central** pads rather than starting from pad 1, keeping reaches short:

| Chars in row | 6 | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|---|
| Right pads used | 1–6 | 2–6 | 2–5 | 3–5 | 3–4 | 3 |

## Shift sets (one-shot)

The joystick shifts the character set for **the next character only** — after you
enter a character it snaps back to the standard set.

- **Up** → upper set: `ABCDEF GHIJKL MNOPQR STUVWX YZ!@£€ %^&*()`
- **Down** → symbol set: `§±-_=+ []{}<> ‘“`\/| .,?~;:` (only four groups — the
  bottom two left pads are inactive here)

Press up (or down) again to cancel the shift without typing. The standard set
shows nothing; the upper set shows an **↑** in the middle, the symbol set a **↓**.

## Controls

- **Left arc** (pads 12–7) — pick a group of six; tap the same pad again to deselect.
- **Right arc** (pads 1–6) — enter a character from the selected group.
- **Joystick up / down** — one-shot shift to the upper / symbol set.
- **Joystick left** — backspace (hold to repeat).
- **Joystick right** — space.
- **Confirm** (C) — return the entered text to the caller (see below).
- **Cancel** (F) — clear the text; when it's already empty, cancel the dialog
  (returns `False`).

The hex buttons A/B/D/E mirror the joystick up/right/down/left directions, so
they work for shift / space / backspace too.

## Confirming and the review screen

Pressing **C** returns the text to the caller. If the whole string already fits
the edit screen (three lines or fewer) it returns straight away. If it's longer,
the first **C** shows a **review screen** first — the whole string rendered across
the full round display, up to eight lines, each as wide as the circle allows at
its height (so the middle lines hold more than the top and bottom ones), measured
with `ctx.text_width` to fit exactly. A second **C** then returns it, or **F**
takes you back to editing. If the text is longer than eight lines will hold, the
last line ends with an ellipsis.

## The app: settings + test

`app.py` launches straight into a settings screen (Up/Down to move, **C** to
select/toggle, **F** to leave). Settings persist to the badge's `/settings.json`.

- **Layout** — the character layout (from `layouts.py`); cycles through the
  available layouts. Defaults to alphabetical.
- **Keep groups** — on: when you select a group the others dim to grey and the
  selected one turns yellow, staying on screen. Off: the group chart is hidden
  while a group is selected and only its six characters show on the right.
- **Ring LEDs** — on: the dialog drives the ring LEDs (taking them off the badge's
  background pattern). Off: the LEDs are left alone (the OS pattern keeps running).
- **Left colour** — the colour of the selected group (its on-screen highlight and
  its LED). Cycles through a small palette; defaults to yellow.
- **Right colour** — the colour of the character choices (on-screen and their
  LEDs). Cycles through the same palette; defaults to green.
- **Override kbd** — on: replace the badge's native on-screen keyboard with
  HexType system-wide (see below). Off: restore the native keyboard.
- **Test input** — open the HexType dialog to try it; whatever you enter is shown
  back on the settings screen.

## Overriding the native keyboard

`text_entry.py` provides `HexTextDialog`, a drop-in with the same signature and
return contract as the firmware's `app_components.TextDialog`
(`TextDialog(message, app, masked=False, on_complete=None, on_cancel=None)`,
`await run()` → string or `False`). Turning **Override keyboard** on calls
`install_override()`, which points `app_components.TextDialog` at `HexTextDialog`
and rebinds it in every already-imported module — so other apps that pop up a
text field use HexType instead of the native keyboard. Turning it off restores
the original.

Caveats (it's a runtime monkey-patch, not a firmware change):

- **It doesn't survive a reboot** — no user-app code runs at boot, so the override
  is re-applied whenever HexType is launched with the setting on. Launch HexType
  once after a reboot to re-enable it.
- It doesn't provide the **physical-keyboard** path the native dialog has, and it
  needs the **2026 front board** (the touch pads) to actually type.

When a caller passes `masked=True` (password fields), the text shows as `*` and
the review screen is skipped.

## Ring LEDs

While a group is selected its left pad glows in the **left colour** and the six
right pads glow in the **right colour** (yellow and green by default) — the pads
you can now tap to enter a character. The dialog takes the LEDs off the badge's
background pattern while it's open and restores them when it closes.

## A note on glyphs

Some of the symbol-set characters (`£ € § ± ‘ “` and friends) may not exist in
the badge's built-in font and can render as blanks on screen — but they still go
into the text buffer correctly.

## Installing (local sideload)

The badge OS has no TOML parser, so a dev-only `metadata.json` points at the
Python import path. The on-badge folder **must** be `hextype` (matching
`apps.hextype.app`).

```bash
mpremote connect /dev/cu.usbmodem83101 fs cp app.py text_entry.py layouts.py metadata.json tildagon.toml :/apps/hextype/
mpremote connect /dev/cu.usbmodem83101 reset
```

Then pick **HexType** from the **Apps** menu. (Remove `metadata.json` before
publishing to the app store — `tildagon.toml` is the store manifest.)

To reuse the dialog in another app, copy `text_entry.py` **and `layouts.py`** into
that app's folder and `from .text_entry import HexTypeDialog`.

## Requirements

Needs a **2026 front board** (the one with the twelve touch pads).
