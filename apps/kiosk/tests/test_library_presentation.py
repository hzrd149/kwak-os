"""Big Picture installed cover grid, dedicated details, and browse continuity."""
import unittest
from unittest.mock import patch

from PIL import Image
from discovery import Discovery, GameCard
from game_details import GameDetails
from kiosk import Kiosk
from textual.widgets import Input, Static
from textual.worker import WorkerCancelled
from test_launcher import LauncherRuntime, ADDRESS, GAME


class LibraryPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def settle(self, app, pilot):
        for _ in range(3):
            try:
                await app.workers.wait_for_complete()
            except WorkerCancelled:
                pass
            await pilot.pause()

    async def test_installed_grid_click_opens_details_without_launch(self):
        for size in ((80, 28), (88, 33), (120, 40)):
            app = Kiosk(LauncherRuntime())
            async with app.run_test(size=size) as pilot:
                await self.settle(app, pilot)
                browser = app.query_one(Discovery)
                self.assertIsInstance(app.focused, GameCard)
                self.assertTrue(browser.display)
                self.assertFalse(app.query_one('#games').display)
                self.assertFalse(app.query_one('#game-panel').display)
                self.assertGreaterEqual(browser.region.width, size[0] - 2)
                card = browser.cards[0]
                self.assertIn('Running', card.status)
                self.assertIn('Update available', card.status)
                await pilot.click(card)
                await self.settle(app, pilot)
                self.assertIsInstance(app.screen, GameDetails)
                self.assertEqual(str(app.screen.query_one('#store-title', Static).content), 'First Game')
                self.assertIn('My Games', str(app.screen.query_one('#store-back').label))
                self.assertFalse(app.runtime.calls)
                await pilot.press('escape')
                await self.settle(app, pilot)
                self.assertEqual(app.selected()['address'], ADDRESS)

    async def test_search_enter_focuses_selected_cover_after_membership_rebuild(self):
        app = Kiosk(LauncherRuntime())
        async with app.run_test(size=(80, 28)) as pilot:
            await self.settle(app, pilot)
            search = app.query_one('#search', Input)
            search.focus()
            search.value = 'Second'
            await pilot.press('enter')
            await self.settle(app, pilot)
            self.assertIsInstance(app.focused, GameCard)
            self.assertEqual(app.focused.game['name'], 'Second Game')
            self.assertFalse(app.runtime.calls)

    async def test_return_restores_search_selected_cover_and_scroll(self):
        games = [dict(GAME, address=f'35129:{"a" * 64}:game-{index}',
                      name=f'Game {index:02d}') for index in range(20)]
        class ManyRuntime(LauncherRuntime):
            def listing(self, command, *args):
                return games
            def details(self, address):
                return next(game for game in games if game['address'] == address)
        app = Kiosk(ManyRuntime())
        async with app.run_test(size=(80, 28)) as pilot:
            await self.settle(app, pilot)
            search = app.query_one('#search', Input)
            search.focus()
            search.value = 'Game'
            await self.settle(app, pilot)
            await pilot.press('enter', 'end')
            await pilot.pause()
            browser = app.query_one(Discovery)
            address = app.selected()['address']
            scroll = browser.scroll_y
            self.assertGreater(scroll, 0)
            await pilot.press('enter')
            await self.settle(app, pilot)
            self.assertIsInstance(app.screen, GameDetails)
            await pilot.press('escape')
            await self.settle(app, pilot)
            self.assertEqual(search.value, 'Game')
            self.assertEqual(app.selected()['address'], address)
            self.assertEqual(browser.scroll_y, scroll)
            self.assertIsInstance(app.focused, GameCard)
            self.assertFalse(app.runtime.calls)

    async def test_grid_artwork_is_not_duplicated_by_hidden_legacy_panel(self):
        class ArtworkRuntime(LauncherRuntime):
            def listing(self, command, *args):
                return [dict(GAME, image='https://example.org/cover.png')]
            def details(self, address):
                return {}
        with patch('images.ImageCache.load', return_value=Image.new('RGB', (32, 16))) as load:
            app = Kiosk(ArtworkRuntime())
            async with app.run_test(size=(80, 28)) as pilot:
                await self.settle(app, pilot)
                card = app.query_one(GameCard)
                image = card.query_one('.card-art')
                if image._load_task:
                    await image._load_task
                await pilot.pause()
                self.assertEqual(image.state, 'ready')
                previous = len(load.call_args_list)
                for _ in range(10):
                    app.update_selection()
                    app.render_games()
                await self.settle(app, pilot)
                self.assertEqual(len(load.call_args_list), previous)
                load.assert_called_once_with('https://example.org/cover.png')
