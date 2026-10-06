#!/usr/bin/env python3
"""READ-ONLY dump of everything the SDINNOVATION pad stores (group 0x06 read commands only)."""
import os, select, sys, time, json

PATH = sys.argv[1] if len(sys.argv) > 1 else "/dev/hidraw9"
READ_CMDS = {5, 7, 8, 10, 12, 19, 65}   # only these are ever sent

fd = os.open(PATH, os.O_RDWR | os.O_NONBLOCK)

def req(*payload):
    assert payload[0] in READ_CMDS, "refusing to send a non-read command"
    while select.select([fd], [], [], 0)[0]:
        os.read(fd, 64)
    os.write(fd, bytes([0x06, *payload]).ljust(64, b"\0"))
    end = time.time() + 1
    while time.time() < end:
        if select.select([fd], [], [], 0.2)[0]:
            d = os.read(fd, 64)
            if d[:2] == b"\xaa\xfa":
                continue
            return d
    raise TimeoutError(payload)

def t(x): return [x & 0xFF, x >> 8]
def hx(b): return " ".join(f"{x:02x}" for x in b)

info = req(5)
print("INFO raw:", hx(info))
p = info[5:43]
nfo = dict(version=p[0] | p[1] << 8, pid=hex(p[2] | p[3] << 8), firmware=hex(p[4] | p[5] << 8),
           work_mode=p[6], profiles=p[10], profile=p[11], layers=p[12], layer=p[13],
           serial=bytes(b for b in info[21:43] if b).decode(errors="replace"))
print("INFO:", nfo)
print("LIGHT:", hx(req(10)[5:16]))

KEYS = {0: "key1", 1: "key2", 2: "key3", 3: "key4", 4: "key5", 5: "key6", 16: "knob press", 17: "knob CW", 18: "knob CCW"}
def table(cmd, extra, span):
    data = bytearray(); off = 0
    while len(data) < span:
        r = req(cmd, *extra(off)); data += r[8:64]; off += 56
    return data

full = table(7, lambda o: [56, *t(o)], 576)
print("\nFACTORY table (non-zero entries of all 144 slots):")
for i in range(144):
    e = full[4*i:4*i+4]
    if any(e): print(f"  idx {i:3} {KEYS.get(i, '?'):10} {hx(e)}")
for layer in range(max(1, nfo["layers"])):
    km = table(8, lambda o: [58, *t(o), 0, layer], 76)
    print(f"\nLAYER {layer} keymap:")
    for i, n in KEYS.items():
        print(f"  idx {i:3} {n:10} {hx(km[4*i:4*i+4])}")

macro = bytearray(); off = 0
while off < 4096:
    n = min(56, 4096 - off); macro += req(12, n, *t(off))[8:8+n]; off += 56
print("\nMACRO area: index:", hx(macro[:64]))
used = [i for i in range(64, 4096) if macro[i] not in (0, 0xff)]
print("  non-empty bytes after index:", len(used), "first at", used[:1])
url = bytearray(); off = 0
try:
    while off < 128:
        url += req(65, 56, *t(off))[8:64]; off += 56
except TimeoutError:
    print("\nURL/OEM area: no response (this firmware does not implement the URL feature)")
url = url[:128]
if url: print("\nURL/OEM area raw:", hx(url))
if url: print("  as text:", repr(bytes(b for b in url if 32 <= b < 127).decode()))
open(os.path.join(os.path.dirname(__file__), "board_dump.json"), "w").write(json.dumps(
    {"info": nfo, "factory": full.hex(), "macro": macro.hex(), "url": url.hex()}, indent=1))
