import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

from overlay import GameOverlay, PublishScreenshot
from test_overlay import FakeDesktop, TARGET
from overlay_control import launch
from terminal_font import synchronize


class PersistentOverlayTests(unittest.IsolatedAsyncioTestCase):
    async def test_hide_reopen_new_game_and_cleanup(self):
        desktop = FakeDesktop()
        desktop.hide_overlay = lambda: desktop.calls.append('hide')
        desktop.show_overlay = lambda: desktop.calls.append('show')
        class Screenshots:
            def latest(self, game): return None
        with tempfile.TemporaryDirectory() as directory:
            endpoint = str(Path(directory) / 'overlay.sock')
            with patch.dict(os.environ, {'KWAK_OVERLAY_SOCKET': endpoint}):
                app = GameOverlay(desktop, Screenshots())
                async with app.run_test(size=(52, 28)) as pilot:
                    await pilot.pause()
                    self.assertTrue(Path(endpoint).exists())
                    app.push_screen(PublishScreenshot({'filename': 'old-game.png'}))
                    await pilot.pause()
                    # The listener sleeps on the socket, not a repeating poll.
                    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                        client.sendto(json.dumps({'hide': True}).encode(), endpoint)
                        for _ in range(50):
                            if app.hidden: break
                            await asyncio.sleep(0.01)
                        self.assertTrue(app.hidden)
                        self.assertEqual(desktop.calls, ['resume', 'hide'])
                        self.assertTrue(app.is_running)
                        next_target = {**TARGET, 'title': 'Second game'}
                        client.sendto(json.dumps({'target': next_target}).encode(), endpoint)
                        for _ in range(50):
                            if 'show' in desktop.calls: break
                            await asyncio.sleep(0.01)
                        self.assertEqual(desktop.target, next_target)
                        self.assertEqual(len(app.screen_stack), 1)
                        self.assertEqual(app.focused.id, 'resume')
                        self.assertIn('Second game', str(app.query_one('#current').render()))
                        self.assertFalse(app.hidden)
                self.assertFalse(Path(endpoint).exists())

    async def test_controls_fit_small_terminal_and_scroll_at_larger_font(self):
        for size in ((52, 28), (80, 28), (88, 33), (120, 40), (36, 20)):
            app = GameOverlay(FakeDesktop())
            async with app.run_test(size=size) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                resume = app.query_one('#resume')
                self.assertGreater(resume.content_region.height, 0)
                session = app.query_one('#session')
                session.focus()
                await pilot.pause()
                self.assertGreater(session.content_region.height, 0)
                self.assertLessEqual(session.region.bottom, app.size.height - 1)
                self.assertGreaterEqual(session.region.y, 0)


class ColdLaunchTests(unittest.TestCase):
    def test_cold_launch_uses_library_font_and_captures_before_spawn(self):
        desktop = FakeDesktop()
        desktop.capture = Mock(return_value=TARGET)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}):
            Path(directory, 'kwak-library-font').write_text('24.5')
            def spawn(args, **kwargs):
                desktop.capture.assert_called_once()
                self.assertIn('font_size=24.5', args)
                self.assertEqual(json.loads(kwargs['env']['KWAK_OVERLAY_TARGET']), TARGET)
                Path(directory, 'kwak-overlay.sock').touch()
                return Mock()
            with patch('overlay_control.subprocess.Popen', side_effect=spawn):
                self.assertEqual(launch('/kitty', '/config', desktop), 0)


class FontWatcherTests(unittest.TestCase):
    def test_library_resize_publishes_size_and_overlay_focus_inherits_it(self):
        boss = Mock()
        window = types.SimpleNamespace(os_window_id=4)
        kitty = types.ModuleType('kitty')
        data = types.ModuleType('kitty.fast_data_types')
        data.os_window_font_size = Mock(return_value=26.5)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'kitty': kitty, 'kitty.fast_data_types': data}), patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}):
            with patch.dict(os.environ):
                os.environ['KWAK_TERMINAL_ROLE'] = 'library'
                synchronize(boss, window, {})
            self.assertEqual(Path(directory, 'kwak-library-font').read_text(), '26.5')
            data.os_window_font_size.return_value = 18
            with patch.dict(os.environ, {'KWAK_TERMINAL_ROLE': 'overlay'}):
                synchronize(boss, window, {'focused': True})
            boss.change_font_size.assert_called_once_with(True, None, 26.5)
            boss.reset_mock()
            Path(directory, 'kwak-library-font').write_text('nan')
            with patch.dict(os.environ, {'KWAK_TERMINAL_ROLE': 'overlay'}):
                synchronize(boss, window, {})
            boss.change_font_size.assert_not_called()
