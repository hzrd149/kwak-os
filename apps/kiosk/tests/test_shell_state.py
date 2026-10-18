import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from kiosk import EventStream, Runtime
from shell_state import Activity

ADDRESS = "35129:" + "a" * 64 + ":game"


class ActivityTests(unittest.TestCase):
    def test_round_trip_and_ordering(self):
        with tempfile.TemporaryDirectory() as root:
            now = [100]
            path = Path(root) / "state/activity.json"
            activity = Activity(path, clock=lambda: now[0])
            activity.record_launch(ADDRESS)
            now[0] = 200
            activity.record_launch("35129:" + "b" * 64 + ":other")
            activity.toggle_favorite(ADDRESS)
            self.assertTrue(activity.add_to_collection("  Couch   co-op ", ADDRESS))
            self.assertTrue(activity.seen([ADDRESS]))
            self.assertFalse(activity.seen([ADDRESS]))
            activity.save()
            loaded = Activity(path)
            self.assertEqual(loaded.recent(), ["35129:" + "b" * 64 + ":other", ADDRESS])
            self.assertEqual(loaded.favorites(), [ADDRESS])
            self.assertEqual(loaded.collections_for(ADDRESS), ["Couch co-op"])
            self.assertEqual(loaded.games[ADDRESS]["launches"], 1)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_corrupt_or_unexpected_files_start_empty(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "activity.json"
            path.write_text("{not json")
            self.assertEqual(Activity(path).games, {})
            path.write_text(json.dumps({"games": {ADDRESS: {"last_played": "soon", "favorite": 1, "extra": 2}},
                                        "collections": {"x": "not a list"}}))
            activity = Activity(path)
            self.assertEqual(activity.games, {ADDRESS: {}})
            self.assertEqual(activity.collections, {})

    def test_empty_collection_is_removed(self):
        with tempfile.TemporaryDirectory() as root:
            activity = Activity(Path(root) / "a.json")
            activity.add_to_collection("RPG", ADDRESS)
            activity.remove_from_collection("RPG", ADDRESS)
            self.assertEqual(activity.collections, {})
            self.assertFalse(activity.add_to_collection("   ", ADDRESS))


class RuntimeArgumentTests(unittest.TestCase):
    def runtime(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs["timeout"]))
            return subprocess.CompletedProcess(args, 0, '{"items":[]}', "")
        return Runtime(run), calls

    def test_discover_filters_and_timeouts(self):
        runtime, calls = self.runtime()
        runtime.discover(query="space", archetype="game", compatible=True, offset=48, limit=48)
        runtime.discover(compatible=False, refresh=True)
        self.assertEqual(calls[0][0][2:], ["discover", "--offset", "48", "--limit", "48",
                                           "--query", "space", "--archetype", "game", "--compatible"])
        self.assertEqual(calls[0][1], 8)
        self.assertIn("--compatible=false", calls[1][0])
        self.assertEqual(calls[1][1], 40)

    def test_history_icon_and_permission_arguments(self):
        runtime, calls = self.runtime()
        runtime.icon(ADDRESS)
        runtime.versions(ADDRESS)
        runtime.select(ADDRESS, "e" * 64)
        runtime.set_permission(ADDRESS, "notify", "deny")
        runtime.clear_permission(ADDRESS, "dispatch", subject="share")
        self.assertEqual([call[0][2:] for call in calls], [
            ["icon", ADDRESS], ["versions", ADDRESS], ["select", ADDRESS, "e" * 64],
            ["permissions", "set", ADDRESS, "notify", "deny"],
            ["permissions", "clear", ADDRESS, "dispatch", "--subject", "share"]])
        self.assertEqual([call[1] for call in calls], [30, 8, 190, 8, 8])

    def test_event_stream_parses_lines_and_terminates_child(self):
        class Process:
            terminated = False
            stdout = iter(['{"type":"window.opened","address":"x"}\n', "garbage\n", '{"no":"type"}\n'])
            def poll(self):
                return None
            def terminate(self):
                self.terminated = True
            def wait(self, timeout):
                return 0
        process = Process()
        commands = []
        stream = Runtime().events(["window.opened"], spawn=lambda command, **kw: commands.append(command) or process)
        self.assertEqual(list(stream), [{"type": "window.opened", "address": "x"}])
        stream.close()
        self.assertTrue(process.terminated)
        self.assertEqual(commands[0][1:], ["--json", "events", "--type", "window.opened"])

    def test_event_stream_spawn_failure_is_runtime_error(self):
        def spawn(*args, **kwargs):
            raise OSError()
        with self.assertRaises(RuntimeError):
            EventStream(["kwak"], spawn)


if __name__ == "__main__":
    unittest.main()
