"""Shared config + paths for the macropad daemon and GUI."""
import copy
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "macropad"
CONFIG_PATH = CONFIG_DIR / "config.json"
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "macropad.sock"

# Action types: none, passthrough, inherit, shortcut, text, command, app, open, terminal, layer, sequence
ACTION_TYPES = {
    "none": "Do nothing",
    "passthrough": "Original key",
    "inherit": "Same as base layer",
    "shortcut": "Keyboard shortcut",
    "text": "Type text",
    "command": "Run command",
    "app": "Launch app",
    "open": "Open URL / file",
    "terminal": "Open terminal",
    "layer": "Switch layer",
    "sequence": "Chain of steps",
}

# What a step inside a "sequence" can be.
STEP_TYPES = {
    "terminal": "Open terminal",
    "app": "Launch app",
    "command": "Run command",
    "open": "Open URL / file",
    "wait": "Wait",
    "text": "Type text",
    "shortcut": "Press keys",
    "layer": "Switch layer",
}

DEFAULT_CONFIG = {
    "version": 1,
    "device_glob": "/dev/input/by-id/usb-SDINNOVATION_SIDE-KEYBOARD_*event*",
    "notify_layer_change": True,
    "terminal": "ptyxis",
    "controls": [
        {"id": "k1", "label": "Key 1", "signature": "KP1"},
        {"id": "k2", "label": "Key 2", "signature": "KP2"},
        {"id": "k3", "label": "Key 3", "signature": "KP3"},
        {"id": "k4", "label": "Key 4", "signature": "KP4"},
        {"id": "k5", "label": "Key 5", "signature": "KP5"},
        {"id": "k6", "label": "Key 6", "signature": "KP6"},
        {"id": "wheel_left", "label": "Wheel ⟲", "signature": "VOLUMEDOWN"},
        {"id": "wheel_right", "label": "Wheel ⟳", "signature": "VOLUMEUP"},
        {"id": "wheel_press", "label": "Wheel press", "signature": "MUTE"},
    ],
    "layers": [
        {
            "name": "Base",
            "bindings": {
                "k1": {"type": "shortcut", "value": "ctrl+c", "name": "Copy"},
                "k2": {"type": "shortcut", "value": "ctrl+v", "name": "Paste"},
                "k3": {"type": "shortcut", "value": "print", "name": "Screenshot"},
                "k4": {"type": "app", "value": "org.gnome.Ptyxis", "name": "Terminal"},
                "k5": {"type": "shortcut", "value": "playpause", "name": "Play/Pause"},
                "k6": {"type": "layer", "value": "next", "name": "Next layer"},
                "wheel_left": {"type": "passthrough"},
                "wheel_right": {"type": "passthrough"},
                "wheel_press": {"type": "passthrough"},
            },
        },
        {
            "name": "Windows",
            "bindings": {
                "k1": {"type": "shortcut", "value": "super+pageup", "name": "Workspace ↑"},
                "k2": {"type": "shortcut", "value": "super+pagedown", "name": "Workspace ↓"},
                "k3": {"type": "shortcut", "value": "super", "name": "Overview"},
                "k4": {"type": "shortcut", "value": "super+up", "name": "Maximize"},
                "k5": {"type": "shortcut", "value": "alt+f4", "name": "Close window"},
                "wheel_left": {"type": "shortcut", "value": "alt+shift+tab", "name": "Prev window"},
                "wheel_right": {"type": "shortcut", "value": "alt+tab", "name": "Next window"},
            },
        },
    ],
}


def default_config():
    return copy.deepcopy(DEFAULT_CONFIG)


def normalize(cfg):
    base = default_config()
    for k, v in base.items():
        cfg.setdefault(k, v)
    if not cfg["layers"]:
        cfg["layers"] = base["layers"][:1]
    for layer in cfg["layers"]:
        layer.setdefault("name", "Layer")
        layer.setdefault("bindings", {})
    return cfg


def load():
    try:
        with open(CONFIG_PATH) as f:
            return normalize(json.load(f))
    except FileNotFoundError:
        cfg = default_config()
        save(cfg)
        return cfg


def save(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


def resolve(cfg, control_id, layer_index):
    """Effective action for a control on a layer (non-base layers fall back to base)."""
    layers = cfg["layers"]
    layer_index = max(0, min(layer_index, len(layers) - 1))
    action = layers[layer_index]["bindings"].get(control_id)
    if (action is None or action.get("type") == "inherit") and layer_index != 0:
        action = layers[0]["bindings"].get(control_id)
    if action is None or action.get("type") == "inherit":
        return {"type": "none"}
    return action


def default_action(t):
    """A fresh action/step of type t with sensible empty fields."""
    if t == "layer":
        return {"type": t, "value": "next"}
    if t == "wait":
        return {"type": t, "value": 500}
    if t == "terminal":
        return {"type": t, "dir": "~", "command": "", "keep_open": True}
    if t == "sequence":
        return {"type": t, "steps": []}
    if t in ("none", "inherit", "passthrough"):
        return {"type": t}
    return {"type": t, "value": ""}
