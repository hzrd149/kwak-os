import threading
import unittest
from kiosk import Kiosk
from discovery import Discovery, GameCard
from textual.widgets import Input, OptionList, Static


GAMES = [dict(address=f'35129:{"a" * 64}:game-{i}', name=f'Game {i}', available=True) for i in range(12)]


class Runtime:
    def listing(self, command, *args):
        return list(GAMES)
    def request(self, *args):
        return {'items': []}


class AutomaticRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_interval_recovers_without_refresh_control(self):
        class Offline(Runtime):
            offline = True
            def listing(self, *args):
                if self.offline:
                    raise RuntimeError('Games unavailable')
                return super().listing(*args)
        runtime = Offline()
        app = Kiosk(runtime)
        app.CATALOG_INTERVAL = 0.2
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            self.assertFalse(app.query('#refresh'))
            self.assertEqual(app.games, [])
            runtime.offline = False
            await pilot.pause(0.5)
            await app.workers.wait_for_complete()
            self.assertEqual(len(app.games), len(GAMES))
            self.assertEqual(str(app.query_one('#status', Static).content), '')

    async def test_quiet_refresh_preserves_cards_focus_search_and_scroll(self):
        started, release = threading.Event(), threading.Event()
        class Slow(Runtime):
            slow = False
            calls = 0
            def listing(self, *args):
                if self.slow:
                    self.calls += 1
                    started.set()
                    release.wait(3)
                return super().listing(*args)
        runtime = Slow()
        app = Kiosk(runtime)
        try:
            async with app.run_test(size=(80, 28)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.click('#store')
                await app.workers.wait_for_complete()
                await pilot.pause()
                browser = app.query_one(Discovery)
                cards = list(browser.query(GameCard))
                cards[5].focus()
                await pilot.pause()
                app.query_one('#status', Static).update('Game opened.')
                original_scroll = browser.scroll_y
                original_selection = app.selected()['address']
                runtime.slow = True
                app.refresh_background()
                await asyncio_wait(started)
                app.refresh_background()
                self.assertEqual(str(app.query_one('#status', Static).content), 'Game opened.')
                release.set()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(runtime.calls, 1)
                self.assertIs(app.focused, cards[5])
                self.assertIs(browser.query(GameCard)[5], cards[5])
                self.assertEqual(app.selected()['address'], original_selection)
                self.assertEqual(browser.scroll_y, original_scroll)
                self.assertEqual(app.query_one('#search', Input).value, '')
                self.assertEqual(str(app.query_one('#status', Static).content), 'Game opened.')
        finally:
            release.set()


async def asyncio_wait(event):
    import asyncio
    await asyncio.to_thread(event.wait, 2)
