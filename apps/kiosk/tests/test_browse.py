import base64
import tempfile
import unittest
from pathlib import Path

import browse
from icons import IconStore, PNG_SIGNATURE
from shell_state import Activity

SHA = "c" * 64


def game(name, created=0, **extra):
    return dict(address=f"35129:{'a' * 64}:{name.lower()}", name=name, available=True,
                version={"event_id": "e" * 64, "created_at": created}, **extra)


class LibraryRuleTests(unittest.TestCase):
    def setUp(self):
        now = [1]
        self.activity = Activity(False, clock=lambda: now[0])
        self.now = now
        self.games = [game("Bravo", 30), game("alpha", 10), game("Charlie", 20, update_available={"event_id": "f" * 64})]
        self.installed = {item["address"]: item for item in self.games[:2]}

    def names(self, rows):
        return [row["name"] for row in rows]

    def test_sorts_fall_back_to_name(self):
        self.now[0] = 5
        self.activity.record_launch(self.games[2]["address"])
        self.now[0] = 9
        self.activity.record_launch(self.games[0]["address"])
        rows = lambda sort: self.names(browse.library_rows(self.games, self.installed, self.activity, sort=sort))
        self.assertEqual(rows("name"), ["alpha", "Bravo", "Charlie"])
        self.assertEqual(rows("recent"), ["Bravo", "Charlie", "alpha"])
        self.assertEqual(rows("updated"), ["Bravo", "Charlie", "alpha"])
        self.assertEqual(rows("installed"), ["alpha", "Bravo", "Charlie"])

    def test_filters_and_query(self):
        self.activity.toggle_favorite(self.games[1]["address"])
        self.activity.add_to_collection("Party", self.games[2]["address"])
        rows = lambda show, query="": self.names(browse.library_rows(self.games, self.installed, self.activity,
                                                                      show=show, query=query))
        self.assertEqual(rows("favorites"), ["alpha"])
        self.assertEqual(rows("updates"), ["Charlie"])
        self.assertEqual(rows("installed"), ["alpha", "Bravo"])
        self.assertEqual(rows("collection:Party"), ["Charlie"])
        self.assertEqual(rows("all", "BRA"), ["Bravo"])
        self.assertEqual(rows("all", "nothing"), [])
        options = browse.show_options(self.activity.collections)
        self.assertEqual(browse.cycle(options, "updates"), "collection:Party")
        self.assertEqual(browse.cycle(options, "collection:Party"), "all")

    def test_home_shelves_order_and_omit_empty(self):
        self.activity.record_launch(self.games[0]["address"])
        self.activity.toggle_favorite(self.games[1]["address"])
        shelves = browse.library_shelves(self.games, self.installed, {self.games[2]["address"]: [{}]}, self.activity)
        self.assertEqual([title for title, _ in shelves],
                         ["Continue playing", "Recently played", "Favourites", "Updates available", "All games"])
        self.assertEqual(self.names(shelves[-1][1]), ["alpha", "Bravo", "Charlie"])

    def test_store_shelves_newest_trial_and_categories(self):
        catalog = [game("Old", 1, archetypes=["game", "puzzle"]), game("New", 9, archetypes=["game"]),
                   game("Broken", 5, missing_domains=["gamepad"], archetypes=["game"])]
        installed = {catalog[1]["address"]: catalog[1]}
        shelves = dict(browse.store_shelves(catalog, installed))
        self.assertEqual(self.names(shelves["New & updated"]), ["New", "Old"])
        self.assertEqual(self.names(shelves["Try instantly"]), ["Old"])
        self.assertEqual(self.names(shelves["Game"]), ["New", "Old", "Broken"])
        self.assertEqual(list(shelves), ["New & updated", "Try instantly", "Game", "Puzzle"])


class Runtime:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0
    def icon(self, address):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class IconStoreTests(unittest.IsolatedAsyncioTestCase):
    GAME = dict(address="35129:" + "a" * 64 + ":x", icon={"sha256": SHA, "mime": "image/png"})

    async def test_fetches_once_and_writes_owner_only(self):
        data = PNG_SIGNATURE + b"rest"
        runtime = Runtime({"sha256": SHA, "data": base64.b64encode(data).decode()})
        with tempfile.TemporaryDirectory() as root:
            store = IconStore(runtime, root)
            path = await store.load(self.GAME)
            self.assertEqual(path, Path(root) / f"{SHA}.png")
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(await store.load(self.GAME), path)
            self.assertEqual(runtime.calls, 1)
            self.assertEqual([item.name for item in Path(root).iterdir()], [path.name])

    async def test_rejects_bad_data_and_backs_off_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            bad = Runtime({"sha256": "d" * 64, "data": base64.b64encode(PNG_SIGNATURE).decode()})
            store = IconStore(bad, root)
            self.assertIsNone(await store.load(self.GAME))
            self.assertIsNone(await store.load(self.GAME))
            self.assertEqual(bad.calls, 1)
            now = [0]
            offline = Runtime(error=RuntimeError("Unavailable"))
            store = IconStore(offline, root, clock=lambda: now[0])
            self.assertIsNone(await store.load(self.GAME))
            self.assertIsNone(await store.load(self.GAME))
            now[0] = 301
            self.assertIsNone(await store.load(self.GAME))
            self.assertEqual(offline.calls, 2)

    async def test_games_without_icon_hash_never_call_runtime(self):
        runtime = Runtime()
        store = IconStore(runtime, "/nonexistent")
        self.assertIsNone(await store.load({"address": "x", "icon": {"sha256": "../../etc"}}))
        self.assertEqual(runtime.calls, 0)


if __name__ == "__main__":
    unittest.main()
