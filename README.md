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
| Open terminal | new Ptyxis window in a folder, optionally running a command (e.g. `~/Coding/macropad` + `claude`) |
| Chain of steps | several of the above in order, with **Wait** steps in between; any action's **Add another step** turns it into a chain |
| Switch layer | next / previous / a specific layer |
| Original key | send what the pad sends natively (wheel = volume) |

**Layers** give you 6 keys × N pages. Bindings on non-base layers fall back to the base layer
unless overridden ("Same as base layer").

## Identify keys
Header button **Identify keys** walks you through pressing each physical key / wheel direction so the
on-screen layout matches the pad. Each on-screen key keeps its action.

## Storing on the pad itself
For a key whose action is a **keyboard shortcut** (single combo, e.g. `ctrl+shift+t`) or media key, the
editor's “Pad hardware” row can write it into the pad's own memory, so it works on any computer.
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
- `macropad/daemon.py` — grabber/remapper, installed as `/usr/libexec/macropad-daemon` and run by the
  per-user service `macropad.service` (`systemctl --user status macropad`, `journalctl --user -u macropad -f`)
- `macropad/gui.py` — settings app (`macropad-settings`); talks to the daemon over `$XDG_RUNTIME_DIR/macropad.sock`
- `build-aux/` — RPM spec, user service, udev rule (gives the logged-in user access to the pad), desktop file, icon

## Install
Every push to `main` builds an RPM in GitHub Actions and publishes it as a DNF repository on GitHub Pages:

```sh
sudo dnf config-manager addrepo --from-repofile=https://djshiye.github.io/macropad-fedora/macropad.repo
sudo dnf install macropad
```

`dnf upgrade` keeps it current, and upgrades restart the running service. The service runs per user (it
needs your session to press keys, open apps and show notifications) and is enabled for every user.

To try changes from a checkout without packaging: `systemctl --user stop macropad`, then run
`./macropad-daemon` and `./macropad-settings` from the repository.

## Notes
- Terminal commands run in an interactive shell (`$SHELL -ic`) so `~/.bashrc` PATH entries apply.
  The terminal binary is the `terminal` key in config.json (default `ptyxis`, which takes `--new-window -d DIR -- CMD`).
- In a chain, typing steps go to whatever window has focus; add a Wait after opening a window before typing into it.
- Output is serialized: a long `sleep:` in one sequence delays other keys until it finishes.
