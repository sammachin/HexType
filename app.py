"""HexType -- settings + test harness for the HexTypeDialog text-entry method.

The entry logic lives in ``text_entry.py`` as a reusable ``HexTypeDialog`` (and a
``TextDialog``-compatible ``HexTextDialog``).  This app launches into a settings
screen where you can toggle features (persisted to the badge's settings.json),
try the input with **Test input**, and turn on **Override keyboard** to make the
badge's other apps use HexType wherever they'd normally pop up the native
on-screen keyboard.

Controls: Up/Down move, C selects/toggles, F leaves.
"""

import asyncio

import app
from events.input import Buttons, BUTTON_TYPES
from app_components.tokens import clear_background

from .text_entry import (
    HexTypeDialog,
    get_setting,
    set_setting,
    get_colour_name,
    set_colour_name,
    colour_screen,
    get_layout_name,
    set_layout_name,
    apply_override_setting,
    PALETTE_NAMES,
    DEFAULT_LEFT_COLOUR,
    DEFAULT_RIGHT_COLOUR,
    SETTING_KEEP_GROUPS,
    SETTING_USE_LEDS,
    SETTING_OVERRIDE,
    SETTING_LEFT_COLOUR,
    SETTING_RIGHT_COLOUR,
    SETTING_LAYOUT,
    _wrap,
)
from .layouts import layout_names

_TEXT = (0.95, 0.96, 1.0)
_HINT = (0.5, 0.55, 0.68)
_SEL = (1.0, 0.82, 0.10)


def _cycle(current, names):
    """Next name in the list, wrapping; falls back to the first."""
    idx = names.index(current) if current in names else -1
    return names[(idx + 1) % len(names)]

# Menu: (kind, key, label).  "toggle" flips a persisted bool; "colour" cycles a
# palette name; "layout" cycles a layout name; "action" runs code.
_ITEMS = [
    ("layout", SETTING_LAYOUT, "Layout"),
    ("toggle", SETTING_KEEP_GROUPS, "Keep groups"),
    ("toggle", SETTING_USE_LEDS, "Ring LEDs"),
    ("colour", SETTING_LEFT_COLOUR, "Left colour"),
    ("colour", SETTING_RIGHT_COLOUR, "Right colour"),
    ("toggle", SETTING_OVERRIDE, "Override kbd"),
    ("action", "test", "Test input"),
]


class HexTypeApp(app.App):
    def __init__(self):
        super().__init__()
        self.buttons = Buttons(self)
        self._sel = 0
        self.last_text = None

        # Load persisted settings and apply the override state on launch.
        self.opts = {
            SETTING_LAYOUT: get_layout_name(),
            SETTING_KEEP_GROUPS: get_setting(SETTING_KEEP_GROUPS, True),
            SETTING_USE_LEDS: get_setting(SETTING_USE_LEDS, True),
            SETTING_OVERRIDE: get_setting(SETTING_OVERRIDE, False),
            SETTING_LEFT_COLOUR: get_colour_name(SETTING_LEFT_COLOUR, DEFAULT_LEFT_COLOUR),
            SETTING_RIGHT_COLOUR: get_colour_name(SETTING_RIGHT_COLOUR, DEFAULT_RIGHT_COLOUR),
        }
        apply_override_setting()

    async def run(self, render_update):
        # NOTE: run() must loop forever.  await render_update() blocks while we're
        # backgrounded and returns True when we regain focus; minimise() just
        # backgrounds us -- returning from run() would kill the app so re-opening
        # it (a foreground push, not a fresh start) would show a dead screen.
        while True:
            regained = await render_update()
            if regained:
                self.buttons.clear()      # drop stale presses from before we left

            if self.buttons.pressed(BUTTON_TYPES["CANCEL"]):
                self.buttons.clear()
                self.minimise()
                continue                  # loop; render_update now blocks until re-shown
            if self.buttons.pressed(BUTTON_TYPES["UP"]):
                self._sel = (self._sel - 1) % len(_ITEMS)
            if self.buttons.pressed(BUTTON_TYPES["DOWN"]):
                self._sel = (self._sel + 1) % len(_ITEMS)
            if self.buttons.pressed(BUTTON_TYPES["CONFIRM"]):
                await self._select(render_update)

            await asyncio.sleep(0.02)

    async def _select(self, render_update):
        kind, key, _label = _ITEMS[self._sel]
        if kind == "toggle":
            self.opts[key] = not self.opts[key]
            set_setting(key, self.opts[key])
            if key == SETTING_OVERRIDE:
                apply_override_setting()
        elif kind == "colour":
            self.opts[key] = _cycle(self.opts[key], PALETTE_NAMES)
            set_colour_name(key, self.opts[key])
        elif kind == "layout":
            self.opts[key] = _cycle(self.opts[key], layout_names())
            set_layout_name(self.opts[key])
        else:  # "test": open a fresh dialog (it's one-shot, like TextDialog)
            dialog = HexTypeDialog(
                self,
                keep_groups=self.opts[SETTING_KEEP_GROUPS],
                use_leds=self.opts[SETTING_USE_LEDS],
                left_colour=self.opts[SETTING_LEFT_COLOUR],
                right_colour=self.opts[SETTING_RIGHT_COLOUR],
                layout=self.opts[SETTING_LAYOUT],
                message="Test input",
            )
            result = await dialog.run(render_update)
            # Wait for the confirming C to lift so it doesn't re-open the test.
            while self.buttons.get(BUTTON_TYPES["CONFIRM"]):
                await render_update()
                await asyncio.sleep(0.02)
            self.buttons.clear()
            if result is not False:
                self.last_text = result

    # ---- drawing ----------------------------------------------------------

    def draw(self, ctx):
        clear_background(ctx)
        self._draw_settings(ctx)
        self.draw_overlays(ctx)          # renders the dialog while it's open

    def _draw_settings(self, ctx):
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = 20
        ctx.rgb(*_TEXT).move_to(0, -100).text("HexType")

        # Rows are centred.  Colour rows draw two segments (label + tinted value),
        # so we measure the whole line and start it at -width/2 with LEFT align.
        ctx.font_size = 16
        n = len(_ITEMS)
        for i, (kind, key, label) in enumerate(_ITEMS):
            y = (i - (n - 1) / 2) * 22 - 2
            row = _SEL if i == self._sel else _TEXT
            if kind == "colour":
                head = "%s: " % label
                val = self.opts[key]
                ctx.text_align = ctx.LEFT
                x = -(ctx.text_width(head) + ctx.text_width(val)) / 2
                ctx.rgb(*row).move_to(x, y).text(head)
                ctx.rgb(*colour_screen(val)).move_to(x + ctx.text_width(head), y).text(val)
            else:
                if kind == "toggle":
                    line = "%s: %s" % (label, "On" if self.opts[key] else "Off")
                elif kind == "layout":
                    line = "%s: %s" % (label, self.opts[key])
                else:
                    line = "> %s" % label
                ctx.text_align = ctx.CENTER
                ctx.rgb(*row).move_to(0, y).text(line)

        ctx.text_align = ctx.CENTER
        ctx.font_size = 12
        ctx.rgb(*_HINT).move_to(0, 88).text("C select   F exit")
        if self.last_text:
            t = self.last_text
            if len(t) > 22:
                t = t[:21] + "…"
            ctx.rgb(*_HINT).move_to(0, 104).text('"%s"' % t)


__app_export__ = HexTypeApp
