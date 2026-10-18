import json
import subprocess
import unittest
from unittest.mock import patch

from overlay import DesktopActions, GameOverlay, OverlayError
from textual.widgets import Button, Input, OptionList

TOKEN = "a" * 32
NAP = "35129:" + "b" * 64 + ":game"
IDENTITY = {"exe": "/nix/store/test/bin/kwaklet", "start": "123"}
TARGET = {"address": "0x123", "pid": 123, "class": "kwaklet", "title": "Game",
          "process": IDENTITY, "window_id": TOKEN, "napplet_address": NAP}


class DesktopActionTests(unittest.TestCase):
    def desktop(self, target=None, clients=None, windows=None):
        calls = []
        class Runtime:
            def request(self, *args):
                calls.append(args)
                return {"closed": True} if args[0] == "stop" else {"items": windows if windows is not None else [{"window_id": TOKEN, "address": NAP}]}
        def run(args, **kwargs):
            calls.append(args)
            output = json.dumps(clients if clients is not None else [TARGET]) if "clients" in args else "ok"
            return subprocess.CompletedProcess(args, 0, output, "")
        desktop = DesktopActions(target or dict(TARGET), run=run, runtime=Runtime())
        desktop.process_identity = lambda pid: IDENTITY
        return desktop, calls

    def test_launch_identity_gate_does_not_wait_for_daemon(self):
        desktop, calls = self.desktop()
        self.assertTrue(desktop.launch_available())
        self.assertFalse(any(call[0] == "windows" for call in calls))
        desktop.target = {**TARGET, "window_id": "unverified"}
        self.assertFalse(desktop.launch_available())
        desktop.target = {**TARGET, "process": {**IDENTITY, "start": "old"}}
        self.assertFalse(desktop.launch_available())

    def test_exit_stops_only_exact_runtime_window(self):
        desktop, calls = self.desktop()
        desktop.exit_game()
        self.assertIn(("stop", TOKEN), calls)
        self.assertFalse(any("closewindow" in call or "kill" in call for call in calls))

    def test_reused_pid_changed_identity_or_missing_window_never_stops(self):
        for target, clients, windows in (
                ({**TARGET, "process": {**IDENTITY, "start": "old"}}, None, None),
                (TARGET, [], None), (TARGET, None, []),
                ({**TARGET, "window_id": "other"}, None, None),
                ({**TARGET, "napplet_address": "other"}, None, None)):
            desktop, calls = self.desktop(target, clients, windows)
            with self.assertRaises(OverlayError):
                desktop.exit_game()
            self.assertFalse(any(call[0] == "stop" for call in calls))

    def test_non_game_and_foreign_process_never_stop(self):
        desktop, calls = self.desktop()
        desktop.process_identity = lambda pid: {"exe": "/bin/kitty", "start": "123"}
        self.assertFalse(desktop.game_available())
        desktop.process_identity = lambda pid: None
        self.assertFalse(desktop.game_available())
        self.assertFalse(any(call[0] == "stop" for call in calls))

    def test_untracked_trial_can_capture_and_close_only_its_exact_window(self):
        desktop, calls = self.desktop({**TARGET, "window_id": "1", "runtime_managed": False}, windows=[])
        self.assertTrue(desktop.game_available())
        desktop.exit_game()
        self.assertIn(["hyprctl", "eval", 'hl.dispatch(hl.dsp.window.close({window="address:0x123"}))'], calls)
        self.assertFalse(any(call[0] in ("windows", "stop") for call in calls))
        desktop.process_identity = lambda pid: {**IDENTITY, "start": "new"}
        with self.assertRaises(OverlayError):
            desktop.exit_game()

    def test_unknown_instance_does_not_fall_back_to_compositor_close(self):
        desktop, calls = self.desktop({**TARGET, "window_id": "unknown", "runtime_managed": False})
        self.assertFalse(desktop.game_available())
        with self.assertRaises(OverlayError):
            desktop.exit_game()
        self.assertFalse(any("window.close" in str(call) for call in calls))

    def test_resume_restores_captured_window_focus(self):
        desktop, calls = self.desktop()
        desktop.resume()
        self.assertIn(["hyprctl", "eval", 'hl.dispatch(hl.dsp.focus({window="address:0x123"}))'], calls)

    def test_back_library_selects_workspace_and_existing_window(self):
        desktop, calls = self.desktop(clients=[{"address": "0x456", "class": "org.kwak.Library"}])
        desktop.library()
        self.assertIn(["hyprctl", "eval", "hl.dispatch(hl.dsp.focus({workspace=1}))"], calls)
        self.assertIn(["hyprctl", "eval", 'hl.dispatch(hl.dsp.focus({window="address:0x456"}))'], calls)


