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
"""

import asyncio
import math
import time

from events.input import Buttons, BUTTON_TYPES
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


# ----------------------------------------------------------------------------
# Character sets
# ----------------------------------------------------------------------------

STANDARD = [
    "abcdef",
    "ghijkl",
    "mnopqr",
    "stuvwx",
    "yz1234",
    "567890",
]
UPPER = [
    "ABCDEF",
    "GHIJKL",
    "MNOPQR",
    "STUVWX",
    "YZ!@£€",
    "%^&*()",
]
SYMBOL = [
    "§±-_=+",
    "[]{}<>",
    "‘“`\\/|",
    ".,?~;:",
]

# Index order matters: joystick up/down move away from STD.
STD, UP, SYM = 0, 1, 2
SETS = [STANDARD, UPPER, SYMBOL]


def _group_of(pad):
    return 12 - pad          # left pad 12->group 0 (row 1) .. 7->group 5 (row 6)

def _left_pad_of(group):
    return 12 - group        # inverse, for lighting the selected group's LED

def _col_of(pad):
    return pad - 1           # right pad 1->col 0 .. 6->col 5

MAX_LEN = 200                # cap on the stored text

# Held-backspace auto-repeat: wait this long after the first delete, then delete
# again every interval while still held.
_BS_REPEAT_DELAY_MS = 400
_BS_REPEAT_INTERVAL_MS = 80


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


# Colours (0..1 floats).
_TEXT = (0.95, 0.96, 1.0)
_HINT = (0.5, 0.55, 0.68)
_LABEL = (0.66, 0.72, 0.86)   # the group chart on the left
_CHOICE = (0.55, 0.92, 0.66)  # right-pad character choices
_IND = (0.72, 0.80, 1.0)      # shift arrow

# Ring-LED colours (0..255).
_LED_GROUP = (200, 120, 0)    # the selected group pad (amber)
_LED_CHAR = (0, 150, 70)      # the six live character pads (green)
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


_PENDING = object()   # sentinel: dialog still running


class HexTypeDialog:
    def __init__(self, app, on_complete=None, on_cancel=None, initial=""):
        self.app = app
        self.on_complete = on_complete
        self.on_cancel = on_cancel
        self.text = initial

        self.buttons = Buttons(app)
        self._group = None       # selected group (row index) or None
        self._set = STD          # active set; one-shot -> reverts after a char
        self._tap_locked = False  # one tap per finger-contact; needs a release
        self._reading = False    # review screen showing the whole string
        self._read_lines = None  # cached round-screen wrap
        self._bs_held = False    # backspace auto-repeat state
        self._bs_timer = 0

        self._led_shown = None   # pad -> colour actually on the hardware
        self._result = _PENDING

    # ---- lifecycle --------------------------------------------------------

    async def run(self, render_update):
        """Open the dialog and block until the user confirms or cancels.
        Returns the entered string, or False if cancelled."""
        # Reset transient state so a reused dialog starts clean (keeps .text).
        self._result = _PENDING
        self._reading = False
        self._read_lines = None
        self._group = None
        self._set = STD
        self._tap_locked = False
        self._bs_held = False
        self.buttons.clear()

        eventbus.emit(PatternDisable())
        self._leds_off()
        self.app.overlays.append(self)

        last = time.ticks_ms()
        while self._result is _PENDING:
            now = time.ticks_ms()
            delta = time.ticks_diff(now, last)
            last = now
            self._update(delta)
            self._sync_leds()
            await render_update()
            await asyncio.sleep(0.02)

        try:
            self.app.overlays.remove(self)
        except ValueError:
            pass
        self._leds_off()
        eventbus.emit(PatternEnable())
        self.buttons.clear()
        await render_update()
        return self._result

    def _complete(self):
        self._result = self.text
        if self.on_complete is not None:
            self.on_complete(self.text)

    def _cancel(self):
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
        """Light the selected group pad and the six character pads."""
        state = {}
        if self._group is not None:
            state[_left_pad_of(self._group)] = _LED_GROUP
            if self._group < len(SETS[self._set]):
                for pad in range(1, 7):
                    state[pad] = _LED_CHAR
        if _tildagonos is None or state == self._led_shown:
            return
        for i in range(1, 13):
            _tildagonos.leds[i] = state.get(i, _LED_OFF)
        _tildagonos.leds.write()
        self._led_shown = dict(state)

    # ---- input ------------------------------------------------------------

    def _pressed(self):
        if _FB is None:
            return set()
        states = _FB.touch_states
        return {n for n in range(1, 13) if states["TOUCH%02d" % n][0]}

    def _update(self, delta):
        b = self.buttons

        # F: leave review -> back to editing; else clear the text; else cancel.
        if b.pressed(BUTTON_TYPES["CANCEL"]):
            if self._reading:
                self._reading = False
            elif self.text:
                self.text = ""
                self._group = None
                self._set = STD
                self._read_lines = None
            else:
                self._cancel()
            return

        # C: return the text.  If the whole thing already fits the edit screen,
        # return straight away; otherwise show the review first, then a second C
        # returns it.
        if b.pressed(BUTTON_TYPES["CONFIRM"]):
            if self._reading:
                self._complete()
            elif len(_wrap(self.text)) <= _MAX_LINES:
                self._complete()
            else:
                self._reading = True
                self._read_lines = None
            return

        if self._reading:
            self._bs_held = False
            return

        # Backspace (LEFT) auto-repeats while held.
        self._update_backspace(delta)

        # Joystick edits that don't need a group.
        if b.pressed(BUTTON_TYPES["RIGHT"]):       # space
            self._append(" ")
        if b.pressed(BUTTON_TYPES["UP"]):          # one-shot upper (toggle)
            self._set = STD if self._set == UP else UP
        if b.pressed(BUTTON_TYPES["DOWN"]):        # one-shot symbols (toggle)
            self._set = STD if self._set == SYM else SYM
        # A shift to a smaller set can strand the selected group; drop it.
        if self._group is not None and self._group >= len(SETS[self._set]):
            self._group = None

        self._update_taps()

    def _update_taps(self):
        pressed = self._pressed()
        if not pressed:
            self._tap_locked = False
            return
        if self._tap_locked:
            return
        # First contact of a fresh tap: accept exactly one pad, then wait for a
        # full release before the next.  This kills adjacent-pad bleed doubles.
        self._tap_locked = True
        self._handle_tap(self._pick_tap(pressed))

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
            elif group < len(SETS[self._set]):
                self._group = group
            # taps on an empty group slot (symbol set) are ignored
        elif 1 <= pad <= 6:
            if self._group is None:
                return
            rows = SETS[self._set]
            if self._group >= len(rows):
                return
            row = rows[self._group]
            col = _col_of(pad)
            if col < len(row):
                self._append(row[col])
                # reset-each-char + one-shot shift both revert here
                self._group = None
                self._set = STD

    def _update_backspace(self, delta):
        """Delete once on press, then repeat while LEFT is held down."""
        if self.buttons.get(BUTTON_TYPES["LEFT"]):
            if not self._bs_held:
                self._bs_held = True
                self._bs_timer = _BS_REPEAT_DELAY_MS
                self.text = self.text[:-1]
            else:
                self._bs_timer -= delta
                if self._bs_timer <= 0:
                    self._bs_timer = _BS_REPEAT_INTERVAL_MS
                    self.text = self.text[:-1]
        else:
            self._bs_held = False

    def _append(self, ch):
        if len(self.text) < MAX_LEN:
            self.text += ch

    # ---- drawing (as a full-screen overlay) -------------------------------

    def draw(self, ctx):
        clear_background(ctx)   # opaque: cover the host app underneath
        if self._reading:
            self._draw_reading(ctx)
        else:
            if self._group is not None and self._group < len(SETS[self._set]):
                self._draw_choices(ctx)
            else:
                self._draw_group_chart(ctx)
            self._draw_entry(ctx)

    def _draw_reading(self, ctx):
        if self._read_lines is None:
            self._read_lines = _reading_lines(ctx, self.text)
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
        """With no group selected, show every group on its own left pad, as two
        rows of three so it sits neatly over the physical pad."""
        rows = SETS[self._set]
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = 15
        ctx.rgb(*_LABEL)
        for pad in range(7, 13):
            group = _group_of(pad)
            if group >= len(rows):
                continue
            row = rows[group]
            x, y = _pad_pos(pad, R_LABEL)
            ctx.move_to(x, y - 9).text(row[0:3])
            ctx.move_to(x, y + 9).text(row[3:6])

    def _draw_choices(self, ctx):
        """With a group selected, show its six characters out on the right pads."""
        row = SETS[self._set][self._group]
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = 24
        for pad in range(1, 7):
            col = _col_of(pad)
            if col < len(row):
                x, y = _pad_pos(pad, R_LABEL)
                ctx.rgb(*_CHOICE).move_to(x, y).text(row[col])

    def _draw_entry(self, ctx):
        """The typed text in the middle, with the shift arrow above it."""
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        if self._set != STD:
            ctx.font_size = 30
            ctx.rgb(*_IND).move_to(0, -52).text("↑" if self._set == UP else "↓")

        if not self.text:
            ctx.font_size = 18
            ctx.rgb(*_HINT).move_to(0, 10).text("type…")
            return
        lines = _wrap(self.text + "|")[-_MAX_LINES:]
        ctx.font_size = 20
        ctx.rgb(*_TEXT)
        y0 = 10 - (len(lines) - 1) * _LINE_H / 2
        for i, line in enumerate(lines):
            ctx.move_to(0, y0 + i * _LINE_H).text(line)
