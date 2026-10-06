# Macropad

Rebind the 6 keys + scroll wheel of the SDINNOVATION SIDE-KEYBOARD (USB `0816:246f`) on GNOME/Wayland.

A small daemon grabs the pad exclusively (its original keys no longer reach apps) and runs
the action you assigned instead. The **Macropad** app (in the app grid) edits the bindings.

## Actions
| Type | Value example |
|---|---|
| Keyboard shortcut | `ctrl+shift+t`, `super+e`, `playpause`, `print`; sequences: `ctrl+a ctrl+c sleep:100 ctrl+v` |
| Type text | any text (non-ASCII goes via clipboard + Ctrl+V) |
| Run command | `nautilus ~/Downloads`, `playerctl next` (run with `sh -c`) |
| Launch app | picked from installed apps |
| Open URL / file | `https://…`, `~/Documents/notes.md` |
| Switch layer | next / previous / a specific layer |
| Original key | send what the pad sends natively (wheel = volume) |

**Layers** give you 6 keys × N pages. Bindings on non-base layers fall back to the base layer
unless overridden ("Same as base layer").

## Identify keys
Header button **Identify keys** walks you through pressing each physical key / wheel direction so the
on-screen layout matches the pad. Each on-screen key keeps its action.

## Storing on the pad itself
For a key whose action is a **keyboard shortcut** (single combo, e.g. `ctrl+shift+t`) or media key, the
editor's “Stored on the pad” section can write it into the pad's own memory, so it works on any computer.
Writes go through `macropad/board.py`, which only allows read commands plus the single-key write
(command 16) and verifies every write by reading it back. Firmware-update (0x55/0x5A) and
factory-reset (15/255) commands are refused. “Factory default” restores one key from the pad's own
factory table. Protocol: https://github.com/parsaj-dev/sdcx-keypad (docs/PROTOCOL.md).

## Security
- `audit/board_dump.py` dumps everything the pad stores (read-only); last result in `audit/board_dump.txt`
  (plain keymaps, empty macro area, no URL feature). The firmware itself can't be read over USB.
- The daemon grabs the pad exclusively; input that doesn't match a known key is blocked and raises a
  desktop warning.

## Files
- `~/.config/macropad/config.json` — config (edited by the app; hand edits are picked up within 2 s)
- `macropad/daemon.py` — grabber/remapper (`systemctl --user status macropad`, `journalctl --user -u macropad -f`)
- `macropad/gui.py` — settings app; talks to the daemon over `$XDG_RUNTIME_DIR/macropad.sock`
- `70-macropad.rules` — udev rule giving the logged-in user access to the pad

## Install
`./install.sh` (asks for admin password once for the udev rule; enables the user service and app launcher).

## Notes
- Output is serialized: a long `sleep:` in one sequence delays other keys until it finishes.
