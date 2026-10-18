import json
import subprocess
import unittest
from types import SimpleNamespace

import gamepad
from gamepad import (ABS_HAT0X, ABS_X, ABS_Y, ABS_Z, BTN_EAST, BTN_MODE, BTN_NORTH, BTN_SOUTH,
                     EV_ABS, EV_KEY, FocusGate, GamepadInput, GamepadReader, Normalizer, is_gamepad)

AXIS = SimpleNamespace(min=-32768, max=32767)
TRIGGER = SimpleNamespace(min=0, max=255)
PAD = {EV_KEY: [BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_MODE],
       EV_ABS: [(ABS_X, AXIS), (ABS_Y, AXIS), (ABS_Z, TRIGGER), (ABS_HAT0X, SimpleNamespace(min=-1, max=1))]}


class Gate:
    def __init__(self, state="self"):
        self.state = state
    def owns(self):
        return self.state == "self"
    def follow(self, stop):
        pass


class Device:
    def __init__(self, capabilities, vendor=0):
        self.caps = capabilities
        self.info = SimpleNamespace(vendor=vendor)
        self.closed = False
    def capabilities(self):
        return self.caps
    def close(self):
        self.closed = True


def reader(gate=None, devices=None, **kwargs):
    received = []
    devices = devices if devices is not None else {"/dev/input/event3": Device(PAD)}
    def open_device(path):
        device = devices[path]
        if isinstance(device, Exception):
            raise device
        return device
    result = GamepadReader(received.append, gate or Gate(), list_devices=lambda: list(devices),
                           open_device=open_device, clock=lambda: 0, **kwargs)
    result.scan()
    return result, received


class DeviceFilterTests(unittest.TestCase):
    def test_pads_accepted_keyboards_and_card_readers_rejected(self):
        self.assertTrue(is_gamepad(PAD))
        self.assertFalse(is_gamepad({EV_KEY: [BTN_SOUTH, 30], EV_ABS: [(ABS_X, AXIS)]}))
        self.assertFalse(is_gamepad({EV_KEY: list(range(1, 60))}))
        self.assertFalse(is_gamepad({EV_KEY: [BTN_SOUTH]}))

    def test_scan_skips_non_pads_reports_permission_and_handles_removal(self):
        keyboard = Device({EV_KEY: list(range(16, 51))})
        devices = {"/dev/input/event1": keyboard, "/dev/input/event2": PermissionError()}
        result, _ = reader(devices=devices)
        self.assertEqual(result.devices, {})
        self.assertTrue(keyboard.closed)
        self.assertEqual(result.problem, "Controller found but not accessible")
        pad = Device(PAD)
        devices["/dev/input/event3"] = pad
        result.scan()
        self.assertIn("/dev/input/event3", result.devices)
        self.assertEqual(result.problem, "")
        result.process("/dev/input/event3", [(EV_KEY, BTN_SOUTH, 1)])
        del devices["/dev/input/event3"]
        result.scan()
        self.assertTrue(pad.closed)
        self.assertEqual(result.pressed, set())


class NormalizerTests(unittest.TestCase):
    def test_stick_deadzone_hysteresis_and_shared_sources(self):
        normalizer = Normalizer(PAD)
        self.assertEqual(normalizer.feed(EV_ABS, ABS_X, 15000), [])
        self.assertEqual(normalizer.feed(EV_ABS, ABS_X, 30000), [("right", True)])
        self.assertEqual(normalizer.feed(EV_ABS, ABS_X, 18000), [])  # Above release.
        self.assertEqual(normalizer.feed(EV_ABS, ABS_HAT0X, 1), [])  # Already held by stick.
        self.assertEqual(normalizer.feed(EV_ABS, ABS_X, 0), [])
        self.assertEqual(normalizer.feed(EV_ABS, ABS_HAT0X, 0), [("right", False)])
        self.assertEqual(normalizer.feed(EV_ABS, ABS_Y, -32768), [("up", True)])
        self.assertEqual(normalizer.feed(EV_ABS, ABS_Z, 200), [("l2", True)])
        self.assertEqual(normalizer.feed(EV_KEY, BTN_SOUTH, 2), [])  # Kernel autorepeat.

    def test_vendor_face_button_override(self):
        self.assertEqual(Normalizer(PAD).feed(EV_KEY, BTN_NORTH, 1), [("y", True)])
        self.assertEqual(Normalizer(PAD, 0x045e).feed(EV_KEY, BTN_NORTH, 1), [("x", True)])


