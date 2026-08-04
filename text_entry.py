"""HexTypeDialog -- a drop-in text-entry dialog for Tildagon apps.

Two-tap text entry on the twelve front-board touch pads: the **left** arc (pads
12,11,10,9,8,7) picks a *group* of six characters, the **right** arc (pads 1-6)
picks one character from it.  The joystick shifts sets (one-shot), backspaces,
and spaces.  See the module ``app.py`` demo and the project README for the full
layout.

Usage mirrors the firmware's ``TextDialog``::

    from .text_entry import HexTypeDialog

    class MyApp(app.App):
        async def run(self, render_update):
            dialog = HexTypeDialog(self, initial="")
            text = await dialog.run(render_update)      # returns str, or False if cancelled
            if text is not False:
                ...

The host app just needs ``self.overlays`` (the ``app.App`` base provides it) and
to call ``self.draw_overlays(ctx)`` in its ``draw`` -- the dialog draws itself as
a full-screen overlay while it's open.

Confirm (C) returns the text to the caller.  If the whole string already fits the
edit screen it returns immediately; if it's longer it first shows a full-screen
review so you can read it all, and a second Confirm returns it.  Cancel (F) clears
the text, or -- when it's already empty -- cancels the dialog (returns False).

Matching the native TextDialog: ``on_complete`` / ``on_cancel`` are called with no
arguments and the handler reads the result off the dialog's ``.text``.
"""

import asyncio
import math
import time

from events.input import BUTTON_TYPES, ButtonDownEvent, ButtonUpEvent
from app_components.tokens import clear_background
from system.eventbus import eventbus
from system.patterndisplay.events import PatternEnable, PatternDisable

# Front-board touch state (class-level dict the firmware keeps updated) and the
# ring LEDs.  Both guarded so the module still imports off-hardware.
try:
    from frontboards.twentysix import TwentyTwentySix as _FB
except Exception:  # pragma: no cover
    _FB = None
try:
    from tildagonos import tildagonos as _tildagonos
except Exception:  # pragma: no cover
    _tildagonos = None
try:
    import settings as _settings
except Exception:  # pragma: no cover
    _settings = None

from .layouts import get_layout, layout_names, DEFAULT_LAYOUT


# Persisted-settings keys (stored in the badge's /settings.json).
SETTING_KEEP_GROUPS = "hextype_keep_groups"
SETTING_USE_LEDS = "hextype_use_leds"
SETTING_OVERRIDE = "hextype_override"
SETTING_LEFT_COLOUR = "hextype_left_colour"
SETTING_RIGHT_COLOUR = "hextype_right_colour"
SETTING_LAYOUT = "hextype_layout"

# Selectable colours: name -> (on-screen rgb 0..1, ring-LED rgb 0..255).  The
# first two match the original yellow/green defaults.
PALETTE = {
    "yellow":  ((1.00, 0.82, 0.10), (200, 120, 0)),
    "green":   ((0.55, 0.92, 0.66), (0, 150, 70)),
    "cyan":    ((0.40, 0.90, 0.95), (0, 150, 150)),
    "blue":    ((0.50, 0.68, 1.00), (0, 70, 200)),
    "magenta": ((1.00, 0.50, 0.90), (170, 0, 130)),
    "red":     ((1.00, 0.40, 0.40), (190, 0, 0)),
    "orange":  ((1.00, 0.60, 0.20), (210, 90, 0)),
    "white":   ((0.92, 0.94, 1.00), (150, 150, 150)),
}
PALETTE_NAMES = ["yellow", "green", "cyan", "blue", "magenta", "red", "orange", "white"]
DEFAULT_LEFT_COLOUR = "yellow"
DEFAULT_RIGHT_COLOUR = "green"


def colour_screen(name):
    return PALETTE.get(name, PALETTE[DEFAULT_LEFT_COLOUR])[0]


def colour_led(name):
    return PALETTE.get(name, PALETTE[DEFAULT_LEFT_COLOUR])[1]


