"""HexType keyboard layouts.

A *layout* defines the character groups HexType shows.  Each layout has up to
three sets that the joystick shifts between:

    standard  (unshifted)
    upper     (joystick up)
    symbol    (joystick down)

Each set is a list of up to **6 rows**; each row is a string of up to **6
characters**.  The left pads (12,11,10,9,8,7) pick the row, the right pads
(1..6) pick the character within it.  Rows may be shorter than 6 (the extra
pads just do nothing), and a set may have fewer than 6 rows.

`upper` and `symbol` are optional -- if a layout leaves one out it falls back to
the standard set, so a simple layout can define `standard` alone.

To add a layout, append a dict to LAYOUTS with a unique `name`.  The order here
is the order they cycle in the settings menu; the first is the default.
"""

LAYOUTS = [
    {
        "name": "alphabetical",
        "standard": ["abcdef", "ghijkl", "mnopqr", "stuvwx", "yz1234", "567890"],
        "upper":    ["ABCDEF", "GHIJKL", "MNOPQR", "STUVWX", "YZ!@£€", "%^&*()"],
        "symbol":   ["§±-_=+", "[]{}<>", "‘“`\\/|", ".,?~;:"],
    },
    {
        # Letters grouped by frequency-ish for shorter reaches; same shifted sets.
        "name": "en-freq",
        "standard": ["qz1234", "ybvkxj", "etaoin", "srhldc", "umfpgw", "567890"],
        "upper":    ["QZ!@£€", "YBVKXJ", "ETAOIN", "SRHLDC", "UMFPGW",  "%^&*()"],
        "symbol":   ["", "§±-_=+", "[]{}<>", "‘“`\\/|", ".,?~;:"],
    },
    {
        # Numbers/symbols first -- handy for calc entry.
        "name": "num-calc",
        "standard": ["", "1234", "5678", "90.+-", "=*/"],
    },
]

DEFAULT_LAYOUT = LAYOUTS[0]["name"]


def layout_names():
    return [layout["name"] for layout in LAYOUTS]


def get_layout(name):
    """Return a layout's three sets as [standard, upper, symbol].
    Unknown name -> the default layout.  Missing upper/symbol -> standard."""
    chosen = None
    for layout in LAYOUTS:
        if layout.get("name") == name:
            chosen = layout
            break
    if chosen is None:
        chosen = LAYOUTS[0]
    standard = chosen.get("standard") or []
    upper = chosen.get("upper") or standard
    symbol = chosen.get("symbol") or standard
    return [standard, upper, symbol]
