"""Controller input for the launcher and game menu, read directly from evdev.

The reader never grabs a device: napplet games keep receiving the same pad
through WebKit. Every input except Guide is therefore gated on this
interface's own window being active in Hyprland, so playing a game never
drives the launcher in the background. Nothing here imports Textual.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import threading
import time

# Linux input-event-codes; stable kernel ABI, so tests need no evdev module.
EV_KEY, EV_ABS = 0x01, 0x03
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE = 0x13a, 0x13b, 0x13c
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
ABS_X, ABS_Y, ABS_Z, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x02, 0x05, 0x10, 0x11
KEY_Q, KEY_M = 16, 50  # The letter block of a keyboard, e.g. the card reader.

DIRECTIONS = ("up", "down", "left", "right")
KEYS = {
    BTN_SOUTH: "a", BTN_EAST: "b", BTN_NORTH: "y", BTN_WEST: "x",
    BTN_TL: "l1", BTN_TR: "r1", BTN_TL2: "l2", BTN_TR2: "r2",
    BTN_SELECT: "view", BTN_START: "menu", BTN_MODE: "guide",
    BTN_DPAD_UP: "up", BTN_DPAD_DOWN: "down", BTN_DPAD_LEFT: "left", BTN_DPAD_RIGHT: "right",
}
# Drivers that report the face buttons by label rather than position.
XY_SWAPPED_VENDORS = {0x045e}
STICK_PRESS, STICK_RELEASE = 0.6, 0.4
FIRST_REPEAT, REPEAT = 0.38, 0.12


@dataclass(frozen=True)
class GamepadInput:
    button: str
    repeat: bool = False


def is_gamepad(capabilities):
    """A real pad: face buttons and a direction source, but no letter keys."""
    keys = set(capabilities.get(EV_KEY, ()))
    axes = {entry[0] if isinstance(entry, tuple) else entry for entry in capabilities.get(EV_ABS, ())}
    if BTN_SOUTH not in keys or any(KEY_Q <= key <= KEY_M for key in keys):
        return False
    return bool(axes & {ABS_X, ABS_HAT0X} or keys & {BTN_DPAD_UP, BTN_DPAD_LEFT})


class Normalizer:
    """Turns one device's raw events into logical button transitions."""

    def __init__(self, capabilities, vendor=0):
        self.ranges = {}
        for entry in capabilities.get(EV_ABS, ()):
            if isinstance(entry, tuple):
                code, info = entry
                self.ranges[code] = (getattr(info, "min", -1), getattr(info, "max", 1))
        self.keys = dict(KEYS)
        if vendor in XY_SWAPPED_VENDORS:
            self.keys[BTN_NORTH], self.keys[BTN_WEST] = "x", "y"
        self.sources = {}

    def scaled(self, code, value):
        low, high = self.ranges.get(code, (-1, 1))
        if high <= low:
            return 0.0
        return max(-1.0, min(1.0, (2 * (value - low) / (high - low)) - 1))

    def hold(self, button, source, held):
        """A button is pressed while any of its sources (D-pad, hat, stick) holds it."""
        sources = self.sources.setdefault(button, set())
        before = bool(sources)
        sources.add(source) if held else sources.discard(source)
        return [(button, bool(sources))] if before != bool(sources) else []

    def feed(self, kind, code, value):
        if kind == EV_KEY and code in self.keys and value in (0, 1):
            return self.hold(self.keys[code], "key", value == 1)
        if kind != EV_ABS:
            return []
        if code in (ABS_HAT0X, ABS_HAT0Y):
            low, high = ("left", "right") if code == ABS_HAT0X else ("up", "down")
            return self.hold(low, "hat", value < 0) + self.hold(high, "hat", value > 0)
        if code in (ABS_X, ABS_Y):
            low, high = ("left", "right") if code == ABS_X else ("up", "down")
            position = self.scaled(code, value)
            changes = []
            for button, magnitude in ((low, -position), (high, position)):
                held = "stick" in self.sources.get(button, ())
                threshold = STICK_RELEASE if held else STICK_PRESS
                changes += self.hold(button, "stick", magnitude > threshold)
            return changes
        if code in (ABS_Z, ABS_RZ):
            # Analog triggers rest at their minimum, unlike sticks.
            low, high = self.ranges.get(code, (0, 255))
            pulled = high > low and (value - low) / (high - low) > 0.5
            return self.hold("l2" if code == ABS_Z else "r2", "axis", pulled)
        return []


