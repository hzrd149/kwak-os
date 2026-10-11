import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from overlay import OverlayError
from screenshots import ScreenshotStore

GAME = "35129:" + "a" * 64 + ":game"
OTHER = "35129:" + "b" * 64 + ":other"


def png(width=2, height=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\x00\xff\x00' * width) * height)) + chunk(b'IEND', b'')


class Desktop:
    target = {'napplet_address': GAME}
    def __init__(self):
        self.calls = []
        self.available = True
        self.game = {'at': [20, 40], 'size': [640, 480]}
    def game_available(self): return self.available
    def live_target(self, game_only=False): return self.game
    def resume(self): self.calls.append(('focus-game',))
    def request(self, *args, **kwargs):
        self.calls.append(args)
        if 'activewindow' in args:
            return {'class': 'org.kwak.Overlay', 'address': '0x999', 'workspace': {'name': '2'}}
        return 'ok'


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'screenshots'
        self.desktop = Desktop()
        self.published = []
        self.grim_calls = []
        def run(args, **kwargs):
            self.grim_calls.append(args)
            Path(args[-1]).write_bytes(png())
            return subprocess.CompletedProcess(args, 0, '', '')
        def request(op, **fields):
            self.published.append((op, fields))
            return {'id': 'event', 'relays': ['wss://relay']}
        self.store = ScreenshotStore(self.root, run, request)
        self.sleep = patch('screenshots.time.sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_capture_restores_overlay_and_persists_publish_contract(self):
        snapshot = self.store.capture(self.desktop)
        self.assertEqual((snapshot['width'], snapshot['height'], snapshot['game']), (2, 2, GAME))
        self.assertEqual(self.grim_calls[0][:3], ['grim', '-g', '20,40 640x480'])
        self.assertEqual(Path(snapshot['path']).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        hidden = ('eval', 'hl.dispatch(hl.dsp.window.move({window="address:0x999",workspace="special:kwak-capture",follow=false}))')
        restored = ('eval', 'hl.dispatch(hl.dsp.window.move({window="address:0x999",workspace="2",follow=false}))')
        focused = ('eval', 'hl.dispatch(hl.dsp.focus({window="address:0x999"}))')
        self.assertLess(self.desktop.calls.index(hidden), self.desktop.calls.index(('focus-game',)))
        self.assertEqual(self.desktop.calls[-2:], [restored, focused])
        self.assertEqual(ScreenshotStore(self.root).latest(GAME), snapshot)
        self.assertIsNone(self.store.latest(OTHER))
        self.store.publish(snapshot, 'Caption')
        self.assertEqual(self.published, [('publish_screenshot', {'filename': snapshot['filename'], 'caption': 'Caption', 'game': GAME})])

    def test_failed_grim_restores_overlay_and_removes_partial_file(self):
        def failed(args, **kwargs):
            Path(args[-1]).write_bytes(png())
            return subprocess.CompletedProcess(args, 1, '', 'private')
        self.store.run = failed
        with self.assertRaisesRegex(OverlayError, 'retry'):
            self.store.capture(self.desktop)
        self.assertEqual(list(self.root.glob('*.png')), [])
        self.assertEqual(self.desktop.calls[-1], ('eval', 'hl.dispatch(hl.dsp.focus({window="address:0x999"}))'))
        self.assertIn(('eval', 'hl.dispatch(hl.dsp.window.move({window="address:0x999",workspace="2",follow=false}))'), self.desktop.calls)

    def test_invalid_image_never_becomes_saved_snapshot(self):
        for data in (b'not PNG', png(0, 2)):
            def invalid(args, **kwargs):
                Path(args[-1]).write_bytes(data)
                return subprocess.CompletedProcess(args, 0, '', '')
            self.store.run = invalid
            with self.assertRaises(OverlayError): self.store.capture(self.desktop)
            self.assertEqual(list(self.root.glob('*.png')), [])
            self.assertIsNone(self.store.latest(GAME))

    def test_no_current_game_never_hides_menu_or_runs_grim(self):
        self.desktop.available = False
        with self.assertRaises(OverlayError): self.store.capture(self.desktop)
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.grim_calls, [])
        self.assertFalse(self.root.exists())

    def test_publish_rejects_other_game_outside_path_and_symlink(self):
        snapshot = self.store.capture(self.desktop)
        for altered in ({**snapshot, 'game': OTHER}, {**snapshot, 'path': '/tmp/outside.png'},
                        {**snapshot, 'filename': '../outside.png'}):
            with self.assertRaises(OverlayError): self.store.publish(altered)
        image = Path(snapshot['path'])
        outside = Path(self.directory.name) / 'outside.png'
        image.rename(outside)
        image.symlink_to(outside)
        self.assertIsNone(self.store.latest(GAME))
        with self.assertRaises(OverlayError): self.store.publish(snapshot)
        self.assertEqual(self.published, [])

    def test_symlinked_root_or_parent_cannot_capture(self):
        elsewhere = Path(self.directory.name) / 'elsewhere'
        elsewhere.mkdir()
        link = Path(self.directory.name) / 'link'
        link.symlink_to(elsewhere, target_is_directory=True)
        for root in (link, link / 'screenshots'):
            with self.assertRaises(OverlayError): ScreenshotStore(root, self.store.run).capture(self.desktop)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_malformed_metadata_is_unavailable(self):
        self.root.mkdir()
        metadata = self.root / 'latest.json'
        for content in ('broken', '[]', 'null', '{}'):
            metadata.write_text(content)
            self.assertIsNone(self.store.latest(GAME))

    def test_publish_failure_keeps_saved_screenshot(self):
        snapshot = self.store.capture(self.desktop)
        def failed(*args, **kwargs): raise RuntimeError('private')
        self.store.request = failed
        with self.assertRaisesRegex(OverlayError, 'saved'):
            self.store.publish(snapshot)
        self.assertTrue(Path(snapshot['path']).is_file())

    def test_missing_media_server_shows_actionable_settings_message(self):
        snapshot = self.store.capture(self.desktop)
        def missing(*args, **kwargs):
            raise RuntimeError('Add a media server in Settings before publishing screenshots.')
        self.store.request = missing
        with self.assertRaisesRegex(OverlayError, 'media server in Settings'):
            self.store.publish(snapshot)
        self.assertTrue(Path(snapshot['path']).exists())
