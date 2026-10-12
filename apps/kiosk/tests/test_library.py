import unittest
from library import Library

ADDRESS = "35129:" + "a" * 64 + ":game"


class Runtime:
    def __init__(self):
        self.games = []
        self.calls = []
        self.offline = False
        self.result = "ok"

    def listing(self, command):
        if self.offline:
            raise RuntimeError("Daemon unavailable")
        return self.games

    def request(self, *args):
        self.calls.append(args)
        if args[0] == "diagnostics":
            return {"install_sync": {"enabled": True, "last_result": self.result}}
        self.games = [{"address": ADDRESS}] if args[0] == "install" else []
        return {}


class LibraryTests(unittest.TestCase):
    def test_changes_use_native_install_and_uninstall(self):
        runtime = Runtime()
        library = Library(runtime)
        self.assertEqual(library.change(ADDRESS, "add")["addresses"], [ADDRESS])
        self.assertEqual(library.change(ADDRESS, "remove")["addresses"], [])
        self.assertIn(("install", ADDRESS), runtime.calls)
        self.assertIn(("uninstall", "--yes", ADDRESS), runtime.calls)
        self.assertTrue(all(call[0] in ("install", "uninstall", "diagnostics") for call in runtime.calls))

    def test_daemon_failure_preserves_last_observed_library(self):
        runtime = Runtime()
        library = Library(runtime)
        library.change(ADDRESS, "add")
        runtime.offline = True
        result = library.sync()
        self.assertEqual(result["addresses"], [ADDRESS])
        self.assertEqual(result["error"], "Daemon unavailable")

    def test_initial_failure_is_not_empty_library(self):
        runtime = Runtime()
        runtime.offline = True
        self.assertFalse(Library(runtime).sync()["loaded"])

    def test_sync_failure_does_not_hide_installed_games(self):
        runtime = Runtime()
        runtime.games = [{"address": ADDRESS}]
        runtime.result = "unreadable"
        result = Library(runtime).sync()
        self.assertEqual(result["addresses"], [ADDRESS])
        self.assertIn("could not read", result["error"])
        self.assertEqual(result["pending"], [])

    def test_first_native_sync_does_not_look_like_an_empty_library(self):
        runtime = Runtime()
        runtime.result = None
        result = Library(runtime).sync()
        self.assertFalse(result["loaded"])
        runtime.games = [{"address": ADDRESS}]
        self.assertTrue(Library(runtime).sync()["loaded"])

    def test_snapshot_and_overlapping_refresh_never_wait_for_daemon(self):
        runtime = Runtime()
        library = Library(runtime)
        library.change(ADDRESS, "add")
        calls = list(runtime.calls)
        with library.lock:
            self.assertEqual(library.snapshot()["addresses"], [ADDRESS])
            self.assertEqual(library.sync()["addresses"], [ADDRESS])
        self.assertEqual(runtime.calls, calls)