def get_setting(key, default):
    if _settings is None:
        return default
    try:
        return bool(_settings.get(key, default))
    except Exception:
        return default


def set_setting(key, value):
    if _settings is None:
        return
    try:
        _settings.set(key, bool(value))
        _settings.save()
    except Exception:
        pass


def get_colour_name(key, default):
    """A persisted palette name, validated against PALETTE."""
    if _settings is None:
        return default
    try:
        name = _settings.get(key, default)
        return name if name in PALETTE else default
    except Exception:
        return default


def set_colour_name(key, name):
    if _settings is None:
        return
    try:
        _settings.set(key, name)
        _settings.save()
    except Exception:
        pass


def get_layout_name(default=DEFAULT_LAYOUT):
    """The persisted layout name, validated against the known layouts."""
    if _settings is None:
        return default
    try:
        name = _settings.get(SETTING_LAYOUT, default)
        return name if name in layout_names() else default
    except Exception:
        return default


def set_layout_name(name):
    if _settings is None:
        return
    try:
        _settings.set(SETTING_LAYOUT, name)
        _settings.save()
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Character sets: a *layout* provides three sets (standard/upper/symbol) as
# [standard, upper, symbol] via layouts.py.  Selected per-dialog; index order
# below matches how the joystick shifts between them.
# ----------------------------------------------------------------------------

STD, UP, SYM = 0, 1, 2


def _group_of(pad):
    return 12 - pad          # left pad 12->group 0 (row 1) .. 7->group 5 (row 6)

def _left_pad_of(group):
    return 12 - group        # inverse, for lighting the selected group's LED

# Rows shorter than 6 chars are centred on the right pads, keeping them as central
# as possible: 6->pads1-6, 5->2-6, 4->2-5, 3->3-5, 2->3-4, 1->pad3.
_CHAR_START_PAD = {6: 1, 5: 2, 4: 2, 3: 3, 2: 3, 1: 3}


def _char_start_pad(row_len):
    return _CHAR_START_PAD.get(row_len, 1)   # 0 or >6 -> 1 (range() handles the rest)


def _col_of(pad, row_len):
    """Right pad -> column index for a row of row_len chars (or None if the pad
    isn't one of the centred character pads for that length)."""
    col = pad - _char_start_pad(min(row_len, 6))
    return col if 0 <= col < row_len else None

MAX_LEN = 200                # cap on the stored text

# Ignore Confirm/Cancel for this long after the dialog opens, so a held key that
# opened it (e.g. selecting a menu item) can't immediately confirm/cancel.
_OPEN_GUARD_MS = 300


# ----------------------------------------------------------------------------
# Geometry: pads are 30-deg sectors, pad 1 centred at
# 15 deg clockwise from 12 o'clock.
# ----------------------------------------------------------------------------

PAD_STEP_DEG = 30.0
PAD1_TOP_DEG = PAD_STEP_DEG / 2
PAD_DIR = 1
R_LABEL = 101


def _pad_pos(pad, radius):
    ang = math.radians(PAD1_TOP_DEG + PAD_DIR * (pad - 1) * PAD_STEP_DEG)
    return radius * math.sin(ang), -radius * math.cos(ang)


# Fixed colours (0..1 floats).  The selected-group (left) and character-choice
# (right) colours are configurable per instance -- see PALETTE / left_colour /
# right_colour.
_TEXT = (0.95, 0.96, 1.0)
_HINT = (0.5, 0.55, 0.68)
_LABEL = (0.66, 0.72, 0.86)   # a group in the chart, nothing selected
_GROUP_DIM = (0.5, 0.5, 0.5)  # the other groups once one is selected
_IND = (0.72, 0.80, 1.0)      # shift arrow

_LED_OFF = (0, 0, 0)

