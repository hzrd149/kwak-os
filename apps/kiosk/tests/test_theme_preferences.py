"""Shared theme persistence and embedded settings continuity across applications."""
import tempfile
import unittest
from unittest.mock import patch

import settings
from kiosk import Kiosk
from overlay import GameOverlay
from textual.widgets import Input


class Runtime:
    def listing(self, *args):
        return []
    def request(self, *args):
        return {'health': {'ready': True}}


class Desktop:
    target = {'title': 'Test game', 'napplet_address': 'test'}
    def game_available(self):
        return False


class Screenshots:
    def latest(self, *args):
        return None


class ThemeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(settings.os.environ, {'XDG_CONFIG_HOME': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        for name, kwargs in (
            ('settings.account_request', {'side_effect': settings.SettingsError('Offline')}),
            ('settings.ModeController.current_mode', {'return_value': 'kwassik'}),
            ('settings.AudioController.volume', {'return_value': (50, False)}),
            ('settings.NetworkController.status', {'return_value': 'Offline'}),
            ('settings.NetworkController.scan', {'return_value': []}),
        ):
            mock = patch(name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    async def test_launcher_preserves_theme_in_settings_and_restores_saved_choice(self):
        settings.save_theme_preference('textual-light')
        app = Kiosk(Runtime())
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            self.assertEqual(app.theme, 'textual-light')
            app.theme = 'textual-dark'
            app.query_one('#search', Input).value = 'arcade'
            await pilot.click('#settings')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app.theme, 'textual-dark')
            screen = app.screen
            screen.query_one(settings.TabbedContent).active = 'appearance'
            await pilot.pause()
            choices = screen.query_one('#themes')
            choices.highlighted = screen.theme_names.index('textual-light')
            choices.focus()
            await pilot.press('enter')
            await app.workers.wait_for_complete()
            self.assertEqual(app.theme, 'textual-light')
            await pilot.press('escape')
            await pilot.pause()
            self.assertEqual(app.query_one('#search', Input).value, 'arcade')
            self.assertEqual(app.theme, 'textual-light')
        restarted = Kiosk(Runtime())
        async with restarted.run_test(size=(88, 33)):
            await restarted.workers.wait_for_complete()
            self.assertEqual(restarted.theme, 'textual-light')

    async def test_overlay_restores_same_user_theme(self):
        settings.save_theme_preference('textual-light')
        overlay = GameOverlay(desktop=Desktop(), screenshots=Screenshots())
        async with overlay.run_test(size=(80, 28)):
            await overlay.workers.wait_for_complete()
            self.assertEqual(overlay.theme, 'textual-light')
