import unittest

from discovery import Discovery, GameCard
from game_details import GameDetails
from kiosk import Kiosk
from textual.widgets import Button, Input, Static


GAME = dict(address='35129:' + 'a' * 64 + ':one', name='First game', description='An adventure.', author_name='Creator', available=True)
OTHER = dict(GAME, address='35129:' + 'b' * 64 + ':two', name='Second game')


class Runtime:
    def __init__(self):
        self.calls = []
        self.installed = []
    def listing(self, command, *args):
        return self.installed if command == 'installed' else [GAME, OTHER]
    def details(self, address):
        return GAME if address == GAME['address'] else OTHER
    def request(self, *args):
        self.calls.append(args)
        if args[0] == 'install':
            self.installed = [GAME]
        return {'items': []}


class DetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_separate_queries_details_install_play_and_back(self):
        runtime = Runtime()
        app = Kiosk(runtime)
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            library_search = app.query_one('#search', Input)
            library_search.value = 'library only'
            await pilot.click('#store')
            await app.workers.wait_for_complete()
            search = app.query_one('#discovery-search', Input)
            search.value = 'First'
            await pilot.pause()
            await app.workers.wait_for_complete()
            self.assertFalse(library_search.display)
            self.assertTrue(search.display)
            self.assertEqual(app.query_one('#nav').region.height, 3)
            browser = app.query_one(Discovery)
            card = app.query_one(GameCard)
            scroll = browser.scroll_y
            await pilot.click(card)
            await app.workers.wait_for_complete()
            await pilot.pause()
            page = app.screen
            self.assertIsInstance(page, GameDetails)
            self.assertEqual(page.query_one('#store-title', Static).content, 'First game')
            self.assertEqual(page.query_one('#store-author', Static).content, 'By Creator')
            self.assertEqual(runtime.calls, [])
            for selector in ('#play', '#install', '#store-back'):
                button = page.query_one(selector, Button)
                self.assertGreaterEqual(button.content_size.width, len(str(button.label)))
                self.assertLessEqual(button.region.bottom, 28)
            await pilot.click('#play')
            await app.workers.wait_for_complete()
            self.assertEqual(runtime.calls, [('try', GAME['address'])])
            await pilot.click('#install')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn(('install', GAME['address']), runtime.calls)
            self.assertTrue(page.query_one('#install', Button).disabled)
            self.assertEqual(str(page.query_one('#play', Button).label), 'Play')
            await pilot.click('#play')
            await app.workers.wait_for_complete()
            self.assertEqual(runtime.calls[-1], ('launch', GAME['address']))
            await pilot.press('escape')
            await pilot.pause()
            self.assertEqual(search.value, 'First')
            self.assertEqual(browser.scroll_y, scroll)
            self.assertIs(app.focused, card)
            await pilot.click('#library')
            await app.workers.wait_for_complete()
            self.assertEqual(library_search.value, 'library only')
            self.assertTrue(library_search.display)
            self.assertFalse(search.display)
            await pilot.click('#store')
            await app.workers.wait_for_complete()
            self.assertEqual(search.value, 'First')

    async def test_detail_failure_keeps_catalog_content_and_retries(self):
        class Offline(Runtime):
            offline = True
            def details(self, address):
                if self.offline:
                    raise RuntimeError('Offline')
                return dict(GAME, description='New description')
        runtime = Offline()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click('#store')
            await app.workers.wait_for_complete()
            await pilot.click(app.query(GameCard)[0])
            await app.workers.wait_for_complete()
            self.assertEqual(app.screen.query_one('#store-description', Static).content, GAME['description'])
            self.assertIn('Retrying', str(app.screen.query_one('#store-feedback', Static).content))
            runtime.offline = False
            app.screen.retry_details()
            await app.workers.wait_for_complete()
            self.assertEqual(app.screen.query_one('#store-description', Static).content, 'New description')
            self.assertEqual(runtime.calls, [])


class PageLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_artwork_and_release_tab_remain_stable_under_refresh(self):
        from unittest.mock import patch
        from PIL import Image
        from images import TerminalImage
        class ArtworkRuntime(Runtime):
            def listing(self, command, *args):
                return [dict(GAME, banner='https://example.test/banner.png')]
            def details(self, address):
                return dict(GAME, banner='https://example.test/banner.png',
                            installed_version={'event_id': 'a' * 64, 'created_at': 1700000000})
        with patch('images.ImageCache.load', return_value=Image.new('RGB', (32, 16))) as load:
            app = Kiosk(ArtworkRuntime())
            async with app.run_test(size=(80, 28)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                await pilot.click(app.query(GameCard)[0])
                await app.workers.wait_for_complete()
                await pilot.pause()
                page = app.screen
                image = page.query_one('#store-art', TerminalImage)
                if image._load_task:
                    await image._load_task
                self.assertEqual(image.state, 'ready')
                self.assertLessEqual(page.query_one('#store-actions').region.bottom,
                                     page.query_one('#store-tabs').region.y)
                page.query_one('#store-releases-tab', Button).focus()
                await pilot.press('enter')
                page.update_page()
                self.assertTrue(page.query_one('#store-release').display)
                self.assertFalse(page.query_one('#store-description').display)
                self.assertEqual(page.detail_tab, 'releases')
                self.assertLess(page.query_one('#store-tabs').region.bottom, 28)
                # Card and details each own a renderer; refresh adds no load.
                before = load.call_count
                for _ in range(10):
                    page.update_page()
                self.assertEqual(load.call_count, before)

    async def test_saved_game_waits_for_download_and_reports_window_service_failure(self):
        runtime = Runtime()
        runtime.installed = [GAME]
        app = Kiosk(runtime)
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.click(app.query(GameCard)[0])
            await app.workers.wait_for_complete()
            page = app.screen
            # The account knows this game while its local download is pending.
            app.library = object()
            app.saved = [GAME['address']]
            app.installed.clear()
            app.windows_available = False
            page.update_page()
            self.assertTrue(page.query_one('#play', Button).disabled)
            self.assertIn('Downloading', str(page.query_one('#store-state', Static).content))
            self.assertIn('Running status unavailable', str(page.query_one('#store-state', Static).content))
            self.assertEqual(str(page.query_one('#play', Button).label), 'Play Now')
            self.assertEqual(runtime.calls, [])
            app.library = None