class ReaderTests(unittest.TestCase):
    PATH = "/dev/input/event3"

    def test_repeat_only_directions(self):
        result, received = reader()
        result.process(self.PATH, [(EV_ABS, ABS_HAT0X, 1), (EV_KEY, BTN_SOUTH, 1)], now=0)
        result.tick(0.3)
        result.tick(0.39)
        result.tick(0.52)
        result.process(self.PATH, [(EV_ABS, ABS_HAT0X, 0)], now=0.6)
        result.tick(1.0)
        self.assertEqual(received, [GamepadInput("right"), GamepadInput("a"),
                                    GamepadInput("right", True), GamepadInput("right", True)])

    def test_inputs_dropped_without_focus_and_held_buttons_blocked_on_return(self):
        gate = Gate("other")
        result, received = reader(gate)
        result.process(self.PATH, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(received, [])
        gate.state = "self"
        result.tick(1)
        result.process(self.PATH, [(EV_ABS, ABS_HAT0X, 1)])
        self.assertEqual(received, [GamepadInput("right")])
        result.process(self.PATH, [(EV_KEY, BTN_SOUTH, 0), (EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(received[-1], GamepadInput("a"))

    def test_guide_policy(self):
        opened = []
        gate = Gate("other")
        result, received = reader(gate, on_guide_elsewhere=lambda: opened.append(True))
        result.process(self.PATH, [(EV_KEY, BTN_MODE, 1), (EV_KEY, BTN_MODE, 0)], now=10)
        result.process(self.PATH, [(EV_KEY, BTN_MODE, 1), (EV_KEY, BTN_MODE, 0)], now=10.5)
        self.assertEqual(opened, [True])  # Debounced.
        gate.state = "overlay"
        result.process(self.PATH, [(EV_KEY, BTN_MODE, 1), (EV_KEY, BTN_MODE, 0)], now=20)
        self.assertEqual(opened, [True])
        gate.state = "self"
        result.process(self.PATH, [(EV_KEY, BTN_MODE, 1)], now=30)
        self.assertEqual(received, [GamepadInput("guide")])

    def test_closed_interface_stops_reader(self):
        def closed(event):
            raise RuntimeError("closed")
        result = GamepadReader(closed, Gate(), list_devices=lambda: [self.PATH],
                               open_device=lambda path: Device(PAD), clock=lambda: 0)
        result.scan()
        result.process(self.PATH, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertTrue(result.stop_event.is_set())


class FocusGateTests(unittest.TestCase):
    def gate(self, window, environ=None, returncode=0):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, returncode, json.dumps(window), "")
        return FocusGate(run=run, environ=environ or {}, pid=4242)

    def test_classifies_owner_overlay_and_games(self):
        gate = self.gate({"pid": 4242, "class": "org.kwak.Library"})
        self.assertEqual(gate.refresh(), "self")
        gate = self.gate({"pid": 99, "class": "org.kwak.Overlay"})
        self.assertEqual(gate.refresh(), "overlay")
        gate = self.gate({"pid": 99, "class": "kwaklet"})
        self.assertEqual(gate.refresh(), "other")
        self.assertFalse(gate.owns())

    def test_unknown_without_compositor_unless_ungated(self):
        gate = self.gate({}, returncode=1)
        self.assertEqual(gate.refresh(), "unknown")
        self.assertFalse(gate.owns())
        gate = self.gate({}, environ={"KWAK_GAMEPAD_UNGATED": "1"}, returncode=1)
        self.assertEqual(gate.refresh(), "self")

    def test_follow_refreshes_on_active_window_events(self):
        import threading
        windows = iter([{"pid": 1, "class": "a"}, {"pid": 4242, "class": "b"}])
        stop = threading.Event()
        gate = FocusGate(run=lambda args, **kw: subprocess.CompletedProcess(args, 0, json.dumps(next(windows)), ""),
                         environ={"XDG_RUNTIME_DIR": "/run", "HYPRLAND_INSTANCE_SIGNATURE": "sig"}, pid=4242,
                         connect=lambda path: Stream(["workspace>>2\n", "activewindowv2>>abc\n"], stop))
        gate.follow(stop)
        self.assertEqual(gate.state, "self")


class Stream(list):
    def __init__(self, lines, stop):
        super().__init__(lines)
        self.stop = stop
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.stop.set()
    def __iter__(self):
        return iter(list.__iter__(self))


class OverlayLaunchTests(unittest.TestCase):
    def test_open_overlay_detaches_and_ignores_missing_command(self):
        calls = []
        gamepad.open_overlay(lambda command, **kw: calls.append((command, kw["start_new_session"])))
        self.assertEqual(calls, [(["kwak-overlay"], True)])
        def missing(*args, **kwargs):
            raise OSError()
        gamepad.open_overlay(missing)


if __name__ == "__main__":
    unittest.main()
