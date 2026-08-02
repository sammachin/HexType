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

Before you pick a group, all six groups are shown around the **left** pads — each
sitting on its own pad as two rows of three characters, so you can see which pad
holds which group. Once you tap a group those disappear and that group's six
characters appear out on the **right** pads, one per pad, so you can see which pad
enters which character. Tapping the selected group's pad again deselects it and
brings the groups back. The text you've typed sits in the middle of the screen,
with the shift arrow just above it.

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

## The demo app

`app.py` is a thin demo of the dialog: press **C** to open the entry dialog, and
whatever you enter is shown on a result screen where **C** re-opens it for editing
and **F** leaves. All the interesting behaviour lives in `text_entry.py`.

## Ring LEDs

While a group is selected its left pad glows amber and the six right pads glow
green — the pads you can now tap to enter a character. The dialog takes the LEDs
off the badge's background pattern while it's open and restores them when it
closes.

## A note on glyphs

Some of the symbol-set characters (`£ € § ± ‘ “` and friends) may not exist in
the badge's built-in font and can render as blanks on screen — but they still go
into the text buffer correctly.

## Installing (local sideload)

The badge OS has no TOML parser, so a dev-only `metadata.json` points at the
Python import path. The on-badge folder **must** be `hextype` (matching
`apps.hextype.app`).

```bash
mpremote connect /dev/cu.usbmodem83101 fs cp app.py text_entry.py metadata.json tildagon.toml :/apps/hextype/
mpremote connect /dev/cu.usbmodem83101 reset
```

Then pick **HexType** from the **Apps** menu. (Remove `metadata.json` before
publishing to the app store — `tildagon.toml` is the store manifest.)

To reuse the dialog in another app, copy `text_entry.py` into that app's folder
and `from .text_entry import HexTypeDialog`.

## Requirements

Needs a **2026 front board** (the one with the twelve touch pads).