class FakeDesktop:
    target = {"title": "Green Game"}
    def __init__(self, available=True):
        self.available = available
        self.calls = []
    def game_available(self): return self.available
    def launch_available(self): return self.available
    def resume(self): self.calls.append("resume")
    def exit_game(self): self.calls.append("exit-game")
    def library(self): self.calls.append("back-library")
    def switch_games(self): self.calls.append("switch-games")
    def open_app(self, command): self.calls.append(command)


class OverlayAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_has_no_catalog_and_focuses_resume(self):
        desktop = FakeDesktop()
        app = GameOverlay(desktop)
        async with app.run_test(size=(72, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(len(app.query(Input)), 0)
            self.assertEqual(len(app.query(OptionList)), 0)
            self.assertEqual({button.id for button in app.query(Button)},
                             {"resume", "exit-game", "screenshot", "publish-screenshot", "back-library", "switch-games", "settings", "session"})
            self.assertEqual(app.focused.id, "resume")
            self.assertFalse(app.query_one("#exit-game").disabled)
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            self.assertEqual(desktop.calls, ["resume"])

    async def test_exit_disabled_without_game(self):
        app = GameOverlay(FakeDesktop(False))
        async with app.run_test(size=(72, 30)):
            await app.workers.wait_for_complete()
            self.assertTrue(app.query_one("#exit-game").disabled)

    async def test_buttons_dispatch_actions_and_close_menu(self):
        for action, expected in (("exit-game", "exit-game"), ("back-library", "back-library"),
                                 ("switch-games", "switch-games"), ("settings", "kwak-settings"),
                                 ("session", "kwak-session")):
            desktop = FakeDesktop()
            app = GameOverlay(desktop)
            async with app.run_test(size=(72, 30)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertTrue(await pilot.click("#" + action))
                await app.workers.wait_for_complete()
                self.assertEqual(desktop.calls, [expected])

    async def test_failed_action_keeps_menu_for_retry(self):
        desktop = FakeDesktop()
        def failed(): raise OverlayError("Retry this action")
        desktop.exit_game = failed
        app = GameOverlay(desktop)
        async with app.run_test(size=(72, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#exit-game")
            await app.workers.wait_for_complete()
            self.assertIn("Retry", str(app.query_one("#menu-status").render()))
            self.assertFalse(app.query_one("#resume").disabled)

class OverlayCaptureTests(unittest.TestCase):
    def test_launcher_ignores_non_game_before_starting_terminal(self):
        import tempfile
        from overlay_control import launch
        desktop = FakeDesktop(False)
        desktop.capture = lambda: {"class": "org.kwak.Library"}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch("overlay_control.subprocess.Popen") as spawn:
            self.assertEqual(launch("/kitty", "/config", desktop), 0)
            spawn.assert_not_called()

    def test_warm_launch_sends_fresh_target_without_starting_terminal(self):
        import tempfile
        import socket
        from pathlib import Path
        from overlay_control import launch
        desktop = FakeDesktop()
        desktop.capture = lambda: TARGET
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch("overlay_control.subprocess.Popen") as spawn, \
                socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(str(Path(directory) / "kwak-overlay.sock"))
            server.settimeout(1)
            self.assertEqual(launch("/kitty", "/config", desktop), 0)
            self.assertEqual(json.loads(server.recv(16384)), {"target": TARGET})
            spawn.assert_not_called()

    def test_toggle_from_overlay_sends_hide_without_recapturing_game(self):
        import tempfile
        import socket
        from pathlib import Path
        from overlay_control import launch
        desktop = FakeDesktop(False)
        desktop.capture = lambda: {"class": "org.kwak.Overlay"}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(str(Path(directory) / "kwak-overlay.sock"))
            server.settimeout(1)
            self.assertEqual(launch("/kitty", "/config", desktop), 0)
            self.assertEqual(json.loads(server.recv(16384)), {"hide": True})

    def test_capture_accepts_real_nul_delimited_trial_context_without_rpc(self):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps(TARGET), "")
        class Runtime:
            def request(self, *args):
                raise AssertionError("Local trial capture must not need daemon windows")
        desktop = DesktopActions(run=run, runtime=Runtime())
        desktop.process_identity = lambda pid: IDENTITY
        environment = bytes([0]).join((b"OTHER=first", b"KWAKORE_INSTANCE_ID=1",
                                       b"KWAKORE_NAPP_ID=" + NAP.encode(), b"OTHER_LAST=value", b""))
        with patch("overlay.Path.read_bytes", return_value=environment):
            captured = desktop.capture()
        self.assertEqual(captured["window_id"], "1")
        self.assertEqual(captured["napplet_address"], NAP)
        self.assertFalse(captured["runtime_managed"])

    def test_capture_maps_process_environment_to_exact_window(self):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps(TARGET), "")
        class Runtime:
            def request(self, *args):
                return {"items": [{"window_id": TOKEN, "address": NAP}]}
        desktop = DesktopActions(run=run, runtime=Runtime())
        desktop.process_identity = lambda pid: IDENTITY
        environment = ("KWAKORE_INSTANCE_ID=" + TOKEN + "\0KWAKORE_NAPP_ID=" + NAP + "\0").encode()
        with patch("overlay.Path.read_bytes", return_value=environment):
            captured = desktop.capture()
        self.assertEqual(captured["window_id"], TOKEN)
        self.assertEqual(captured["napplet_address"], NAP)
        self.assertEqual(captured["process"], IDENTITY)

class ScreenshotMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_stays_open_and_publication_requires_confirmation(self):
        from textual.widgets import TextArea
        desktop = FakeDesktop()
        desktop.target = {'title': 'Game', 'napplet_address': NAP}
        class Screenshots:
            current = None
            calls = []
            def latest(self, game): return self.current if game == NAP else None
            def capture(self, selected_desktop):
                self.calls.append(('capture',))
                self.current = {'filename': 'shot.png', 'path': '/saved/shot.png', 'game': NAP}
                return self.current
            def publish(self, snapshot, caption):
                self.calls.append(('publish', snapshot['game'], caption))
                return {'id': 'event'}
        screenshots = Screenshots()
        app = GameOverlay(desktop, screenshots)
        async with app.run_test(size=(72, 36)) as pilot:
            await app.workers.wait_for_complete()
            self.assertTrue(app.query_one('#publish-screenshot').disabled)
            await pilot.click('#screenshot')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertFalse(app.query_one('#publish-screenshot').disabled)
            self.assertEqual(screenshots.calls, [('capture',)])
            await pilot.click('#publish-screenshot')
            await pilot.pause()
            self.assertEqual(app.focused.id, 'cancel-publish')
            self.assertIn('shot.png', str(app.screen.query_one('#screenshot-info').render()))
            await pilot.press('escape')
            await pilot.pause()
            self.assertEqual(screenshots.calls, [('capture',)])
            await pilot.click('#publish-screenshot')
            await pilot.pause()
            app.screen.query_one('#caption', TextArea).text = 'A green victory'
            await pilot.click('#confirm-publish')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(screenshots.calls[-1], ('publish', NAP, 'A green victory'))
            self.assertEqual(len(app.screen_stack), 1)
            self.assertIn('published', str(app.query_one('#menu-status').render()))
            self.assertEqual(desktop.calls, [])