# Edit-screen text layout.  Lines run up to _WRAP_MAX characters, but if there's
# a space in the last few characters (from _WRAP_MIN on) we break there instead,
# so words stay whole.  A word longer than _WRAP_MAX is hard-broken.
_WRAP_MIN = 9
_WRAP_MAX = 13
_MAX_LINES = 3    # edit view shows the last 3 lines (older scroll off the top)
_LINE_H = 24


def _wrap(text, lo=_WRAP_MIN, hi=_WRAP_MAX):
    lines = []
    i = 0
    n = len(text)
    while i < n:
        if n - i <= hi:
            lines.append(text[i:])
            break
        # Prefer the last space in the [lo, hi] window; break there and drop it.
        cut = -1
        for j in range(i + hi, i + lo - 1, -1):
            if text[j] == " ":
                cut = j
                break
        if cut == -1:
            lines.append(text[i:i + hi])   # no space to break on: hard wrap
            i += hi
        else:
            lines.append(text[i:cut])
            i = cut + 1
    return lines if lines else [""]


# Review mode: render the whole string using the full round screen.  Each line's
# width depends on where it sits vertically (a chord of the circle), so middle
# lines hold more than top/bottom ones.
_READ_MAX_LINES = 8
_READ_FONT = 18
_READ_LH = 24       # line pitch
_READ_R = 112       # usable radius (screen is 120)
_READ_HPAD = 8      # horizontal padding at each end of a line


def _line_width(y):
    """Available text width for a line centred at height y (0 if off the disc)."""
    inner = _READ_R * _READ_R - y * y
    if inner <= 0:
        return 0
    return 2 * math.sqrt(inner) - 2 * _READ_HPAD


def _slot_ys(n):
    """Vertically-centred y positions for n lines."""
    return [(i - (n - 1) / 2) * _READ_LH for i in range(n)]


def _wrap_to_slots(ctx, text, widths):
    """Greedily flow words into lines whose widths are given per slot (measured
    with ctx.text_width). Returns (lines, overflowed)."""
    n = len(widths)
    words = text.split(" ")
    lines = []
    line = ""
    i = 0
    k = 0
    while k < len(words):
        if i >= n:
            return lines, True
        word = words[k]
        cand = word if not line else line + " " + word
        if ctx.text_width(cand) <= widths[i]:
            line = cand
            k += 1
        elif not line:
            # A single word too wide even alone: hard-split to fit this slot.
            j = len(word)
            while j > 1 and ctx.text_width(word[:j]) > widths[i]:
                j -= 1
            lines.append(word[:j])
            words[k] = word[j:]
            i += 1
        else:
            lines.append(line)   # commit the line, move to the next slot
            line = ""
            i += 1
    if line:
        if i >= n:
            return lines, True
        lines.append(line)
    return lines, False


def _reading_lines(ctx, text):
    """Wrap text to the fewest round-screen lines that hold it (max 8)."""
    ctx.save()
    ctx.font_size = _READ_FONT
    result = None
    for n in range(1, _READ_MAX_LINES + 1):
        widths = [_line_width(y) for y in _slot_ys(n)]
        lines, overflow = _wrap_to_slots(ctx, text, widths)
        if not overflow:
            result = lines
            break
    if result is None:
        widths = [_line_width(y) for y in _slot_ys(_READ_MAX_LINES)]
        lines, _ = _wrap_to_slots(ctx, text, widths)
        if lines:
            lines[-1] = lines[-1] + "…"   # text didn't all fit
        result = lines[:_READ_MAX_LINES]
    ctx.restore()
    return result


