import unittest

from discovery import GameCard
from game_details import GameDetails
from hints import ButtonHints
from kiosk import Kiosk
from navigation import ConfirmPress, GamepadPressed
from osk import OnScreenKeyboard
from textual.widgets import Button, Input

GAMES = [dict(address="35129:" + "a" * 64 + f":g{index}", name=f"Game {index}", available=True)
         for index in range(6)]


class Runtime:
    def __init__(self):
        self.calls = []
    def listing(self, command, *args):
        return GAMES
    def details(self, address):
        return {}
    def request(self, *args):
        self.calls.append(args)
        return {"items": []}


class FakeReader:
    stopped = False
    def stop(self):
        self.stopped = True


async def settle(pilot):
    """Workers start further workers (card rebuilds); wait for all of them."""
    for _ in range(5):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


async def press(pilot, *buttons):
    for button in buttons:
        pilot.app.post_message(GamepadPressed(button))
        await pilot.pause()


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_grid_edges_tabs_details_and_back(self):
        for size in ((80, 28), (110, 36)):
            reader = FakeReader()
            app = Kiosk(Runtime(), gamepad=reader)
            async with app.run_test(size=size) as pilot:
                await settle(pilot)
                self.assertIsInstance(app.focused, GameCard)
                first = app.focused
                await press(pilot, "right")
                self.assertEqual(app.focused.game["name"], "Game 1")
                await press(pilot, "left", "up")
                self.assertEqual(app.focused.parent.id, "library-toolbar")  # Left the shelves.
                await press(pilot, "up")
                self.assertIsInstance(app.focused, Button)
                self.assertEqual(app.focused.parent.id, "nav")
                await press(pilot, "down", "down")
                self.assertIs(app.focused, first)  # The top shelf, where it was left.
                await press(pilot, "right", "b")
                self.assertEqual(app.focused.id, "library")
                await press(pilot, "b")
                self.assertEqual(app.focused.game["name"], "Game 1")
                await press(pilot, "left")
                for page, view in (("installed", "all"), ("discover", "home"), ("discover", "browse")):
                    await press(pilot, "r1")
                    await settle(pilot)
                    self.assertEqual((app.page, app.current_view()), (page, view))
                    self.assertIsInstance(app.focused, GameCard)
                await press(pilot, "r1")
                await settle(pilot)
                self.assertEqual((app.page, app.current_view()), ("installed", "home"))
                await press(pilot, "l1")
                await settle(pilot)
                self.assertEqual((app.page, app.current_view()), ("discover", "browse"))
                await press(pilot, "r1")
                await settle(pilot)
                await press(pilot, "a")
                await pilot.pause()
                self.assertIsInstance(app.screen, GameDetails)
                self.assertTrue(app.screen.query_one(ButtonHints).display)
                await press(pilot, "r1")
                self.assertEqual(app.screen.detail_tab, "releases")
                await press(pilot, "b")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, GameDetails)
                self.assertEqual(app.runtime.calls, [])
            self.assertTrue(reader.stopped)

    async def test_hints_follow_input_mode(self):
        app = Kiosk(Runtime(), gamepad=FakeReader())
        async with app.run_test(size=(80, 28)) as pilot:
            await settle(pilot)
            hints = app.query_one(ButtonHints)
            self.assertFalse(hints.display)
            await press(pilot, "right")
            self.assertTrue(hints.display)
            self.assertIn("[A] Details", str(hints.content))
            await pilot.press("left")
            self.assertFalse(hints.display)

    async def test_on_screen_keyboard_filters_live(self):
        app = Kiosk(Runtime(), gamepad=FakeReader())
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            await press(pilot, "y")
            self.assertIsInstance(app.screen, OnScreenKeyboard)
            keyboard = app.screen
            keyboard.query_one("#osk-key-53").press()
            await pilot.pause()
            search = app.query_one("#search", Input)
            self.assertEqual(search.value, "5")
            await press(pilot, "x", "y")
            self.assertEqual(search.value, " ")
            await press(pilot, "x")
            keyboard.query_one("#osk-key-53").press()
            await press(pilot, "b")
            self.assertNotIsInstance(app.screen, OnScreenKeyboard)
            self.assertEqual(search.value, "5")
            await press(pilot, "a")
            self.assertIsInstance(app.screen, OnScreenKeyboard)
            await press(pilot, "menu")
            await pilot.pause()
            self.assertIsInstance(app.focused, GameCard)
            self.assertEqual(app.focused.game["name"], "Game 5")

    async def test_guide_returns_home_then_opens_menu(self):
        from unittest.mock import patch
        app = Kiosk(Runtime(), gamepad=FakeReader())
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            app.query_one(GameCard).focus()
            await pilot.pause()
            await press(pilot, "a")
            self.assertIsInstance(app.screen, GameDetails)
            with patch("gamepad.open_overlay") as overlay:
                await press(pilot, "guide")
                self.assertEqual(app.focused.id, "library")
                overlay.assert_not_called()
                await press(pilot, "guide")
                overlay.assert_called_once()


class ConfirmPressTests(unittest.TestCase):
    def test_second_press_within_window(self):
        now = [0]
        confirm = ConfirmPress(clock=lambda: now[0])
        self.assertFalse(confirm.confirm("remove"))
        self.assertFalse(confirm.confirm("other"))
        self.assertTrue(confirm.confirm("other"))
        self.assertFalse(confirm.confirm("remove"))
        now[0] = 5
        self.assertFalse(confirm.confirm("remove"))


if __name__ == "__main__":
    unittest.main()
