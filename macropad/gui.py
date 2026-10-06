"""Macropad settings app (GTK4 / libadwaita)."""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import config as C  # noqa: E402
from . import board  # noqa: E402
from . import keys  # noqa: E402

APP_ID = "io.github.dino.Macropad"
KEY_IDS = ["k1", "k2", "k3", "k4", "k5", "k6"]
POSITIONS = [("k1", "the TOP-LEFT key"), ("k2", "the TOP-MIDDLE key"), ("k3", "the TOP-RIGHT key"),
             ("k4", "the BOTTOM-LEFT key"), ("k5", "the BOTTOM-MIDDLE key"), ("k6", "the BOTTOM-RIGHT key"),
             ("wheel_left", "the wheel: turn it LEFT one click"),
             ("wheel_right", "the wheel: turn it RIGHT one click"),
             ("wheel_press", "the wheel: PRESS it")]
WHEEL_IDS = [("wheel_left", "⟲"), ("wheel_press", "●"), ("wheel_right", "⟳")]
TYPE_ICONS = {
    "none": "action-unavailable-symbolic", "passthrough": "input-keyboard-symbolic",
    "inherit": "go-up-symbolic", "shortcut": "input-keyboard-symbolic",
    "text": "insert-text-symbolic", "command": "utilities-terminal-symbolic",
    "app": "application-x-executable-symbolic", "open": "web-browser-symbolic",
    "terminal": "utilities-terminal-symbolic", "layer": "view-paged-symbolic",
    "sequence": "view-list-ordered-symbolic", "wait": "preferences-system-time-symbolic",
}
CSS = """
.padkey { min-width: 130px; min-height: 100px; border-radius: 14px; padding: 8px; }
.padkey.selected { box-shadow: inset 0 0 0 3px @accent_color; }
.padkey.flash { background-color: alpha(@accent_bg_color, 0.55); }
.padkey .keyname { font-size: 0.8em; opacity: 0.6; }
.padkey .action { font-weight: bold; }
.padkey .inherited { opacity: 0.55; font-style: italic; }
.knob { min-width: 70px; min-height: 70px; border-radius: 999px; padding: 4px; }
.knob.selected { box-shadow: inset 0 0 0 3px @accent_color; }
.knob.flash { background-color: alpha(@accent_bg_color, 0.55); }
.minikey { min-width: 54px; min-height: 40px; border-radius: 8px; }
.minikey.current { background-color: @accent_bg_color; color: @accent_fg_color; }
.minikey.done { opacity: 0.45; }
.padframe { border-radius: 20px; padding: 18px; background-color: alpha(@card_fg_color, 0.04); }
"""


# ---------------- IPC ----------------
def ipc(msg, timeout=3.0):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    try:
        s.connect(str(C.SOCKET_PATH))
        s.sendall((json.dumps(msg) + "\n").encode())
        return json.loads(s.makefile().readline() or "null")
    finally:
        s.close()


def ipc_async(msg, callback, timeout=3.0):
    def worker():
        try:
            res = ipc(msg, timeout)
        except (OSError, ValueError):
            res = None
        GLib.idle_add(callback, res)
    threading.Thread(target=worker, daemon=True).start()


def subscribe(callback):
    def worker():
        while True:
            try:
                s = socket.socket(socket.AF_UNIX)
                s.connect(str(C.SOCKET_PATH))
                s.sendall(b'{"cmd":"subscribe"}\n')
                for line in s.makefile():
                    GLib.idle_add(callback, json.loads(line))
            except (OSError, ValueError):
                pass
            GLib.idle_add(callback, {"event": "status", "daemon": False})
            time.sleep(2)
    threading.Thread(target=worker, daemon=True).start()