class HexTypeDialog:
    def __init__(self, app, on_complete=None, on_cancel=None, initial="",
                 keep_groups=True, use_leds=True, message="", masked=False,
                 left_colour=DEFAULT_LEFT_COLOUR, right_colour=DEFAULT_RIGHT_COLOUR,
                 layout=DEFAULT_LAYOUT):
        self.app = app
        self.on_complete = on_complete
        self.on_cancel = on_cancel
        self.text = initial
        self.message = message           # prompt shown centred until you type
        self.masked = masked             # show the text as * (password entry)

        # The character layout: [standard, upper, symbol] sets.
        self._sets = get_layout(layout)

        # Switchable options (can be flipped between run()s).
        self.keep_groups = keep_groups   # keep all groups on screen when selecting
        self.use_leds = use_leds         # drive the ring LEDs (else leave the OS pattern)

        # Left (selected group) and right (character choices) colours, each used
        # for both the on-screen highlight and the ring LED.
        self._left_screen = colour_screen(left_colour)
        self._left_led = colour_led(left_colour)
        self._right_screen = colour_screen(right_colour)
        self._right_led = colour_led(right_colour)

        self._group = None       # selected group (row index) or None
        self._set = STD          # active set; one-shot -> reverts after a char
        self._reading = False    # review screen showing the whole string
        self._read_lines = None  # cached round-screen wrap
        self._touch = set()      # touch pads currently held down
        self._tap_locked = False  # one tap per finger-contact; needs a release

        self._led_shown = None   # pad -> colour actually on the hardware
        self._led_warmup = 30 if use_leds else 0  # frames to re-assert LEDs at open
        self._result = None      # None while running; str on complete, False on cancel
        self._closed = False
        self._opened_ms = time.ticks_ms()

        # Native TextDialog exposes ``open`` (True while the dialog is up); some
        # apps read it to decide when to drop the overlay. Provide it so we stay a
        # drop-in replacement -- we also flip it False on teardown, which native
        # never does but which can only help a caller that polls it.
        self.open = True

        # Event-driven input, like the firmware's TextDialog: this makes the
        # dialog work whether the caller uses the callback style (add to
        # overlays + on_complete/on_cancel) or awaits run().
        eventbus.on(ButtonDownEvent, self._handle_down, app)
        eventbus.on(ButtonUpEvent, self._handle_up, app)

        # Open: take over the LEDs now (callback-style callers never call run()).
        if self.use_leds:
            eventbus.emit(PatternDisable())
            self._sync_leds()

    # ---- lifecycle --------------------------------------------------------

    async def run(self, render_update):
        """Async convenience: add ourselves to the overlays and block until the
        user confirms or cancels.  Returns the string, or False if cancelled.
        (Callback-style callers skip this and read on_complete/on_cancel.)"""
        self.app.overlays.append(self)
        await render_update()
        while self._result is None:
            await render_update()
            await asyncio.sleep(0.02)
        try:
            self.app.overlays.remove(self)
        except ValueError:
            pass
        await render_update()
        return self._result

    def _finish(self):
        """Idempotent teardown: unsubscribe and hand the LEDs back."""
        if self._closed:
            return
        self._closed = True
        self.open = False
        eventbus.remove(ButtonDownEvent, self._handle_down, self.app)
        eventbus.remove(ButtonUpEvent, self._handle_up, self.app)
        if self.use_leds:
            self._leds_off()
            eventbus.emit(PatternEnable())

    # The firmware's TextDialog names its teardown ``_cleanup``, and some apps
    # (e.g. Bleepie) call it directly on the dialog after completion. Alias it so
    # we stay a drop-in replacement.
    _cleanup = _finish

    def _complete(self):
        self._finish()
        self._result = self.text
        # Match the native TextDialog contract: handlers take NO args and read
        # the text off the dialog (.text) themselves.
        if self.on_complete is not None:
            self.on_complete()

    def _cancel(self):
        self._finish()
        self._result = False
        if self.on_cancel is not None:
            self.on_cancel()

    # ---- LEDs -------------------------------------------------------------

    def _leds_off(self):
        if _tildagonos is None:
            return
        for i in range(1, 13):
            _tildagonos.leds[i] = _LED_OFF
        _tildagonos.leds.write()
        self._led_shown = {}

    def _sync_leds(self):
        """Light the selected group pad and its (centred) character pads."""
        state = {}
        if self._group is not None:
            state[_left_pad_of(self._group)] = self._left_led
            rows = self._sets[self._set]
            if self._group < len(rows):
                row = rows[self._group]
                start = _char_start_pad(min(len(row), 6))
                for col in range(min(len(row), 6)):
                    state[start + col] = self._right_led
        if _tildagonos is None or state == self._led_shown:
            return
        for i in range(1, 13):
            _tildagonos.leds[i] = state.get(i, _LED_OFF)
        _tildagonos.leds.write()
        self._led_shown = dict(state)

    # ---- input (event-driven) ---------------------------------------------

    def _handle_down(self, event):
        if self._result is not None:
            return                          # already finished
        b = event.button
        name = getattr(b, "name", "") or ""

        # A held key that opened the dialog keeps re-emitting; ignore Confirm/
        # Cancel until the open guard passes so it can't act on that key.
        guarded = time.ticks_diff(time.ticks_ms(), self._opened_ms) < _OPEN_GUARD_MS

        if name.startswith("TOUCH"):        # touch pads: TOUCH01..TOUCH12
            try:
                pad = int(name[5:])
            except ValueError:
                pad = 0
            if pad:
                self._touch_down(pad)
        elif BUTTON_TYPES["CANCEL"] in b:
            if not guarded:
                self._on_cancel()
        elif BUTTON_TYPES["CONFIRM"] in b:
            if not guarded:
                self._on_confirm()
        elif self._reading:
            pass                            # review screen ignores editing keys
        elif BUTTON_TYPES["LEFT"] in b:     # backspace (repeats via re-emit)
            self.text = self.text[:-1]
        elif BUTTON_TYPES["RIGHT"] in b:    # space
            self._append(" ")
        elif BUTTON_TYPES["UP"] in b:       # one-shot upper (toggle)
            self._set = STD if self._set == UP else UP
            self._drop_stranded_group()
        elif BUTTON_TYPES["DOWN"] in b:     # one-shot symbols (toggle)
            self._set = STD if self._set == SYM else SYM
            self._drop_stranded_group()

        # Re-light after a state change, but not once we've finished (a
        # complete/cancel already handed the LEDs back).
        if self.use_leds and self._result is None:
            self._sync_leds()

    def _handle_up(self, event):
        name = getattr(event.button, "name", "") or ""
        if name.startswith("TOUCH"):
            try:
                pad = int(name[5:])
            except ValueError:
                return
            self._touch.discard(pad)
            if not self._touch:
                self._tap_locked = False

    def _on_cancel(self):
        # F: leave review -> back to editing; else clear the text; else cancel.
        if self._reading:
            self._reading = False
        elif self.text:
            self.text = ""
            self._group = None
            self._set = STD
            self._read_lines = None
        else:
            self._cancel()

    def _on_confirm(self):
        # C: return the text.  Whole thing already visible (or masked) -> return
        # now; otherwise show the review first, then a second C returns it.
        if self._reading:
            self._complete()
        elif self.masked or len(_wrap(self.text)) <= _MAX_LINES:
            self._complete()
        else:
            self._reading = True
            self._read_lines = None

    def _drop_stranded_group(self):
        # A shift to a smaller set can strand the selected group; drop it.
        if self._group is not None and self._group >= len(self._sets[self._set]):
            self._group = None

    def _touch_down(self, pad):
        if self._reading:
            return
        self._touch.add(pad)
        if self._tap_locked:
            return
        # First contact of a fresh tap: accept exactly one pad, then wait for a
        # full release before the next.  This kills adjacent-pad bleed doubles.
        self._tap_locked = True
        self._handle_tap(self._pick_tap(self._touch))

    def _pick_tap(self, pressed):
        """Pick the one intended pad, biased to the side we expect next."""
        if self._group is None:
            prefer = [p for p in pressed if 7 <= p <= 12]   # expecting a group
        else:
            prefer = [p for p in pressed if 1 <= p <= 6]     # expecting a char
        pool = prefer if prefer else list(pressed)
        return min(pool)

    def _handle_tap(self, pad):
        if 7 <= pad <= 12:
            group = _group_of(pad)
            if group == self._group:
                self._group = None       # re-tapping the selected group deselects
            elif group < len(self._sets[self._set]):
                self._group = group
            # taps on an empty group slot (symbol set) are ignored
        elif 1 <= pad <= 6:
            if self._group is None:
                return
            rows = self._sets[self._set]
            if self._group >= len(rows):
                return
            row = rows[self._group]
            col = _col_of(pad, len(row))
            if col is not None:
                self._append(row[col])
                # reset-each-char + one-shot shift both revert here
                self._group = None
                self._set = STD

    def _append(self, ch):
        if len(self.text) < MAX_LEN:
            self.text += ch

    def _shown(self, text):
        """The text as displayed -- masked to * for password entry."""
        return ("*" * len(text)) if self.masked else text

    # ---- drawing (as a full-screen overlay) -------------------------------

    def draw(self, ctx):
        clear_background(ctx)   # opaque: cover the host app underneath

        # PatternDisable is handled asynchronously, so the OS pattern keeps
        # writing the LEDs for a few frames after we open -- long enough to leave
        # a stale frame behind.  Re-assert our LED state every frame for a short
        # warm-up so it wins once the pattern actually stops.
        if self.use_leds and self._led_warmup > 0:
            self._led_warmup -= 1
            self._led_shown = None      # force the write past the throttle
            self._sync_leds()

        if self._reading:
            self._draw_reading(ctx)
        else:
            selected = self._group is not None and self._group < len(self._sets[self._set])
            if self.keep_groups:
                # Keep every group on screen: the others dim and the selected one
                # turns yellow, with its characters also out on the right pads.
                self._draw_group_chart(ctx)
                if selected:
                    self._draw_choices(ctx)
            else:
                # Swap the group chart out for the selected group's characters.
                if selected:
                    self._draw_choices(ctx)
                else:
                    self._draw_group_chart(ctx)
            self._draw_entry(ctx)

    def _draw_reading(self, ctx):
        if self._read_lines is None:
            self._read_lines = _reading_lines(ctx, self._shown(self.text))
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        lines = self._read_lines
        if not lines or lines == [""]:
            ctx.font_size = 18
            ctx.rgb(*_HINT).move_to(0, 0).text("(nothing typed)")
            return
        ctx.font_size = _READ_FONT
        ctx.rgb(*_TEXT)
        for line, y in zip(lines, _slot_ys(len(lines))):
            ctx.move_to(0, y).text(line)

    def _draw_group_chart(self, ctx):
        """Show every group on its own left pad, as two rows of three so it sits
        neatly over the physical pad.  With nothing selected they're all the same
        colour; once a group is selected the others dim and it turns yellow."""
        rows = self._sets[self._set]
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = 15
        for pad in range(7, 13):
            group = _group_of(pad)
            if group >= len(rows):
                continue
            if self._group is None:
                col = _LABEL
            elif group == self._group:
                col = self._left_screen
            else:
                col = _GROUP_DIM
            row = rows[group]
            x, y = _pad_pos(pad, R_LABEL)
            ctx.rgb(*col)
            ctx.move_to(x, y - 9).text(row[0:3])
            ctx.move_to(x, y + 9).text(row[3:6])

    def _draw_choices(self, ctx):
        """With a group selected, show its characters on the (centred) right pads."""
        row = self._sets[self._set][self._group]
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = 24
        start = _char_start_pad(min(len(row), 6))
        for col in range(min(len(row), 6)):
            x, y = _pad_pos(start + col, R_LABEL)
            ctx.rgb(*self._right_screen).move_to(x, y).text(row[col])

    def _draw_entry(self, ctx):
        """The typed text in the middle, with the shift arrow above it."""
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        if self._set != STD:
            ctx.font_size = 30
            ctx.rgb(*_IND).move_to(0, -52).text("↑" if self._set == UP else "↓")

        if not self.text:
            # Empty: show the prompt (or a generic hint) as centred placeholder.
            ctx.font_size = 18
            ctx.rgb(*_HINT)
            plines = _wrap(self.message or "type…")[:_MAX_LINES]
            y0 = 10 - (len(plines) - 1) * _LINE_H / 2
            for i, line in enumerate(plines):
                ctx.move_to(0, y0 + i * _LINE_H).text(line)
            return
        lines = _wrap(self._shown(self.text) + "|")[-_MAX_LINES:]
        ctx.font_size = 20
        ctx.rgb(*_TEXT)
        y0 = 10 - (len(lines) - 1) * _LINE_H / 2
        for i, line in enumerate(lines):
            ctx.move_to(0, y0 + i * _LINE_H).text(line)


# ----------------------------------------------------------------------------
# Drop-in replacement for the firmware's app_components.TextDialog
# ----------------------------------------------------------------------------

class HexTextDialog(HexTypeDialog):
    """Same constructor/return contract as the firmware's ``TextDialog`` --
    ``TextDialog(message, app, masked=False, on_complete=None, on_cancel=None)``,
    ``await run(render_update)`` returns the string (or ``False`` if cancelled) --
    but backed by the HexType touch-pad method.  Honours the persisted HexType
    options so it behaves consistently wherever it's used."""

    def __init__(self, message, app, masked=False, on_complete=None, on_cancel=None):
        super().__init__(
            app,
            on_complete=on_complete,
            on_cancel=on_cancel,
            initial="",
            keep_groups=get_setting(SETTING_KEEP_GROUPS, True),
            use_leds=get_setting(SETTING_USE_LEDS, True),
            message=message,
            masked=masked,
            left_colour=get_colour_name(SETTING_LEFT_COLOUR, DEFAULT_LEFT_COLOUR),
            right_colour=get_colour_name(SETTING_RIGHT_COLOUR, DEFAULT_RIGHT_COLOUR),
            layout=get_layout_name(),
        )


# System-wide override of app_components.TextDialog.  Best effort: it patches the
# package attribute *and* rebinds the name in every already-imported module (so
# apps that did `from app_components import TextDialog` before us are covered
# too).  It does NOT survive a reboot -- no user-app code runs at boot -- so it's
# re-applied whenever this app loads with the setting on; and it doesn't provide
# the physical-keyboard path the native dialog has.
_orig_textdialog = None


