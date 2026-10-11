import tempfile
import unittest
from library import Library

ADDRESS = "35129:" + "a" * 64 + ":game"
OTHER = "35129:" + "b" * 64 + ":other"


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_offline_intents_survive_restart_without_erasing_base(self):
        def offline(*args, **kwargs):
            raise RuntimeError("Network unavailable")
        library = Library(self.directory.name, offline)
        with library.state() as state:
            state.update(base=[OTHER], loaded=True)
        library.change(ADDRESS, "add")
        snapshot = Library(self.directory.name, offline).sync()
        self.assertEqual(snapshot["addresses"], [ADDRESS, OTHER])
        self.assertEqual(len(snapshot["pending"]), 1)
        self.assertEqual(snapshot["base"], [OTHER])

    def test_remote_merge_removes_only_acknowledged_intent(self):
        library = None
        def request(op, **kwargs):
            if op == "napplet_library":
                return {"addresses": [OTHER], "name": "Player"}
            # A second UI queues a new intent while publication is in flight.
            library.change(OTHER, "remove")
            return {"addresses": [ADDRESS, OTHER]}
        library = Library(self.directory.name, request)
        library.change(ADDRESS, "add")
        snapshot = library.sync()
        self.assertEqual(snapshot["addresses"], [ADDRESS])
        self.assertEqual(snapshot["pending"][0]["action"], "remove")

    def test_failed_initial_fetch_does_not_become_empty_library(self):
        def request(*args, **kwargs):
            raise RuntimeError("Retry")
        library = Library(self.directory.name, request)
        snapshot = library.sync()
        self.assertFalse(snapshot["loaded"])
        self.assertEqual(snapshot["error"], "Retry")

    def test_corrupt_cache_is_not_overwritten(self):
        from pathlib import Path
        path = Path(self.directory.name) / "state.json"
        path.write_text("broken")
        with self.assertRaises(RuntimeError):
            Library(self.directory.name).change(ADDRESS, "add")
        self.assertEqual(path.read_text(), "broken")
