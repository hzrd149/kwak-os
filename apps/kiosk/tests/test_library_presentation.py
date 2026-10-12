"""Big Picture library hierarchy, tab continuity, and bounded artwork loads."""
import unittest
from unittest.mock import patch

from kiosk import Kiosk
from images import TerminalImage
from PIL import Image
from textual.widgets import Button, OptionList
from test_launcher import LauncherRuntime, ADDRESS, GAME, SECOND


class LibraryPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def settle(self, app, pilot):
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()

    async def test_primary_actions_above_tabs_and_tabs_preserve_selection(self):
        for size in ((80, 28), (88, 33), (120, 40)):
            app = Kiosk(LauncherRuntime())
            async with app.run_test(size=size) as pilot:
                await self.settle(app, pilot)
                self.assertFalse(app.query_one('#library-art').display)
                self.assertFalse(app.query_one('#install').display)
                self.assertLessEqual(app.query_one('#actions').region.bottom,
                                     app.query_one('#detail-tabs').region.y)
                self.assertLess(app.query_one('#detail-tabs').region.bottom, size[1])
                self.assertTrue(app.query_one('#detail').display)
                self.assertFalse(app.query_one('#game-version').display)
                await pilot.click('#releases-tab')
                self.assertTrue(app.query_one('#game-version').display)
                self.assertFalse(app.query_one('#detail').display)
                app.render_games()
                self.assertEqual(app.detail_tab, 'releases')
                self.assertEqual(app.selected()['address'], ADDRESS)
                self.assertFalse(app.runtime.calls)
                # Built-in button keyboard activation matches the mouse action.
                app.query_one('#info-tab', Button).focus()
                await pilot.press('enter')
                self.assertEqual(app.detail_tab, 'info')

    async def test_selected_artwork_changes_only_with_source_and_fits_small_terminal(self):
        class ArtworkRuntime(LauncherRuntime):
            def listing(self, command, *args):
                return [dict(GAME, banner='https://example.org/banner.png',
                             image='https://example.org/icon.png'), SECOND]
            def details(self, address):
                return {}

        sources = []
        def record_source(widget, source):
            widget.source = source
            sources.append(source)

        with patch.object(TerminalImage, 'set_source', record_source):
            app = Kiosk(ArtworkRuntime())
            async with app.run_test(size=(80, 28)) as pilot:
                await self.settle(app, pilot)
                self.assertTrue(app.query_one('#library-art').display)
                self.assertEqual(app.query_one('#library-art').source,
                                 'https://example.org/banner.png')
                previous = len(sources)
                for _ in range(10):
                    app.update_selection()
                    app.render_games()
                self.assertEqual(len(sources), previous)
                self.assertLess(app.query_one('#detail-tabs').region.bottom, 28)
                app.query_one('#games', OptionList).highlighted = 1
                await self.settle(app, pilot)
                self.assertFalse(app.query_one('#library-art').display)
                self.assertEqual(sources[-1], None)

    async def test_hero_renders_cached_artwork_at_small_size(self):
        class ArtworkRuntime(LauncherRuntime):
            def listing(self, command, *args):
                return [dict(GAME, banner='https://example.org/banner.png')]
            def details(self, address):
                return {}

        with patch('images.ImageCache.load', return_value=Image.new('RGB', (32, 16))) as load:
            app = Kiosk(ArtworkRuntime())
            async with app.run_test(size=(80, 28)) as pilot:
                await self.settle(app, pilot)
                image = app.query_one('#library-art', TerminalImage)
                if image._load_task:
                    await image._load_task
                await pilot.pause()
                self.assertEqual(image.state, 'ready')
                self.assertTrue(image.display)
                self.assertLess(app.query_one('#detail-tabs').region.bottom, 28)
                load.assert_called_once_with('https://example.org/banner.png')
