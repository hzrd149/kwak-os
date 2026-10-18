import asyncio
import tempfile
import unittest
from pathlib import Path

from discovery import Discovery, GameCard
from game_menu import GameMenu
from kiosk import Kiosk
from navigation import GamepadPressed
from shell_state import Activity
from shelves import ShelfView
from support import settle, show_grid
from textual.widgets import Button, Input

GAMES = [dict(address="35129:" + "a" * 64 + f":g{index}", name=f"Game {index}", available=True,
              archetypes=["game", "puzzle" if index % 2 else "action"],
              version={"event_id": "e" * 64, "created_at": 100 + index}) for index in range(6)]


class Runtime:
    def __init__(self):
        self.calls = []
        self.discover_calls = []
    def listing(self, command, *args):
        return GAMES
    def details(self, address):
        return {}
    def request(self, *args):
        self.calls.append(args)
        return {"items": []}
    def discover(self, **kwargs):
        self.discover_calls.append(kwargs)
        matches = [game for game in GAMES if not kwargs.get("query") or kwargs["query"] in game["name"]]
        if kwargs.get("archetype"):
            matches = [game for game in matches if kwargs["archetype"] in game["archetypes"]]
        offset, limit = kwargs.get("offset", 0), kwargs.get("limit", 48)
        page = matches[offset:offset + limit]
        return {"items": page, "next_offset": offset + limit if offset + limit < len(matches) else None}


class Reader:
    def stop(self):
        pass


async def press(pilot, *buttons):
    for button in buttons:
        pilot.app.post_message(GamepadPressed(button))
        await pilot.pause()


def shelf_titles(app):
    return [shelf.title for shelf in app.query_one(ShelfView).shelves]


class LibraryViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_favourite_collection_and_recent_shelves_persist(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "activity.json"
            app = Kiosk(Runtime(), gamepad=Reader(), activity=Activity(path))
            async with app.run_test(size=(100, 36)) as pilot:
                await settle(pilot)
                self.assertEqual(shelf_titles(app), ["Recently added", "All games"])
                card = app.focused
                self.assertIsInstance(card, GameCard)
                await press(pilot, "x")
                await settle(pilot)
                self.assertIn("Favourites", shelf_titles(app))
                self.assertTrue(app.activity.favorite(card.game["address"]))
                self.assertTrue(any(c.status.startswith("★") for c in app.query(GameCard)))
                await press(pilot, "menu")
                self.assertIsInstance(app.screen, GameMenu)
                app.screen.query_one("#new-collection", Input).value = "Couch"
                app.screen.query_one("#menu-create", Button).press()
                await settle(pilot)
                self.assertNotIsInstance(app.screen, GameMenu)
                self.assertIn("Couch", shelf_titles(app))
                app.perform_game_action("play", GAMES[3])
                await settle(pilot)
                self.assertEqual(shelf_titles(app)[0], "Recently played")
                self.assertIn(("launch", GAMES[3]["address"]), app.runtime.calls)
            saved = Activity(path)
            self.assertTrue(saved.favorite(card.game["address"]))
            self.assertEqual(saved.collections, {"Couch": [card.game["address"]]})
            self.assertEqual(saved.recent(), [GAMES[3]["address"]])

    async def test_all_view_sort_show_and_shelf_navigation(self):
        app = Kiosk(Runtime(), gamepad=Reader())
        async with app.run_test(size=(80, 28)) as pilot:
            await settle(pilot)
            first = app.focused
            await press(pilot, "down")
            self.assertIsNot(app.focused, first)  # Next shelf.
            self.assertEqual(app.focused.parent.parent.title, "All games")
            await show_grid(pilot)
            grid = app.query_one(Discovery)
            self.assertTrue(grid.display)
            self.assertEqual([card.game["name"] for card in grid.cards][:2], ["Game 0", "Game 1"])
            await pilot.click("#library-sort")
            await pilot.click("#library-sort")  # Recently updated.
            await settle(pilot)
            self.assertEqual(str(app.query_one("#library-sort", Button).label), "Sort: Recently updated")
            self.assertEqual(grid.cards[0].game["name"], "Game 5")
            app.activity.toggle_favorite(GAMES[2]["address"])
            await pilot.click("#library-show")
            await pilot.click("#library-show")
            await settle(pilot)
            self.assertEqual(str(app.query_one("#library-show", Button).label), "Show: Favourites")
            self.assertEqual([card.game["name"] for card in grid.cards], ["Game 2"])
            await press(pilot, "view")
            await settle(pilot)
            self.assertEqual(str(app.query_one("#library-sort", Button).label), "Sort: Recently added")


class StoreViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_home_search_categories_and_pages(self):
        runtime = Runtime()
        app = Kiosk(runtime, gamepad=Reader())
        app.SEARCH_DELAY = 0.01
        app.PAGE_SIZE = 2
        async with app.run_test(size=(100, 36)) as pilot:
            await settle(pilot)
            await pilot.click("#store")
            await settle(pilot)
            self.assertEqual(shelf_titles(app), ["New & updated", "Game", "Action", "Puzzle"])
            search = app.query_one("#discovery-search", Input)
            search.value = "Game 1"
            search.value = "Game"
            await asyncio.sleep(0.05)
            await settle(pilot)
            self.assertEqual(app.current_view(), "browse")
            # One debounced query; each later page starts when focus reaches
            # the last row, which two-card pages always are.
            self.assertEqual({call.get("query") for call in runtime.discover_calls}, {"Game"})
            self.assertEqual([call["offset"] for call in runtime.discover_calls], [0, 2, 4])
            grid = app.query_one(Discovery)
            self.assertEqual(len(grid.cards), 6)
            search.value = ""
            await pilot.click("#store-category")
            await asyncio.sleep(0.05)
            await settle(pilot)
            self.assertEqual(runtime.discover_calls[-1]["archetype"], "game")
            await pilot.click("#store-compatible")
            await asyncio.sleep(0.05)
            await settle(pilot)
            self.assertIs(runtime.discover_calls[-1]["compatible"], True)
            await pilot.click("#store-home")
            await settle(pilot)
            self.assertEqual(app.current_view(), "home")
            self.assertIsNone(app.category)
            self.assertEqual(runtime.calls, [])

    async def test_stale_results_never_replace_newer_query(self):
        import threading
        gate = threading.Event()
        class Slow(Runtime):
            def discover(self, **kwargs):
                if kwargs.get("query") == "slow":
                    gate.wait(3)
                return super().discover(**kwargs)
        app = Kiosk(Slow())
        app.SEARCH_DELAY = 0.01
        try:
            async with app.run_test(size=(100, 36)) as pilot:
                await settle(pilot)
                await pilot.click("#store")
                await settle(pilot)
                search = app.query_one("#discovery-search", Input)
                search.value = "slow"
                await asyncio.sleep(0.05)
                search.value = "Game 4"
                await asyncio.sleep(0.05)
                gate.set()
                await settle(pilot)
                self.assertEqual([game["name"] for game in app.search_results], ["Game 4"])
        finally:
            gate.set()


if __name__ == "__main__":
    unittest.main()