class FocusGate:
    """Who owns the controller right now: this window, the menu, or a game."""

    def __init__(self, run=subprocess.run, environ=None, pid=None, connect=None):
        self.run = run
        self.environ = os.environ if environ is None else environ
        self.connect = connect or self.default_connect
        self.owners = self.ancestors(pid or os.getpid())
        self.ungated = self.environ.get("KWAK_GAMEPAD_UNGATED") == "1"
        self.state = "self" if self.ungated else "unknown"
        self.active_class = ""
        self.lock = threading.Lock()

    @staticmethod
    def ancestors(pid):
        """This process and its parents, which include the hosting kitty."""
        owners = set()
        while pid > 1 and pid not in owners:
            owners.add(pid)
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
                pid = int(stat.rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
        return owners

    def classify(self, window):
        if not isinstance(window, dict):
            return "other", ""
        klass = str(window.get("class") or "")
        if window.get("pid") in self.owners:
            return "self", klass
        return ("overlay" if klass == "org.kwak.Overlay" else "other"), klass

    def refresh(self):
        if self.ungated:
            return self.state
        try:
            result = self.run(["hyprctl", "-j", "activewindow"], capture_output=True,
                              text=True, timeout=2, check=False)
            window = json.loads(result.stdout) if not result.returncode else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            window = None
        state, klass = self.classify(window) if window is not None else ("unknown", "")
        with self.lock:
            self.state, self.active_class = state, klass
        return state

    def socket_path(self):
        runtime, signature = self.environ.get("XDG_RUNTIME_DIR"), self.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not runtime or not signature:
            return None
        return Path(runtime) / "hypr" / signature / ".socket2.sock"

    @staticmethod
    def default_connect(path):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(path))
        return client.makefile("r", encoding="utf-8", errors="replace")

    def follow(self, stop):
        """Track active-window changes; polls when the event socket is missing."""
        if self.ungated:
            return
        while not stop.is_set():
            self.refresh()
            path = self.socket_path()
            try:
                if path is None:
                    raise OSError()
                with self.connect(path) as stream:
                    for line in stream:
                        if stop.is_set():
                            return
                        if line.startswith("activewindowv2>>"):
                            self.refresh()
            except OSError:
                stop.wait(0.5)

    def owns(self):
        return self.state == "self"


