"""HexType -- a demo for the HexTypeDialog text-entry component.

HexType is a two-tap text entry method for the Tildagon's twelve touch pads (a
left pad picks a group of six characters, a right pad picks one).  The entry
logic lives in ``text_entry.py`` as a reusable ``HexTypeDialog`` that other apps
can drop in exactly like the firmware's ``TextDialog``.

This app is just a thin demo: it opens the dialog, shows whatever you entered,
and lets you edit it again or leave.

    Confirm (C) : open / re-open the entry dialog (and, inside it, return the text)
    Cancel  (F) : leave the demo (inside the dialog, clear or cancel)

See ``text_entry.py`` and the README for the full entry controls.
"""

import asyncio

import app
from events.input import Buttons, BUTTON_TYPES
from app_components.tokens import clear_background

from .text_entry import HexTypeDialog, _wrap

_TEXT = (0.95, 0.96, 1.0)
_HINT = (0.5, 0.55, 0.68)


class HexTypeApp(app.App):
    def __init__(self):
        super().__init__()
        self.buttons = Buttons(self)
        self.dialog = HexTypeDialog(self)
        self.result = None       # last entered text (None before the first entry)

    async def run(self, render_update):
        while True:
            # Open the entry dialog, seeded with whatever's there already.
            self.dialog.text = self.result or ""
            entered = await self.dialog.run(render_update)
            self.buttons.clear()          # drop the key that closed the dialog
            if entered is False:          # cancelled from an empty field
                self.minimise()
                return
            self.result = entered

            # Result screen: C to edit again, F to leave.
            self.buttons.clear()
            while True:
                await render_update()
                if self.buttons.pressed(BUTTON_TYPES["CONFIRM"]):
                    break
                if self.buttons.pressed(BUTTON_TYPES["CANCEL"]):
                    self.minimise()
                    return
                await asyncio.sleep(0.05)

    def draw(self, ctx):
        clear_background(ctx)
        self._draw_result(ctx)
        self.draw_overlays(ctx)          # renders the dialog while it's open

    def _draw_result(self, ctx):
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        ctx.font_size = 16
        ctx.rgb(*_HINT).move_to(0, -96).text("HexType demo")

        if self.result is None:
            ctx.font_size = 18
            ctx.rgb(*_TEXT).move_to(0, -6).text("Press C to type")
        elif self.result == "":
            ctx.font_size = 18
            ctx.rgb(*_HINT).move_to(0, -6).text("(empty)")
        else:
            all_lines = _wrap(self.result)
            lines = all_lines[:5]
            if len(all_lines) > 5:
                lines[-1] = lines[-1] + "…"
            ctx.font_size = 20
            ctx.rgb(*_TEXT)
            y0 = -6 - (len(lines) - 1) * 22 / 2
            for i, line in enumerate(lines):
                ctx.move_to(0, y0 + i * 22).text(line)

        ctx.font_size = 14
        ctx.rgb(*_HINT).move_to(0, 96).text("C edit   F exit")


__app_export__ = HexTypeApp
