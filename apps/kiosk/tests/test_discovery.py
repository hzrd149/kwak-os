import unittest
from unittest.mock import patch

from discovery import Discovery, GameCard, artwork
from kiosk import Kiosk
from game_details import GameDetails
from textual.widgets import Button, Input, OptionList


GAMES = [dict(address=f"35129:{'a' * 64}:game-{i}", name=f"Game {i}",
              available=True, description=f"Description {i}") for i in range(8)]


class Runtime:
    def __init__(self):
        self.calls = []
    def listing(self, command, *args):
        return GAMES
    def request(self, *args):
        self.calls.append(args)
        return {"items": []}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mouse_keyboard_search_and_responsive_cards(self):
        runtime = Runtime()
        app = Kiosk(runtime)
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click('#store')
            await app.workers.wait_for_complete()
            await pilot.pause()
            browser = app.query_one(Discovery)
            cards = list(browser.query(GameCard))
            self.assertEqual(len(cards), 8)
            self.assertFalse(app.query_one('#games', OptionList).display)
            self.assertEqual(app.query_one('#shell').region.x, 0)
            self.assertGreater(browser.region.width, 45)
            self.assertFalse(app.query_one('#game-panel').display)
            self.assertTrue(cards[0].query_one('.card-icon').display)
            await pilot.click(cards[1])
            await app.workers.wait_for_complete()
            self.assertIsInstance(app.screen, GameDetails)
            self.assertEqual(app.screen.address, GAMES[1]['address'])
            self.assertFalse(any(call[0] in ('try', 'launch') for call in runtime.calls))
            for control in ("play", "install"):
                button = app.screen.query_one(f"#{control}", Button)
                if button.display:
                    self.assertGreaterEqual(button.content_size.width, len(str(button.label)))
                else:
                    self.assertEqual(control, 'install')
                    self.assertTrue(button.disabled)
            await pilot.press('escape')
            await pilot.press('left')
            self.assertEqual(app.selected()['address'], GAMES[0]['address'])
            await pilot.press('down')
            self.assertEqual(app.selected()['address'], GAMES[browser.columns]['address'])
            await pilot.press('enter')
            await app.workers.wait_for_complete()
            self.assertIsInstance(app.screen, GameDetails)
            self.assertEqual(app.screen.address, GAMES[browser.columns]['address'])
            await pilot.click('#play')
            await app.workers.wait_for_complete()
            self.assertIn(('launch', GAMES[browser.columns]['address']), runtime.calls)
            await pilot.press('escape')
            search = app.query_one('#discovery-search', Input)
            search.value = 'Game 7'
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(len(browser.query(GameCard)), 1)
            search.focus()
            await pilot.press('enter')
            self.assertIsInstance(app.focused, GameCard)
            self.assertEqual(app.selected()['address'], GAMES[7]['address'])
            search.value = 'missing'
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(len(browser.query(GameCard)), 0)
            self.assertTrue(browser.query_one('#discovery-empty').display)
            search.value = ''
            await app.workers.wait_for_complete()
            await pilot.resize_terminal(140, 40)
            await pilot.pause()
            self.assertGreaterEqual(browser.columns, 4)
            for card in browser.query(GameCard):
                self.assertLessEqual(card.region.right, browser.region.right)

    async def test_https_artwork_uses_image_widget(self):
        from PIL import Image
        runtime = Runtime()
        game = dict(GAMES[0], image='https://example.test/game.png')
        runtime.listing = lambda *args: [game]
        with patch('images.ImageCache.load', return_value=Image.new('RGB', (32, 32))) as load:
            app = Kiosk(runtime)
            async with app.run_test(size=(100, 32)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.click('#store')
                await app.workers.wait_for_complete()
                await pilot.pause()
                card = app.query_one(GameCard)
                card.load_artwork()
                await pilot.pause()
                self.assertFalse(card.query_one('.card-icon').display)
                self.assertEqual(card.query_one('.card-art').state, 'ready')
                load.assert_called_once_with(game['image'])

    def test_remote_artwork_cannot_read_local_files(self):
        self.assertIsNone(artwork({'image': '/etc/passwd', 'icon': 'http://example.test/icon'}))
        self.assertEqual(artwork({'icon': 'https://example.test/icon.png'}), 'https://example.test/icon.png')
