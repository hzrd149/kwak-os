"""Controller and terminal UI tests, run headless without a live desktop session."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "kwak_settings", Path(__file__).resolve().parents[1] / "settings.py"
)
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


class Desktop:
    def __init__(self, mode="kwassik"):
        self.mode = mode
        self.calls = []
        self.replies = {}
        self.ignore_apply = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        expression = args[2]
        if expression in self.replies:
            return subprocess.CompletedProcess(args, 0, self.replies[expression], "")
        if args[1] == "repl":
            output = self.mode
        else:
            if not self.ignore_apply:
                self.mode = expression.split('"')[1]
            output = "ok"
        return subprocess.CompletedProcess(args, 0, output + "\n", "")


class ModeControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.preference = Path(self.directory.name) / "kwak" / "tiling-mode"
        self.desktop = Desktop()
        self.controller = settings.ModeController(self.preference, run=self.desktop)

    def save_prior(self, mode="kwassik"):
        self.preference.parent.mkdir(parents=True, exist_ok=True)
        self.preference.write_text(mode + "\n")

    def test_each_mode_is_applied_verified_and_saved(self):
        for mode in settings.MODES:
            with self.subTest(mode=mode):
                self.assertEqual(self.controller.apply(mode), mode)
                self.assertEqual(self.desktop.mode, mode)
                self.assertEqual(self.preference.read_text(), mode + "\n")
                self.assertEqual(self.desktop.calls[-1][1], "repl")
                self.assertEqual(self.preference.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(self.preference.parent.glob(".tiling-mode-*")), [])

    def test_untrusted_mode_never_reaches_ipc_or_disk(self):
        with self.assertRaisesRegex(settings.SettingsError, "supported"):
            self.controller.apply('master"); os.execute("bad")')
        self.assertEqual(self.desktop.calls, [])
        self.assertFalse(self.preference.exists())

    def test_unavailable_live_contract_never_mutates(self):
        self.desktop.mode = "nil"
        with self.assertRaisesRegex(settings.SettingsError, "unavailable"):
            self.controller.apply("master")
        self.assertEqual(len(self.desktop.calls), 1)
        self.assertFalse(self.preference.exists())

    def test_invalid_saved_mode_is_preserved_without_live_change(self):
        self.save_prior("corrupt")
        with self.assertRaisesRegex(settings.SettingsError, "saved window mode.*invalid"):
            self.controller.apply("master")
        self.assertEqual(self.desktop.mode, "kwassik")
        self.assertEqual(self.preference.read_text(), "corrupt\n")
        self.assertEqual(len(self.desktop.calls), 1)

    def test_error_text_with_success_exit_code_does_not_save(self):
        self.save_prior()
        self.desktop.replies['kwak_settings.apply("scrolling")'] = "plugin unavailable"
        with self.assertRaisesRegex(settings.SettingsError, "plugin unavailable.*Restored Kwassik"):
            self.controller.apply("scrolling")
        self.assertEqual(self.preference.read_text(), "kwassik\n")
        self.assertEqual(self.desktop.mode, "kwassik")

    def test_ack_without_actual_mode_change_does_not_save(self):
        self.save_prior()
        self.desktop.ignore_apply = True
        with self.assertRaisesRegex(settings.SettingsError, "still using Kwassik"):
            self.controller.apply("master")
        self.assertEqual(self.preference.read_text(), "kwassik\n")

    def test_atomic_replace_failure_preserves_file_and_rolls_back(self):
        self.save_prior()
        with patch.object(settings.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(settings.SettingsError, "disk full.*Restored Kwassik"):
                self.controller.apply("master")
        self.assertEqual(self.preference.read_text(), "kwassik\n")
        self.assertEqual(self.desktop.mode, "kwassik")
        self.assertEqual(list(self.preference.parent.glob(".tiling-mode-*")), [])

    def test_fsync_failure_preserves_file_and_rolls_back(self):
        self.save_prior()
        with patch.object(settings.os, "fsync", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(settings.SettingsError, "write failed.*Restored Kwassik"):
                self.controller.apply("dwindle")
        self.assertEqual(self.preference.read_text(), "kwassik\n")
        self.assertEqual(self.desktop.mode, "kwassik")
        self.assertEqual(list(self.preference.parent.glob(".tiling-mode-*")), [])

    def test_failed_rollback_is_reported_truthfully(self):
        self.save_prior()
        self.desktop.replies['kwak_settings.apply("kwassik")'] = "rollback unavailable"
        with patch.object(settings.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(settings.SettingsError, "Restoring Kwassik also failed"):
                self.controller.apply("master")
        self.assertEqual(self.desktop.mode, "master")
        self.assertEqual(self.preference.read_text(), "kwassik\n")

    def test_ipc_nonzero_exit_is_not_a_mode(self):
        self.controller.run = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "not connected")
        with self.assertRaisesRegex(settings.SettingsError, "not connected"):
            self.controller.current_mode()

    def test_ipc_timeout_is_visible_and_does_not_write(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("hyprctl", 5)

        self.controller.run = timeout
        with self.assertRaisesRegex(settings.SettingsError, "Cannot reach the desktop"):
            self.controller.apply("master")
        self.assertFalse(self.preference.exists())

    def test_missing_hyprctl_is_visible(self):
        self.controller.run = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("hyprctl"))
        with self.assertRaisesRegex(settings.SettingsError, "Cannot reach the desktop"):
            self.controller.current_mode()


class SettingsAppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.desktop = Desktop("master")
        controller = settings.ModeController(
            Path(self.directory.name) / "tiling-mode", run=self.desktop
        )
        self.app = settings.SettingsApp(controller)

    async def test_choosing_a_mode_applies_it(self):
        async with self.app.run_test(size=(60, 20)) as pilot:
            await pilot.app.workers.wait_for_complete()
            modes = pilot.app.query_one("#modes")
            self.assertEqual(modes.highlighted, list(settings.MODES).index("master"))
            await pilot.press("down", "enter")
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(self.desktop.mode, "dwindle")
            self.assertIn("Dwindle is active", str(pilot.app.query_one("#status").render()))

    async def test_unavailable_desktop_is_shown(self):
        self.desktop.replies["return kwak_settings.mode"] = "nil"
        async with self.app.run_test(size=(40, 12)) as pilot:
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertTrue(pilot.app.query_one("#modes").disabled)
            self.assertTrue(pilot.app.query_one("#status").has_class("error"))


if __name__ == "__main__":
    unittest.main()
