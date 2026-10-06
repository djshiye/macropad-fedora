"""Macropad daemon: grabs the pad exclusively and turns its keys into actions."""
import asyncio
import glob
import json
import logging
import os
import signal
import subprocess
import time

import evdev
from evdev import UInput, ecodes as e

from . import config as C
from . import keys

log = logging.getLogger("macropad")


def _spawn(argv):
    subprocess.Popen(argv, start_new_session=True, cwd=os.path.expanduser("~"),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title, body="", icon="input-keyboard"):
    try:
        _spawn(["notify-send", "-a", "Macropad", "-i", icon, "-e", "-t", "1500",
                "-h", "string:x-canonical-private-synchronous:macropad", title, body])
    except OSError:
        pass


class Daemon:
    def __init__(self):
        self.cfg = C.load()
        self.cfg_mtime = self._mtime()
        self.layer = 0
        self.devices = []
        self.held = set()
        self.mod_pending = None
        self.learners = []
        self.quiet = (None, 0.0)  # (signature, until): swallow repeats right after a learn
        self.last_alert = 0.0
        self.subscribers = set()
        self.emit_lock = asyncio.Lock()
        self.ui = UInput({e.EV_KEY: keys.EMIT_KEYS}, name="macropad-virtual-keyboard")

    # ---------- config ----------
    def _mtime(self):
        try:
            return C.CONFIG_PATH.stat().st_mtime
        except OSError:
            return None

    def reload(self):
        try:
            self.cfg = C.load()
        except (OSError, ValueError) as ex:
            log.error("config reload failed: %s", ex)
            notify("Macropad config error", str(ex), "dialog-error")
            return
        self.cfg_mtime = self._mtime()
        self.layer = min(self.layer, len(self.cfg["layers"]) - 1)
        log.info("config reloaded")
        self.broadcast(self.status())

    # ---------- device handling ----------
    def connect(self):
        paths = sorted(glob.glob(self.cfg["device_glob"]))
        if not paths:
            return
        loop = asyncio.get_running_loop()
        try:
            for p in paths:
                dev = evdev.InputDevice(p)
                dev.grab()
                self.devices.append(dev)
                loop.add_reader(dev.fd, self._on_readable, dev)
        except OSError as ex:
            log.warning("could not open pad (%s), retrying", ex)
            self.disconnect()
            return
        log.info("connected: %s", ", ".join(d.path for d in self.devices))
        self.broadcast(self.status())

    def disconnect(self):
        loop = asyncio.get_running_loop()
        for dev in self.devices:
            try:
                loop.remove_reader(dev.fd)
                dev.close()
            except OSError:
                pass
        if self.devices:
            log.info("pad disconnected")
        self.devices = []
        self.held.clear()
        self.broadcast(self.status())

    def _on_readable(self, dev):
        try:
            for ev in dev.read():
                if ev.type == e.EV_KEY:
                    self.on_key(ev.code, ev.value)
        except OSError:
            self.disconnect()

    async def watch_loop(self):
        while True:
            if not self.devices:
                self.connect()
            if self._mtime() != self.cfg_mtime:
                self.reload()
            await asyncio.sleep(2)

    # ---------- input -> trigger ----------
    def on_key(self, code, value):
        if code in keys.MODIFIERS:
            if value == 1:
                self.held.add(code)
                self.mod_pending = code
            elif value == 0:
                lone = self.mod_pending == code
                self.held.discard(code)
                if lone:
                    self.mod_pending = None
                    self.trigger(keys.signature(self.held, code))
        elif value == 1:
            self.mod_pending = None
            self.trigger(keys.signature(self.held, code))

    def control_for(self, sig):
        return next((c for c in self.cfg["controls"] if c["signature"] == sig), None)

    def trigger(self, sig):
        now = time.monotonic()
        if self.learners:
            for fut in self.learners:
                if not fut.done():
                    fut.set_result(sig)
            self.learners = []
            self.quiet = (sig, now + 0.6)
            return
        if sig == self.quiet[0] and now < self.quiet[1]:
            self.quiet = (sig, now + 0.6)
            return
        control = self.control_for(sig)
        self.broadcast({"event": "press", "signature": sig,
                        "control": control["id"] if control else None, "layer": self.layer})
        if not control:
            # Blocked: the pad sent something none of its keys should send.
            log.warning("blocked unexpected input from pad: %s", sig)
            if now - self.last_alert > 10:
                self.last_alert = now
                notify("Macropad blocked unexpected input", f"The pad sent “{keys.pretty_combo(sig)}”, "
                       "which isn't one of its known keys. It was not passed to your system.",
                       "dialog-warning")
            return
        self.run(C.resolve(self.cfg, control["id"], self.layer), control)

    # ---------- actions ----------
    def run(self, action, control):
        asyncio.create_task(self.execute(action, control))

    async def execute(self, action, control):
        try:
            if action.get("type") == "sequence":
                for step in action.get("steps", []):
                    if step.get("type") != "sequence":  # no nesting
                        await self.do(step, control)
            else:
                await self.do(action, control)
        except (ValueError, OSError) as ex:
            log.error("action %s failed: %s", action, ex)
            notify("Macropad action failed", f"{control['label']}: {ex}", "dialog-error")

    async def do(self, action, control):
        t, v = action.get("type", "none"), action.get("value", "")
        if t == "passthrough":
            codes = [keys.code_of(n) for n in control["signature"].split("+")]
            await self.play([("combo", codes)])
        elif t == "shortcut":
            await self.play(keys.parse_sequence(v))
        elif t == "text":
            await self.type_text(v)
        elif t == "wait":
            await asyncio.sleep(int(v or 0) / 1000)
        elif t == "command":
            _spawn(["sh", "-c", v])
        elif t == "app":
            _spawn(["gtk-launch", v])
        elif t == "open":
            _spawn(["xdg-open", os.path.expanduser(v)])
        elif t == "terminal":
            self.open_terminal(action)
        elif t == "layer":
            self.switch_layer(v)

    def open_terminal(self, action):
        folder = os.path.expanduser(action.get("dir") or "~")
        if not os.path.isdir(folder):
            raise ValueError(f"folder not found: {folder}")
        argv = [self.cfg.get("terminal") or "ptyxis", "--new-window", "-d", folder]
        cmd = action.get("command", "").strip()
        if cmd:
            # Interactive shell so ~/.bashrc puts things like ~/.local/bin on PATH.
            shell = os.environ.get("SHELL") or "/bin/bash"
            argv += ["--", shell, "-ic", f"{cmd}; exec {shell}" if action.get("keep_open", True) else cmd]
        _spawn(argv)

    def switch_layer(self, v):
        n = len(self.cfg["layers"])
        if v == "next":
            self.layer = (self.layer + 1) % n
        elif v == "prev":
            self.layer = (self.layer - 1) % n
        else:
            self.layer = max(0, min(int(v), n - 1))
        log.info("layer -> %d", self.layer)
        if self.cfg.get("notify_layer_change", True):
            notify(f"Layer: {self.cfg['layers'][self.layer]['name']}")
        self.broadcast(self.status())

    def _write(self, code, value):
        self.ui.write(e.EV_KEY, code, value)
        self.ui.syn()

    async def tap(self, codes):
        for c in codes:
            self._write(c, 1)
            await asyncio.sleep(0.004)
        await asyncio.sleep(0.012)
        for c in reversed(codes):
            self._write(c, 0)
            await asyncio.sleep(0.004)

    async def play(self, steps):
        async with self.emit_lock:
            for kind, arg in steps:
                if kind == "sleep":
                    await asyncio.sleep(arg / 1000)
                else:
                    await self.tap(arg)

    async def type_text(self, text):
        if all(ch in keys.US_CHARS for ch in text):
            steps = []
            for ch in text:
                code, shift = keys.US_CHARS[ch]
                steps.append(("combo", [e.KEY_LEFTSHIFT, code] if shift else [code]))
            await self.play(steps)
        else:
            # Non-ASCII: go through the clipboard and paste.
            proc = await asyncio.create_subprocess_exec("wl-copy", stdin=subprocess.PIPE)
            await proc.communicate(text.encode())
            await asyncio.sleep(0.15)
            await self.play([("combo", [e.KEY_LEFTCTRL, e.KEY_V])])

    # ---------- IPC ----------
    def status(self):
        return {"event": "status", "connected": bool(self.devices), "layer": self.layer,
                "layer_name": self.cfg["layers"][self.layer]["name"]}

    def broadcast(self, msg):
        data = (json.dumps(msg) + "\n").encode()
        for w in list(self.subscribers):
            try:
                w.write(data)
            except Exception:
                self.subscribers.discard(w)

    async def handle_client(self, reader, writer):
        def send(msg):
            writer.write((json.dumps(msg) + "\n").encode())
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except ValueError:
                    send({"error": "bad json"})
                    continue
                cmd = req.get("cmd")
                if cmd == "status":
                    send(self.status())
                elif cmd == "reload":
                    self.reload()
                    send({"ok": True})
                elif cmd == "subscribe":
                    self.subscribers.add(writer)
                    send(self.status())
                elif cmd == "learn":
                    fut = asyncio.get_running_loop().create_future()
                    self.learners.append(fut)
                    eof = asyncio.ensure_future(reader.read(1))  # client hung up -> stop learning
                    await asyncio.wait({fut, eof}, timeout=req.get("timeout", 15),
                                       return_when=asyncio.FIRST_COMPLETED)
                    hung_up = eof.done()
                    eof.cancel()
                    if fut in self.learners:
                        self.learners.remove(fut)
                    if fut.done() and not fut.cancelled():
                        send({"signature": fut.result()})
                    else:
                        send({"error": "cancelled" if hung_up or fut.cancelled() else "timeout"})
                        fut.cancel()
                    await writer.drain()
                    break  # learn is one-shot: the pending read above owns the stream
                elif cmd == "cancel_learn":
                    for fut in self.learners:
                        fut.cancel()
                    self.learners = []
                    send({"ok": True})
                elif cmd == "run":
                    control = next((c for c in self.cfg["controls"] if c["id"] == req["control"]), None)
                    if control:
                        action = req.get("action") or C.resolve(self.cfg, control["id"], req.get("layer", self.layer))
                        self.run(action, control)
                    send({"ok": bool(control)})
                elif cmd == "set_layer":
                    self.switch_layer(req["layer"])
                    send(self.status())
                else:
                    send({"error": f"unknown cmd {cmd}"})
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.subscribers.discard(writer)
            writer.close()

    async def main(self):
        try:
            C.SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        server = await asyncio.start_unix_server(self.handle_client, path=str(C.SOCKET_PATH))
        os.chmod(C.SOCKET_PATH, 0o600)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGHUP, self.reload)
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, stop.set)
        await asyncio.sleep(0.5)  # let the compositor pick up the virtual keyboard
        watcher = asyncio.create_task(self.watch_loop())
        log.info("macropad daemon running")
        await stop.wait()
        watcher.cancel()
        server.close()
        self.disconnect()
        self.ui.close()
        try:
            C.SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(_amain())


async def _amain():
    await Daemon().main()


if __name__ == "__main__":
    main()