def _rebind_textdialog(old, new):
    """Point every module-level `TextDialog` that is `old` at `new`."""
    import sys
    # Snapshot the values: a getattr below could import a submodule and mutate
    # sys.modules, which would break a live iteration.
    for mod in list(sys.modules.values()):
        try:
            if getattr(mod, "TextDialog", None) is old:
                mod.TextDialog = new
        except Exception:
            pass


def install_override():
    """Replace app_components.TextDialog with HexTextDialog everywhere."""
    global _orig_textdialog
    try:
        import app_components
        import app_components.dialog as _dialog
    except Exception:
        return False
    if _orig_textdialog is None:
        _orig_textdialog = _dialog.TextDialog
    _rebind_textdialog(_orig_textdialog, HexTextDialog)
    _dialog.TextDialog = HexTextDialog
    app_components.TextDialog = HexTextDialog
    return True


def remove_override():
    """Restore the firmware's TextDialog."""
    global _orig_textdialog
    if _orig_textdialog is None:
        return
    try:
        import app_components
        import app_components.dialog as _dialog
    except Exception:
        return
    _rebind_textdialog(HexTextDialog, _orig_textdialog)
    _dialog.TextDialog = _orig_textdialog
    app_components.TextDialog = _orig_textdialog


def is_overridden():
    try:
        import app_components.dialog as _dialog
        return _dialog.TextDialog is HexTextDialog
    except Exception:
        return False


def apply_override_setting():
    """Install or remove the override to match the persisted setting."""
    if get_setting(SETTING_OVERRIDE, False):
        install_override()
    else:
        remove_override()
