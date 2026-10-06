"""Key names, combo parsing and US-layout text mapping (evdev keycodes)."""
from evdev import ecodes as e

MODIFIERS = {
    e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
    e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA,
}
_MOD_ORDER = [e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
              e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA]

ALIASES = {
    "ctrl": "LEFTCTRL", "control": "LEFTCTRL", "lctrl": "LEFTCTRL", "rctrl": "RIGHTCTRL",
    "shift": "LEFTSHIFT", "lshift": "LEFTSHIFT", "rshift": "RIGHTSHIFT",
    "alt": "LEFTALT", "lalt": "LEFTALT", "ralt": "RIGHTALT", "altgr": "RIGHTALT",
    "super": "LEFTMETA", "meta": "LEFTMETA", "win": "LEFTMETA", "cmd": "LEFTMETA",
    "return": "ENTER", "escape": "ESC", "del": "DELETE", "ins": "INSERT",
    "pgup": "PAGEUP", "pgdn": "PAGEDOWN", "bksp": "BACKSPACE",
    "print": "SYSRQ", "prtsc": "SYSRQ", "printscreen": "SYSRQ",
    "play": "PLAYPAUSE", "pause": "PLAYPAUSE", "next": "NEXTSONG",
    "prev": "PREVIOUSSONG", "previous": "PREVIOUSSONG",
    "volup": "VOLUMEUP", "voldown": "VOLUMEDOWN",
    "-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE",
    ";": "SEMICOLON", "'": "APOSTROPHE", "`": "GRAVE", "\\": "BACKSLASH",
    ",": "COMMA", ".": "DOT", "/": "SLASH",
}

_PRETTY = {
    "LEFTCTRL": "Ctrl", "RIGHTCTRL": "RCtrl", "LEFTSHIFT": "Shift", "RIGHTSHIFT": "RShift",
    "LEFTALT": "Alt", "RIGHTALT": "AltGr", "LEFTMETA": "Super", "RIGHTMETA": "RSuper",
    "SYSRQ": "Print", "PLAYPAUSE": "Play/Pause", "NEXTSONG": "Next", "PREVIOUSSONG": "Prev",
    "VOLUMEUP": "Vol+", "VOLUMEDOWN": "Vol−", "MUTE": "Mute",
}

# Keys the virtual output device may emit: the keyboard range, skipping mouse/joystick buttons.
EMIT_KEYS = [c for c in range(1, 0x2c0) if c < 0x100 or c >= 0x160]


def name_of(code):
    n = e.KEY.get(code)
    if isinstance(n, (list, tuple)):
        n = next(x for x in n if x != "KEY_MIN_INTERESTING")
    return n[4:] if n and n.startswith("KEY_") else str(code)


def code_of(name):
    n = name.strip()
    n = ALIASES.get(n.lower(), n.upper())
    code = e.ecodes.get("KEY_" + n)
    if code is None:
        raise ValueError(f"unknown key '{name}'")
    return code


def signature(mods, key):
    """Canonical trigger name, e.g. 'LEFTCTRL+A'."""
    parts = [name_of(m) for m in _MOD_ORDER if m in mods and m != key]
    return "+".join(parts + [name_of(key)])


def parse_combo(token):
    return [code_of(p) for p in token.split("+") if p]


def parse_sequence(text):
    """'ctrl+c alt+tab sleep:200 ctrl+v' -> [('combo', [codes]), ('sleep', ms), ...]"""
    steps = []
    for tok in text.split():
        low = tok.lower()
        if low.startswith(("sleep:", "wait:")):
            steps.append(("sleep", int(low.split(":", 1)[1])))
        else:
            steps.append(("combo", parse_combo(tok)))
    return steps


def _pretty_name(n):
    if n.startswith("KP") and n[2:].isdigit():
        return f"Num {n[2:]}"
    return _PRETTY.get(n, n.title() if len(n) > 1 else n)


def pretty_combo(text):
    out = []
    for tok in text.split():
        if tok.lower().startswith(("sleep:", "wait:")):
            out.append(f"⏱{tok.split(':', 1)[1]}ms")
            continue
        try:
            names = [name_of(c) for c in parse_combo(tok)]
        except ValueError:
            out.append(tok)
            continue
        out.append("+".join(_pretty_name(n) for n in names))
    return " ".join(out)


# US layout: char -> (keycode, needs_shift)
US_CHARS = {" ": (e.KEY_SPACE, False), "\n": (e.KEY_ENTER, False), "\t": (e.KEY_TAB, False)}
for _c in "abcdefghijklmnopqrstuvwxyz":
    US_CHARS[_c] = (code_of(_c), False)
    US_CHARS[_c.upper()] = (code_of(_c), True)
for _plain, _shifted, _key in zip("`1234567890-=[]\\;',./", '~!@#$%^&*()_+{}|:"<>?',
                                  ["GRAVE", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
                                   "MINUS", "EQUAL", "LEFTBRACE", "RIGHTBRACE", "BACKSLASH",
                                   "SEMICOLON", "APOSTROPHE", "COMMA", "DOT", "SLASH"]):
    US_CHARS[_plain] = (code_of(_key), False)
    US_CHARS[_shifted] = (code_of(_key), True)