# ---------------- helpers ----------------
def list_apps():
    apps = []
    for a in Gio.AppInfo.get_all():
        if a.should_show() and a.get_id():
            apps.append((a.get_display_name(), a.get_id().removesuffix(".desktop")))
    return sorted(apps, key=lambda x: x[0].lower())


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Macropad", default_width=1060, default_height=720)
        self.cfg = C.load()
        self.layer_idx = 0
        self.active_layer = 0
        self.selected = "k1"
        self.loading = False
        self.save_source = 0
        self.apps = list_apps()
        self.app_names = {i: n for n, i in self.apps}
        self.pad_buttons = {}
        self.store_row = None
        self.open_step = None  # index of the expanded step in a chain editor

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Macropad", subtitle="Connecting…")
        header.set_title_widget(self.title)
        ident = Gtk.Button(label="Identify keys", tooltip_text="Tell the app which physical key is which")
        ident.connect("clicked", self.identify_keys)
        header.pack_start(ident)
        toolbar.add_top_bar(header)

        self.banner = Adw.Banner(title="The macropad background service isn't running",
                                 button_label="Start service")
        self.banner.connect("button-clicked", self.on_start_service)
        toolbar.add_top_bar(self.banner)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        body.append(self.build_left())
        self.editor_scroller = Gtk.ScrolledWindow(hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        body.append(self.editor_scroller)
        toolbar.set_content(body)
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

        self.refresh_layers()
        self.refresh_pad()
        self.build_editor()
        subscribe(self.on_daemon_event)

    # ---------- left side: layers + pad ----------
    def build_left(self):
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        lbar = Gtk.Box(spacing=6)
        lbar.append(Gtk.Label(label="Layer", css_classes=["heading"]))
        self.layer_model = Gtk.StringList()
        self.layer_dd = Gtk.DropDown(model=self.layer_model, hexpand=True)
        self.layer_dd.connect("notify::selected", self.on_layer_selected)
        lbar.append(self.layer_dd)
        for icon, tip, cb in [("list-add-symbolic", "Add layer", self.on_add_layer),
                              ("document-edit-symbolic", "Rename layer", self.on_rename_layer),
                              ("user-trash-symbolic", "Delete layer", self.on_delete_layer)]:
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["flat"])
            b.connect("clicked", cb)
            lbar.append(b)
        left.append(lbar)

        self.activate_btn = Gtk.Button(label="Make this the active layer", css_classes=["pill"])
        self.activate_btn.connect("clicked", lambda *_: ipc_async(
            {"cmd": "set_layer", "layer": self.layer_idx}, lambda r: None))
        left.append(self.activate_btn)

        frame = Gtk.Box(spacing=18, css_classes=["padframe"], halign=Gtk.Align.CENTER)
        grid = Gtk.Grid(row_spacing=10, column_spacing=10)
        for i, cid in enumerate(KEY_IDS):
            btn = self.make_pad_button(cid, ["padkey", "card"])
            grid.attach(btn, i % 3, i // 3, 1, 1)
        frame.append(grid)
        wheel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        wheel.append(Gtk.Label(label="Wheel", css_classes=["caption-heading", "dim-label"]))
        for cid, glyph in WHEEL_IDS:
            wheel.append(self.make_pad_button(cid, ["knob", "card"], glyph))
        frame.append(wheel)
        left.append(frame)

        hint = Gtk.Label(wrap=True, max_width_chars=48, xalign=0, css_classes=["dim-label", "caption"],
                         label="Click a key to edit it. Pressing a key on the pad flashes it here, "
                               "so you can see which is which. Changes save automatically.")
        left.append(hint)
        return left

    def make_pad_button(self, cid, classes, glyph=None):
        btn = Gtk.Button(css_classes=classes)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        if glyph:
            box.append(Gtk.Label(label=glyph, css_classes=["title-3"]))
        else:
            box.append(Gtk.Label(css_classes=["keyname"]))
            box.append(Gtk.Image(pixel_size=20))
        action = Gtk.Label(css_classes=["action"], wrap=True, justify=Gtk.Justification.CENTER,
                           max_width_chars=14, ellipsize=3 if glyph else 0, lines=2)
        box.append(action)
        btn.set_child(box)
        btn.connect("clicked", lambda *_: self.select(cid))
        self.pad_buttons[cid] = btn
        return btn

    def control(self, cid):
        return next(c for c in self.cfg["controls"] if c["id"] == cid)

    def summarize(self, action):
        t, v = action.get("type", "none"), action.get("value", "")
        if action.get("name"):
            return action["name"]
        if t == "shortcut":
            return keys.pretty_combo(v) or "—"
        if t == "text":
            return f"“{v[:18]}”"
        if t == "command":
            return v[:22] or "—"
        if t == "app":
            return self.app_names.get(v, v) or "—"
        if t == "open":
            return os.path.basename(v.rstrip("/")) or v or "—"
        if t == "terminal":
            cmd = action.get("command", "").strip()
            where = action.get("dir") or "~"
            return f"{cmd[:16]} in {os.path.basename(where.rstrip('/')) or where}" if cmd else f"Terminal in {where}"
        if t == "wait":
            return f"Wait {v} ms"
        if t == "sequence":
            n = len(action.get("steps", []))
            return f"{n} step{'s' if n != 1 else ''}"
        if t == "layer":
            if v in ("next", "prev"):
                return f"{v.title()} layer"
            try:
                return f"→ {self.cfg['layers'][int(v)]['name']}"
            except (ValueError, IndexError):
                return "Layer ?"
        if t == "passthrough":
            return "Original"
        return "—"

    def refresh_pad(self):
        bindings = self.cfg["layers"][self.layer_idx]["bindings"]
        for cid, btn in self.pad_buttons.items():
            own = bindings.get(cid)
            inherited = self.layer_idx != 0 and (own is None or own.get("type") == "inherit")
            action = C.resolve(self.cfg, cid, self.layer_idx)
            box = btn.get_child()
            labels = [w for w in iter_children(box) if isinstance(w, Gtk.Label)]
            act_label = labels[-1]
            act_label.set_label(self.summarize(action))
            if inherited:
                act_label.add_css_class("inherited")
            else:
                act_label.remove_css_class("inherited")
            if cid in KEY_IDS:
                labels[0].set_label(self.control(cid)["label"])
                img = next(w for w in iter_children(box) if isinstance(w, Gtk.Image))
                img.set_from_icon_name(TYPE_ICONS.get(action.get("type"), "input-keyboard-symbolic"))
            btn.set_tooltip_text(f"{self.control(cid)['label']}: {self.summarize(action)}"
                                 + (" (from base layer)" if inherited else ""))
            if cid == self.selected:
                btn.add_css_class("selected")
            else:
                btn.remove_css_class("selected")

    def refresh_layers(self):
        self.loading = True
        self.layer_model.splice(0, self.layer_model.get_n_items(),
                                [l["name"] for l in self.cfg["layers"]])
        self.layer_dd.set_selected(self.layer_idx)
        self.loading = False
        self.update_title()

    def update_title(self, connected=None, daemon=None):
        if connected is not None:
            self._connected, self._daemon = connected, daemon
        connected, daemon = getattr(self, "_connected", False), getattr(self, "_daemon", False)
        active = self.cfg["layers"][min(self.active_layer, len(self.cfg["layers"]) - 1)]["name"]
        if not daemon:
            sub = "Service not running"
        elif not connected:
            sub = "Pad not connected"
        else:
            sub = f"Pad connected · active layer: {active}"
        self.title.set_subtitle(sub)
        self.banner.set_revealed(not daemon)
        self.activate_btn.set_sensitive(daemon and self.layer_idx != self.active_layer)
        self.activate_btn.set_label("This is the active layer" if self.layer_idx == self.active_layer
                                    else "Make this the active layer")

    def select(self, cid):
        self.selected = cid
        self.open_step = None
        self.refresh_pad()
        self.build_editor()

    # ---------- layer management ----------
    def on_layer_selected(self, dd, _):
        if self.loading:
            return
        self.layer_idx = dd.get_selected()
        self.refresh_pad()
        self.build_editor()
        self.update_title()

    def ask_name(self, heading, initial, callback):
        dlg = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "OK")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")

        def on_resp(_d, resp):
            if resp == "ok" and entry.get_text().strip():
                callback(entry.get_text().strip())
        dlg.connect("response", on_resp)
        dlg.present(self)

    def on_add_layer(self, *_):
        def done(name):
            self.cfg["layers"].append({"name": name, "bindings": {}})
            self.layer_idx = len(self.cfg["layers"]) - 1
            self.refresh_layers()
            self.refresh_pad()
            self.build_editor()
            self.schedule_save()
        self.ask_name("New layer", f"Layer {len(self.cfg['layers']) + 1}", done)

    def on_rename_layer(self, *_):
        def done(name):
            self.cfg["layers"][self.layer_idx]["name"] = name
            self.refresh_layers()
            self.schedule_save()
        self.ask_name("Rename layer", self.cfg["layers"][self.layer_idx]["name"], done)

    def on_delete_layer(self, *_):
        if self.layer_idx == 0:
            self.toast("The base layer can't be deleted")
            return
        dlg = Adw.AlertDialog(heading=f"Delete “{self.cfg['layers'][self.layer_idx]['name']}”?",
                              body="All bindings on this layer will be lost.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(_d, resp):
            if resp != "delete":
                return
            del self.cfg["layers"][self.layer_idx]
            self.layer_idx = max(0, self.layer_idx - 1)
            self.refresh_layers()
            self.refresh_pad()
            self.build_editor()
            self.schedule_save()
        dlg.connect("response", on_resp)
        dlg.present(self)

    # ---------- editor ----------
    def binding(self):
        return self.cfg["layers"][self.layer_idx]["bindings"].get(self.selected)

    def set_binding(self, action):
        self.cfg["layers"][self.layer_idx]["bindings"][self.selected] = action
        self.refresh_pad()
        self.schedule_save()

    def build_editor(self):
        self.loading = True
        ctl = self.control(self.selected)
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title=ctl["label"],
                                 description=f"Editing on layer “{self.cfg['layers'][self.layer_idx]['name']}”")
        label_row = Adw.EntryRow(title="Key label", text=ctl["label"])
        label_row.connect("changed", self.on_label_changed)
        g.add(label_row)
        sig_row = Adw.ActionRow(title=f"Pad signal: {keys.pretty_combo(ctl['signature'])}",
                                subtitle="How the app recognises this key. It is intercepted, "
                                         "so only the action below runs", subtitle_selectable=True)
        learn = Gtk.Button(label="Re-detect", valign=Gtk.Align.CENTER)
        learn.set_tooltip_text("Press this, then press the physical key on the pad")
        learn.connect("clicked", self.on_learn)
        sig_row.add_suffix(learn)
        g.add(sig_row)
        page.add(g)

        action = self.binding() or {"type": "inherit" if self.layer_idx else "none"}
        types = [t for t in C.ACTION_TYPES if t != "inherit" or self.layer_idx != 0]
        self.types = types
        ag = Adw.PreferencesGroup(title="Action")
        test = Gtk.Button(label="Test", css_classes=["flat"], valign=Gtk.Align.CENTER)
        test.set_tooltip_text("Run this action now (as if the key was pressed)")
        test.connect("clicked", self.on_test)
        ag.set_header_suffix(test)
        type_row = Adw.ComboRow(title="When pressed",
                                model=Gtk.StringList.new([C.ACTION_TYPES[t] for t in types]))
        cur = action.get("type", "none")
        type_row.set_selected(types.index(cur) if cur in types else 0)
        type_row.connect("notify::selected", self.on_type_changed)
        ag.add(type_row)
        if cur not in ("none", "inherit"):
            name_row = Adw.EntryRow(title="Display name (optional)", text=action.get("name", ""))
            name_row.connect("changed", lambda r: self.update_field("name", r.get_text()))
            ag.add(name_row)
        page.add(ag)

        if cur == "sequence":
            page.add(self.build_steps_group(action))
        else:
            vg = self.build_value_group(cur, action, self.update_field)
            if vg:
                page.add(vg)
        if cur == "inherit":
            base = C.resolve(self.cfg, self.selected, 0)
            ig = Adw.PreferencesGroup()
            ig.add(Adw.ActionRow(title="Base layer action",
                                 subtitle=f"{C.ACTION_TYPES[base.get('type', 'none')]}: {self.summarize(base)}"))
            page.add(ig)

        page.add(self.build_board_group(ctl, action))
        self.editor_scroller.set_child(page)
        self.loading = False

    # ---------- on-board keymap ----------
    def board_index(self, ctl, keymap):
        for idx, entry in keymap.items():
            if board.decode(entry)[1] == ctl["signature"]:
                if ctl.get("board_index") != idx:
                    ctl["board_index"] = idx
                    self.schedule_save()
                return idx
        return ctl.get("board_index")

    def build_board_group(self, ctl, action):
        g = Adw.PreferencesGroup(
            title="Stored on the pad",
            description="What the pad itself sends for this key, kept in its own memory. A shortcut "
                        "stored here works on any computer, even without this app.")
        try:
            with board.Board() as b:
                keymap, factory = b.keymap(), b.factory()
        except (board.BoardError, OSError) as ex:
            g.add(Adw.ActionRow(title="Pad memory not reachable", subtitle=str(ex)))
            return g
        idx = self.board_index(ctl, keymap)
        if idx is None:
            g.add(Adw.ActionRow(title="Couldn't match this key to the pad's memory",
                                subtitle="Use “Identify keys” first"))
            return g
        combo, _sig = board.decode(keymap[idx])
        shown = keys.pretty_combo(combo) if _sig else combo
        if keymap[idx] != factory[idx]:
            fcombo, fsig = board.decode(factory[idx])
            shown += f"  (changed; factory default is {keys.pretty_combo(fcombo) if fsig else fcombo})"
        row = Adw.ActionRow(title="Pad sends", subtitle=shown)
        restore = Gtk.Button(label="Factory default", valign=Gtk.Align.CENTER, css_classes=["flat"],
                             tooltip_text="Put this key back to what it sent out of the box")
        restore.connect("clicked", lambda *_: self.confirm_board_write(ctl, idx, None))
        row.add_suffix(restore)
        g.add(row)
        self.store_row = None
        if action.get("type") == "shortcut":
            self.store_row = Adw.ActionRow()
            self.store_btn = Gtk.Button(label="Store on pad", valign=Gtk.Align.CENTER,
                                        css_classes=["suggested-action"])
            self.store_btn.connect("clicked", lambda *_: self.confirm_board_write(
                ctl, idx, (self.binding() or {}).get("value", "")))
            self.store_row.add_suffix(self.store_btn)
            g.add(self.store_row)
            self.update_store_row()
        elif action.get("type") == "passthrough":
            g.add(Adw.ActionRow(title="This key uses what the pad sends (shown above)"))
        else:
            g.add(Adw.ActionRow(title="Only keyboard shortcuts and media keys can be stored on the pad",
                                subtitle="Commands, apps, text and layers need the background service",
                                css_classes=["dim-label"]))
        return g

    def update_store_row(self):
        if not self.store_row:
            return
        value = (self.binding() or {}).get("value", "").strip()
        why = None
        if self.layer_idx != 0:
            why = "Only base-layer shortcuts can be stored on the pad"
        elif not value:
            why = "Enter a shortcut above first"
        else:
            try:
                board.encode(value)
            except (board.BoardError, ValueError) as ex:
                why = str(ex)
        self.store_row.set_title(f"Store “{keys.pretty_combo(value)}” on the pad" if value
                                 else "Store this shortcut on the pad")
        self.store_row.set_subtitle(why or "The key will send this by itself")
        self.store_btn.set_sensitive(why is None)

    def confirm_board_write(self, ctl, idx, combo):
        what = f"“{keys.pretty_combo(combo)}”" if combo else "its factory default"
        dlg = Adw.AlertDialog(heading=f"Write to the pad's memory?",
                              body=f"{ctl['label']} will be set to {what} in the pad's own memory. "
                                   "Only this key's entry is changed; you can put it back with “Factory default”.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("write", "Write")
        dlg.set_response_appearance("write", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r == "write" and self.board_write(ctl, idx, combo))
        dlg.present(self)

    def board_write(self, ctl, idx, combo):
        try:
            with board.Board() as b:
                entry = board.encode(combo) if combo else b.factory()[idx]
                new_combo, new_sig = board.decode(entry)
                if not new_sig:
                    raise board.BoardError(f"the app couldn't recognise “{new_combo}” afterwards")
                clash = next((c for c in self.cfg["controls"]
                              if c["signature"] == new_sig and c["id"] != ctl["id"]), None)
                if clash:
                    raise board.BoardError(f"{clash['label']} already sends {keys.pretty_combo(new_sig)}; "
                                           "the app couldn't tell the two keys apart")
                b.write_key(idx, entry)
        except (board.BoardError, ValueError, OSError) as ex:
            self.toast(f"Not written: {ex}")
            return
        ctl["signature"] = new_sig
        ctl["board_index"] = idx
        if combo:
            old = self.cfg["layers"][0]["bindings"].get(ctl["id"], {})
            self.cfg["layers"][0]["bindings"][ctl["id"]] = {
                "type": "passthrough", "name": old.get("name") or keys.pretty_combo(combo)}
        self.schedule_save()
        self.refresh_pad()
        self.build_editor()
        self.toast(f"Stored on the pad and verified: {ctl['label']} → {keys.pretty_combo(new_combo)}")

    # ---------- identify keys wizard ----------
    def identify_keys(self, *_):
        state = {"step": 0, "sigs": {}, "gen": 0, "open": True}
        dlg = Adw.Dialog(title="Identify keys", content_width=420)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=12,
                      margin_bottom=18, margin_start=18, margin_end=18)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(box)
        dlg.set_child(tv)
        prompt = Gtk.Label(wrap=True, css_classes=["title-3"], justify=Gtk.Justification.CENTER)
        note = Gtk.Label(wrap=True, css_classes=["dim-label"], justify=Gtk.Justification.CENTER)
        box.append(prompt)
        mini = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER)
        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        cells = {}
        for i, cid in enumerate(KEY_IDS):
            cells[cid] = Gtk.Label(label=str(i + 1), css_classes=["card", "minikey"])
            grid.attach(cells[cid], i % 3, i // 3, 1, 1)
        mini.append(grid)
        wheel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for cid, glyph in WHEEL_IDS:
            cells[cid] = Gtk.Label(label=glyph, css_classes=["card", "minikey"])
            wheel.append(cells[cid])
        mini.append(wheel)
        box.append(mini)
        box.append(note)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        skip = Gtk.Button(label="Skip (keep as is)", css_classes=["pill"])
        buttons.append(skip)
        box.append(buttons)

        def show():
            for i, (cid, _) in enumerate(POSITIONS):
                cells[cid].remove_css_class("current")
                cells[cid].remove_css_class("done")
                if i < state["step"]:
                    cells[cid].add_css_class("done")
                elif i == state["step"]:
                    cells[cid].add_css_class("current")
            prompt.set_label(f"Press {POSITIONS[state['step']][1]}")

        def listen():
            state["gen"] += 1
            gen = state["gen"]
            ipc_async({"cmd": "learn", "timeout": 60}, lambda r: got(gen, r), timeout=62)

        def got(gen, res):
            if not state["open"] or gen != state["gen"]:
                return
            if not res or "signature" not in res:
                if res is None:
                    note.set_label("The background service isn't responding")
                    return
                listen()
                return
            sig = res["signature"]
            dup = next((POSITIONS[i][0] for i, (c, _) in enumerate(POSITIONS)
                        if state["sigs"].get(c) == sig), None)
            if dup:
                note.set_label(f"That one was already used for {self.control(dup)['label']}. Try again.")
                listen()
                return
            advance(sig)

        def advance(sig):
            cid = POSITIONS[state["step"]][0]
            state["sigs"][cid] = sig
            note.set_label("")
            state["step"] += 1
            if state["step"] == len(POSITIONS):
                finish()
            else:
                show()
                listen()

        def on_skip(*_):
            state["gen"] += 1
            sig = self.control(POSITIONS[state["step"]][0])["signature"]
            # Cancel first, then advance, so the cancel can't hit the next step's learn.
            ipc_async({"cmd": "cancel_learn"}, lambda r: state["open"] and advance(sig))
        skip.connect("clicked", on_skip)

        def finish():
            state["open"] = False
            for c in self.cfg["controls"]:
                if c["id"] in state["sigs"]:
                    c["signature"] = state["sigs"][c["id"]]
                    c.pop("board_index", None)
            self.schedule_save()
            self.refresh_pad()
            self.build_editor()
            dlg.close()
            self.toast("Keys identified. The on-screen keys now match the pad")

        def on_closed(*_):
            if state["open"]:
                state["open"] = False
                ipc_async({"cmd": "cancel_learn"}, lambda r: None)
        dlg.connect("closed", on_closed)
        note.set_label("Each key keeps the action shown at its position on screen.")
        show()
        listen()
        dlg.present(self)

    def build_value_group(self, t, action, set_field):
        """Editor widgets for one action (or sequence step); edits go through set_field(field, value)."""
        v = action.get("value", "")
        g = Adw.PreferencesGroup()
        if t == "shortcut":
            row = Adw.EntryRow(title="Keys", text=v)
            row.connect("changed", lambda r: set_field("value", r.get_text()))
            rec = Gtk.Button(icon_name="media-record-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text="Record a shortcut from your keyboard", css_classes=["flat"])
            rec.connect("clicked", lambda *_: self.record_shortcut(row))
            row.add_suffix(rec)
            g.add(row)
            g.set_description("Examples: ctrl+shift+t · super+e · playpause · print\n"
                              "Sequences: separate with spaces, e.g. “ctrl+a ctrl+c sleep:100 alt+tab ctrl+v”")
        elif t == "text":
            g.set_title("Text to type")
            g.set_description("ASCII is typed directly. Text with other characters (emoji, accents) "
                              "goes through the clipboard and Ctrl+V.")
            tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                              left_margin=10, right_margin=10, css_classes=["card"], height_request=120)
            tv.get_buffer().set_text(v)
            tv.get_buffer().connect("changed", lambda b: set_field(
                "value", b.get_text(b.get_start_iter(), b.get_end_iter(), False)))
            g.add(tv)
        elif t == "command":
            row = Adw.EntryRow(title="Shell command", text=v)
            row.connect("changed", lambda r: set_field("value", r.get_text()))
            g.add(row)
            g.set_description("Runs via sh -c in your home directory, e.g. "
                              "“nautilus ~/Downloads” or “playerctl next”")
        elif t == "app":
            names = [n for n, _ in self.apps]
            row = Adw.ComboRow(title="Application", model=Gtk.StringList.new(names), enable_search=True)
            row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            ids = [i for _, i in self.apps]
            if v in ids:
                row.set_selected(ids.index(v))
            elif ids:
                row.set_selected(Gtk.INVALID_LIST_POSITION)
            row.connect("notify::selected", lambda r, _: set_field(
                "value", ids[r.get_selected()]) if r.get_selected() < len(ids) else None)
            g.add(row)
        elif t == "open":
            row = Adw.EntryRow(title="URL, file or folder", text=v)
            row.connect("changed", lambda r: set_field("value", r.get_text()))
            browse = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat"], tooltip_text="Choose a file")
            browse.connect("clicked", lambda *_: self.pick_file(row))
            row.add_suffix(browse)
            g.add(row)
        elif t == "layer":
            opts = [("next", "Next layer"), ("prev", "Previous layer")] + \
                   [(str(i), f"Go to “{l['name']}”") for i, l in enumerate(self.cfg["layers"])]
            row = Adw.ComboRow(title="Switch to", model=Gtk.StringList.new([o[1] for o in opts]))
            vals = [o[0] for o in opts]
            row.set_selected(vals.index(str(v)) if str(v) in vals else 0)
            if str(v) not in vals:  # e.g. the target layer was deleted
                action["value"] = "next"
                self.schedule_save()
            row.connect("notify::selected", lambda r, _: set_field("value", vals[r.get_selected()]))
            g.add(row)
        elif t == "terminal":
            row = Adw.EntryRow(title="Folder", text=action.get("dir", "~"))
            row.connect("changed", lambda r: set_field("dir", r.get_text()))
            browse = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat"], tooltip_text="Choose a folder")
            browse.connect("clicked", lambda *_: self.pick_folder(row))
            row.add_suffix(browse)
            g.add(row)
            cmd = Adw.EntryRow(title="Command to run there (optional)", text=action.get("command", ""))
            cmd.connect("changed", lambda r: set_field("command", r.get_text()))
            g.add(cmd)
            keep = Adw.SwitchRow(title="Keep the terminal open afterwards",
                                 subtitle="Leaves you at a shell prompt when the command exits",
                                 active=action.get("keep_open", True))
            keep.connect("notify::active", lambda r, _: set_field("keep_open", r.get_active()))
            g.add(keep)
            g.set_description("Opens a new terminal window in the folder and runs the command, "
                              "e.g. folder “~/Coding/macropad”, command “claude”.")
        elif t == "wait":
            row = Adw.SpinRow.new_with_range(0, 60000, 100)
            row.set_title("Milliseconds")
            row.set_value(int(v or 0))
            row.connect("notify::value", lambda r, _: set_field("value", int(r.get_value())))
            g.add(row)
            g.set_description("Gives a window time to open before the next step, e.g. 800 ms.")
        else:
            return None
        return g

    def update_field(self, field, value):
        if self.loading:
            return
        action = dict(self.binding() or {"type": "none"})
        action[field] = value
        if field == "name" and not value:
            action.pop("name", None)
        self.set_binding(action)
        if field == "value":
            self.update_store_row()

    def on_label_changed(self, row):
        if self.loading:
            return
        self.control(self.selected)["label"] = row.get_text()
        self.refresh_pad()
        self.schedule_save()

    def on_type_changed(self, row, _):
        if self.loading:
            return
        t = self.types[row.get_selected()]
        old = self.binding() or {}
        if t == old.get("type"):
            return
        new = self.new_action(t)
        if old.get("name") and t not in ("none", "inherit"):
            new["name"] = old["name"]
        if t == "inherit":
            self.cfg["layers"][self.layer_idx]["bindings"].pop(self.selected, None)
            self.refresh_pad()
            self.schedule_save()
        else:
            self.set_binding(new)
        GLib.idle_add(self.build_editor)

    def new_action(self, t):
        new = C.default_action(t)
        if t == "app" and self.apps:
            new["value"] = self.apps[0][1]
        return new

    def on_test(self, *_):
        action = C.resolve(self.cfg, self.selected, self.layer_idx)
        first = next((st for st in action.get("steps", []) if st.get("type") != "wait"), {})

        def run():
            ipc_async({"cmd": "run", "control": self.selected, "action": action},
                      lambda r: self.toast("Service not running") if r is None else None)
            return False
        # Short delay so shortcut/text actions land in the window you switch to, not here.
        if action.get("type") in ("shortcut", "text", "passthrough") or \
                first.get("type") in ("shortcut", "text"):
            self.toast("Running in 2 seconds — focus the target window")
            GLib.timeout_add(2000, run)
        else:
            run()

    def on_learn(self, *_):
        self.toast("Press the key on your pad now…")

        def done(res):
            if not res or "signature" not in res:
                self.toast("No key detected" if res else "Service not running")
                return
            sig = res["signature"]
            other = next((c for c in self.cfg["controls"]
                          if c["signature"] == sig and c["id"] != self.selected), None)
            if other:
                other["signature"] = self.control(self.selected)["signature"]
                self.toast(f"Swapped signals with {other['label']}")
            self.control(self.selected)["signature"] = sig
            self.schedule_save()
            self.build_editor()
        ipc_async({"cmd": "learn", "timeout": 10}, done, timeout=12)

    def record_shortcut(self, row):
        dlg = Adw.AlertDialog(heading="Press a shortcut",
                              body="Press the key combination on your keyboard.\nEsc cancels.")
        dlg.add_response("cancel", "Cancel")
        ctrl = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        surface = self.get_surface()

        def on_key(_c, keyval, keycode, state):
            code = keycode - 8  # X/XKB keycode -> evdev keycode
            if code in keys.MODIFIERS:
                return True
            if keyval == Gdk.KEY_Escape and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                                                   | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
                dlg.close()
                return True
            mods = []
            for mask, name in [(Gdk.ModifierType.CONTROL_MASK, "ctrl"), (Gdk.ModifierType.SHIFT_MASK, "shift"),
                               (Gdk.ModifierType.ALT_MASK, "alt"), (Gdk.ModifierType.SUPER_MASK, "super")]:
                if state & mask:
                    mods.append(name)
            row.set_text("+".join(mods + [keys.name_of(code).lower()]))
            dlg.close()
            return True
        ctrl.connect("key-pressed", on_key)
        dlg.add_controller(ctrl)
        if isinstance(surface, Gdk.Toplevel):
            surface.inhibit_system_shortcuts(None)
            dlg.connect("closed", lambda *_: surface.restore_system_shortcuts())
        dlg.present(self)

    def pick_file(self, row):
        fd = Gtk.FileDialog(title="Choose a file or folder")

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            row.set_text(f.get_path())
        fd.open(self, None, done)

    def pick_folder(self, row):
        fd = Gtk.FileDialog(title="Choose a folder")
        start = os.path.expanduser(row.get_text().strip() or "~")
        if os.path.isdir(start):
            fd.set_initial_folder(Gio.File.new_for_path(start))

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            home = os.path.expanduser("~")
            row.set_text("~" + path[len(home):] if path == home or path.startswith(home + "/") else path)
        fd.select_folder(self, None, done)

    # ---------- chain of steps ----------
    def build_steps_group(self, action):
        steps = action.setdefault("steps", [])
        g = Adw.PreferencesGroup(
            title="Steps",
            description="Run from top to bottom. Put a Wait after opening something "
                        "if the next step types into it.")
        add = Gtk.MenuButton(label="Add step", valign=Gtk.Align.CENTER, css_classes=["flat"])
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pop = Gtk.Popover(child=menu)
        for t, label in C.STEP_TYPES.items():
            b = Gtk.Button(css_classes=["flat"])
            inner = Gtk.Box(spacing=8)
            inner.append(Gtk.Image(icon_name=TYPE_ICONS[t]))
            inner.append(Gtk.Label(label=label))
            b.set_child(inner)
            b.connect("clicked", lambda _b, t=t: (pop.popdown(), self.add_step(t)))
            menu.append(b)
        add.set_popover(pop)
        g.set_header_suffix(add)
        if not steps:
            g.add(Adw.ActionRow(title="No steps yet", subtitle="Use “Add step” to build the chain",
                                css_classes=["dim-label"]))
        for i, step in enumerate(steps):
            g.add(self.build_step_row(steps, i))
        return g

    def build_step_row(self, steps, i):
        step = steps[i]
        t = step.get("type", "none")
        exp = Adw.ExpanderRow(title=f"{i + 1}. {C.STEP_TYPES.get(t, t)}", subtitle=self.summarize(step),
                              expanded=i == self.open_step)
        exp.add_prefix(Gtk.Image(icon_name=TYPE_ICONS.get(t, "input-keyboard-symbolic")))
        exp.connect("notify::expanded", lambda r, _: setattr(
            self, "open_step", i if r.get_expanded() else (None if self.open_step == i else self.open_step)))
        for icon, tip, delta, ok in [("go-up-symbolic", "Move up", -1, i > 0),
                                     ("go-down-symbolic", "Move down", 1, i < len(steps) - 1)]:
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER,
                           css_classes=["flat"], sensitive=ok)
            b.connect("clicked", lambda *_, d=delta: self.move_step(i, d))
            exp.add_suffix(b)
        rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove step",
                        valign=Gtk.Align.CENTER, css_classes=["flat"])
        rm.connect("clicked", lambda *_: self.remove_step(i))
        exp.add_suffix(rm)

        def set_field(field, value):
            if self.loading:
                return
            step[field] = value
            exp.set_subtitle(self.summarize(step))
            self.refresh_pad()
            self.schedule_save()
        vg = self.build_value_group(t, step, set_field)
        if vg:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=8, margin_bottom=12,
                          margin_start=12, margin_end=12)
            box.append(vg)
            exp.add_row(box)
        return exp

    def steps(self):
        return self.binding().setdefault("steps", [])

    def add_step(self, t):
        self.steps().append(self.new_action(t))
        self.open_step = len(self.steps()) - 1
        self.refresh_pad()
        self.schedule_save()
        self.build_editor()

    def move_step(self, i, delta):
        steps = self.steps()
        steps[i], steps[i + delta] = steps[i + delta], steps[i]
        self.open_step = i + delta if self.open_step == i else None
        self.schedule_save()
        self.build_editor()

    def remove_step(self, i):
        del self.steps()[i]
        self.open_step = None
        self.refresh_pad()
        self.schedule_save()
        self.build_editor()

    # ---------- persistence / daemon ----------
    def schedule_save(self):
        if self.save_source:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(350, self.do_save)

    def do_save(self):
        self.save_source = 0
        C.save(self.cfg)
        ipc_async({"cmd": "reload"}, lambda r: None)
        return False

    def on_daemon_event(self, msg):
        if msg.get("event") == "status":
            daemon = msg.get("daemon", True)
            if daemon and msg.get("layer") is not None and msg["layer"] != self.active_layer:
                self.active_layer = msg["layer"]
                if self.active_layer < len(self.cfg["layers"]):
                    self.layer_idx = self.active_layer
                    self.refresh_layers()
                    self.refresh_pad()
                    self.build_editor()
            self.update_title(msg.get("connected", False), daemon)
        elif msg.get("event") == "press" and msg.get("control") in self.pad_buttons:
            btn = self.pad_buttons[msg["control"]]
            btn.add_css_class("flash")
            GLib.timeout_add(220, lambda: btn.remove_css_class("flash") or False)

    def on_start_service(self, *_):
        r = subprocess.run(["systemctl", "--user", "start", "macropad.service"],
                           capture_output=True, text=True)
        if r.returncode:
            self.toast("Couldn't start the service. Run install.sh first")

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))


def iter_children(widget):
    child = widget.get_first_child()
    while child:
        yield child
        child = child.get_next_sibling()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.get_active_window()
        if not win:
            css = Gtk.CssProvider()
            css.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            win = Window(self)
        win.present()


def main():
    sys.exit(App().run(sys.argv))


if __name__ == "__main__":
    main()