class GamepadReader:
    """Reads every pad, applies repeat and the held-button guard, and reports.

    `on_input(GamepadInput)` runs on the reader thread; it must only hand the
    input over to the UI loop. `on_guide_elsewhere()` runs when Guide is
    pressed while another window (a game) is active.
    """

    def __init__(self, on_input, gate=None, on_guide_elsewhere=None, list_devices=None,
                 open_device=None, clock=time.monotonic):
        self.on_input = on_input
        self.gate = gate or FocusGate()
        self.on_guide_elsewhere = on_guide_elsewhere
        self.list_devices = list_devices or self.default_list_devices
        self.open_device = open_device or self.default_open_device
        self.clock = clock
        self.devices = {}
        self.normalizers = {}
        self.pressed = set()
        self.blocked = set()
        self.repeat_at = {}
        self.owned = self.gate.owns()
        self.problem = ""
        self.last_guide = 0.0
        self.stop_event = threading.Event()
        self.threads = []

    @staticmethod
    def default_list_devices():
        try:
            return sorted(str(path) for path in Path("/dev/input").glob("event*"))
        except OSError:
            return []

    @staticmethod
    def default_open_device(path):
        import evdev
        return evdev.InputDevice(path)

    def scan(self):
        found = set(self.list_devices())
        for path in list(self.normalizers):
            if path not in found:
                self.drop(path)
        denied = 0
        for path in found - set(self.devices) - set(self.normalizers):
            try:
                device = self.open_device(path)
                capabilities = device.capabilities()
            except PermissionError:
                denied += 1
                continue
            except (OSError, ImportError):
                continue
            if not is_gamepad(capabilities):
                device.close()
                self.normalizers[path] = None  # Not a pad; skip on later scans.
                continue
            self.devices[path] = device
            self.normalizers[path] = Normalizer(capabilities, getattr(getattr(device, "info", None), "vendor", 0))
        self.problem = ("Controller found but not accessible" if denied and not self.devices else "")

    def drop(self, path):
        device = self.devices.pop(path, None)
        self.normalizers.pop(path, None)
        if device is None:
            return
        try:
            device.close()
        except OSError:
            pass
        # A pad unplugged mid-press must not leave its buttons held.
        self.pressed.clear()
        self.repeat_at.clear()

    def process(self, path, events, now=None):
        now = self.clock() if now is None else now
        normalizer = self.normalizers.get(path)
        if normalizer is None:
            return
        for kind, code, value in events:
            for button, pressed in normalizer.feed(kind, code, value):
                self.transition(button, pressed, now)

    def update_gate(self):
        owned = self.gate.owns()
        if owned and not self.owned:
            # Buttons held while focus arrives (e.g. A that launched the menu)
            # act only after they are released and pressed again.
            self.blocked |= self.pressed
            self.repeat_at.clear()
        self.owned = owned

    def transition(self, button, pressed, now):
        if not pressed:
            self.pressed.discard(button)
            self.blocked.discard(button)
            self.repeat_at.pop(button, None)
            return
        self.pressed.add(button)
        self.update_gate()
        if button == "guide":
            self.guide(now)
            return
        if not self.owned or button in self.blocked:
            return
        if button in DIRECTIONS:
            self.repeat_at[button] = now + FIRST_REPEAT
        self.emit(GamepadInput(button))

    def guide(self, now):
        if now - self.last_guide < 1.0:
            return
        self.last_guide = now
        if self.owned:
            self.emit(GamepadInput("guide"))
        elif self.gate.state == "other" and self.on_guide_elsewhere:
            self.on_guide_elsewhere()

    def tick(self, now=None):
        now = self.clock() if now is None else now
        self.update_gate()
        if not self.owned:
            self.repeat_at.clear()
            return
        for button, due in list(self.repeat_at.items()):
            if now >= due:
                self.repeat_at[button] = now + REPEAT
                self.emit(GamepadInput(button, repeat=True))

    def emit(self, event):
        try:
            self.on_input(event)
        except RuntimeError:
            self.stop_event.set()  # The interface has closed.

    def start(self):
        for target in (self.run, lambda: self.gate.follow(self.stop_event)):
            thread = threading.Thread(target=target, daemon=True, name="kwak-gamepad")
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        for path in list(self.devices):
            self.drop(path)

    def run(self):
        next_scan = 0.0
        while not self.stop_event.is_set():
            now = self.clock()
            if now >= next_scan:
                self.scan()
                next_scan = now + 2
            devices = dict(self.devices)
            if not devices:
                self.stop_event.wait(0.5)
                continue
            try:
                ready, _, _ = select.select(list(devices.values()), [], [], 0.05)
            except (OSError, ValueError):
                ready = []
                next_scan = 0.0
            for device in ready:
                path = next((key for key, value in devices.items() if value is device), None)
                try:
                    events = [(event.type, event.code, event.value) for event in device.read()]
                except BlockingIOError:
                    continue
                except OSError:
                    self.drop(path)
                    continue
                self.process(path, events)
            self.tick()


def open_overlay(spawn=subprocess.Popen):
    """Guide over a game opens the menu; its wrapper lock prevents duplicates."""
    try:
        spawn(["kwak-overlay"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass
