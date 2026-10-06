"""On-board keymap access for the SDINNOVATION pad over its vendor HID config channel.

Protocol: 64-byte reports on the hidraw node whose report descriptor starts with
06 00 ff 09 02 (vendor page 0xFF00). Frames are [0x06, cmd, ...]. Only the config
commands listed in ALLOWED are ever sent; firmware-update groups (0x55/0x5A) and
factory reset ([15, 255]) are refused. Reverse-engineered by
https://github.com/parsaj-dev/sdcx-keypad (docs/PROTOCOL.md).
"""
import os
import select
import time
from pathlib import Path

from evdev import ecodes as e

from . import keys

GROUP = 0x06
CMD_INFO, CMD_FACTORY, CMD_GET_KEYMAP, CMD_SET_KEY = 5, 7, 8, 16
ALLOWED = {CMD_INFO, CMD_FACTORY, CMD_GET_KEYMAP, CMD_SET_KEY}
TYPE_KEYBOARD, TYPE_CONSUMER, TYPE_DISABLED = 0x20, 0x30, 0x13

# Board key index of each physical control (HCY-K006 matrix; knob lives at 16-18).
BOARD_INDICES = [0, 1, 2, 3, 4, 5, 16, 17, 18]
INDEX_NAMES = {0: "Key A", 1: "Key B", 2: "Key C", 3: "Key D", 4: "Key E", 5: "Key F",
               16: "Wheel press", 17: "Wheel ⟳", 18: "Wheel ⟲"}

# HID keyboard usage -> evdev keycode, from hid_keyboard[] in linux drivers/hid/hid-input.c.
_HID_TO_EVDEV = bytes.fromhex(
    "000000001e302e2012212223172425263231181910131f14162f112d152c0203"
    "0405060708090a0b1c010e0f390c0d1a1b2b2b2728293334353a3b3c3d3e3f40"
    "4142434457586346776e66686f6b6d6a696c674562374a4e604f50514b4c4d47"
    "48495253567f7475b7b8b9babbbcbdbebfc0c1c2868a82848081838985878871"
    "73720000007900595d7c5c5e5f0000007a7b5a5b55000000000000006f000000"
    "00000000000000000000000000000000000000000000b3b40000000000000000"
    "0000000000000000000000000000000000000000000000006f00000000000000"
    "1d2a387d6136647ea4a6a5a3a1737271969e9f8088b1b2b08e98ad8c00000000"
)
_EVDEV_TO_HID = {}
for _usage, _code in enumerate(_HID_TO_EVDEV):
    if _code and _usage < 0xE0:
        _EVDEV_TO_HID.setdefault(_code, _usage)

# Consumer-page usages the board can send (16-bit) <-> evdev.
_CONSUMER = {
    0xE2: e.KEY_MUTE, 0xE9: e.KEY_VOLUMEUP, 0xEA: e.KEY_VOLUMEDOWN, 0xCD: e.KEY_PLAYPAUSE,
    0xB5: e.KEY_NEXTSONG, 0xB6: e.KEY_PREVIOUSSONG, 0xB7: e.KEY_STOPCD,
    0x6F: e.KEY_BRIGHTNESSUP, 0x70: e.KEY_BRIGHTNESSDOWN, 0x192: e.KEY_CALC,
    0x18A: e.KEY_MAIL, 0x221: e.KEY_SEARCH, 0x223: e.KEY_HOMEPAGE, 0x194: e.KEY_FILE,
}
_EVDEV_TO_CONSUMER = {v: k for k, v in _CONSUMER.items()}

_MOD_BITS = [e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_LEFTALT, e.KEY_LEFTMETA,
             e.KEY_RIGHTCTRL, e.KEY_RIGHTSHIFT, e.KEY_RIGHTALT, e.KEY_RIGHTMETA]


class BoardError(Exception):
    pass


def encode(combo):
    """'ctrl+shift+t' / 'playpause' -> the 4 bytes the board stores. Single combos only."""
    toks = combo.split()
    if len(toks) != 1:
        raise BoardError("the board stores one key combination per key (no sequences or delays)")
    codes = keys.parse_combo(toks[0])
    mods = [c for c in codes if c in keys.MODIFIERS]
    rest = [c for c in codes if c not in keys.MODIFIERS]
    mask = sum(1 << _MOD_BITS.index(m) for m in mods)
    if len(rest) > 1:
        raise BoardError("only one non-modifier key per combination")
    if not rest:
        if len(mods) != 1:
            raise BoardError("empty combination")
        rest, mask = mods, 0
    key = rest[0]
    if key in _EVDEV_TO_CONSUMER and not mask:
        usage = _EVDEV_TO_CONSUMER[key]
        return bytes([TYPE_CONSUMER, usage & 0xFF, usage >> 8, 0])
    if key in _MOD_BITS:
        return bytes([TYPE_KEYBOARD, mask, 0xE0 + _MOD_BITS.index(key), 0])
    if key not in _EVDEV_TO_HID:
        raise BoardError(f"the board can't send {keys.name_of(key)}")
    return bytes([TYPE_KEYBOARD, mask, _EVDEV_TO_HID[key], 0])


def decode(entry):
    """4 stored bytes -> (combo string, evdev signature) or (description, None)."""
    t, a, b, _ = entry
    if t == TYPE_KEYBOARD:
        mods = {m for i, m in enumerate(_MOD_BITS) if a & (1 << i)}
        code = _MOD_BITS[b - 0xE0] if 0xE0 <= b <= 0xE7 else _HID_TO_EVDEV[b]
        if not code:
            return f"HID usage 0x{b:02x}", None
        short = {e.KEY_LEFTCTRL: "ctrl", e.KEY_LEFTSHIFT: "shift", e.KEY_LEFTALT: "alt", e.KEY_LEFTMETA: "super"}
        parts = [short.get(m, keys.name_of(m).lower()) for m in _MOD_BITS if m in mods]
        return "+".join(parts + [keys.name_of(code).lower()]), keys.signature(mods, code)
    if t == TYPE_CONSUMER:
        code = _CONSUMER.get(a | b << 8)
        if code:
            return keys.name_of(code).lower(), keys.name_of(code)
        return f"media 0x{a | b << 8:03x}", None
    if t == TYPE_DISABLED:
        return "disabled", None
    return f"type 0x{t:02x} ({entry.hex(' ')})", None


def find_config_node():
    for node in sorted(Path("/sys/class/hidraw").iterdir()):
        try:
            uevent = (node / "device" / "uevent").read_text()
            desc = (node / "device" / "report_descriptor").read_bytes()
        except OSError:
            continue
        if ":00000816:0000246F" in uevent.upper() and desc.startswith(b"\x06\x00\xff\x09\x02"):
            return f"/dev/{node.name}"
    return None


class Board:
    def __init__(self):
        path = find_config_node()
        if not path:
            raise BoardError("pad not found")
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            raise BoardError(f"no permission for {path}; re-run install.sh") from None

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _send(self, *payload):
        if payload[0] not in ALLOWED:
            raise BoardError(f"command {payload[0]} is not allowed")
        while select.select([self.fd], [], [], 0)[0]:
            os.read(self.fd, 64)
        os.write(self.fd, bytes([GROUP, *payload]).ljust(64, b"\0"))

    def _request(self, *payload):
        self._send(*payload)
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            if select.select([self.fd], [], [], 0.2)[0]:
                data = os.read(self.fd, 64)
                if data[:2] != b"\xaa\xfa":  # skip unsolicited lighting notifications
                    return data
        raise BoardError("board did not answer")

    def _table(self, cmd, head, span, layer=None):
        data, off = bytearray(), 0
        while len(data) < span:
            extra = [0, layer] if layer is not None else []
            data += self._request(cmd, head, off & 0xFF, off >> 8, *extra)[8:64]
            off += 56
        return data

    def keymap(self, layer=0):
        data = self._table(CMD_GET_KEYMAP, 58, 76, layer)
        return {i: bytes(data[4 * i:4 * i + 4]) for i in BOARD_INDICES}

    def factory(self):
        data = self._table(CMD_FACTORY, 56, 76)
        return {i: bytes(data[4 * i:4 * i + 4]) for i in BOARD_INDICES}

    def write_key(self, index, entry, layer=0):
        """Store one key and verify by reading it back."""
        if index not in BOARD_INDICES or len(entry) != 4:
            raise BoardError("bad key index or entry")
        off = index * 4
        self._send(CMD_SET_KEY, 7, off & 0xFF, off >> 8, 0, layer, 0, *entry)
        time.sleep(0.05)
        got = self.keymap(layer)[index]
        if got != bytes(entry):
            raise BoardError(f"verify failed: wrote {bytes(entry).hex(' ')}, board has {got.hex(' ')}")
