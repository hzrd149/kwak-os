import asyncio
import io
import os
import pty
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from textual.app import App
from images import ImageCache, ImageLoadError, TerminalImage, decode_image, _HTTPSRedirects


def png(size=(30, 20), color="green"):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


class LoaderTests(unittest.TestCase):
    def test_invalid_and_byte_limit(self):
        for data in (b"not an image", b"x" * (8 * 1024 * 1024 + 1)):
            with self.assertRaises(ImageLoadError):
                decode_image(data)

    def test_pixel_limit_and_thumbnail(self):
        with self.assertRaises(ImageLoadError):
            decode_image(png((4001, 4000)))
        image = decode_image(png((1000, 500)))
        self.assertEqual(image.size, (512, 256))
        self.assertEqual(image.mode, "RGBA")

    def test_cache_is_hashed_atomic_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ImageCache(directory)
            source = "https://example.test/icon.png?token=secret"
            with patch.object(cache.opener, "open", return_value=io.BytesIO(png())) as fetch:
                self.assertEqual(cache.load(source).size, (30, 20))
                self.assertEqual(cache.load(source).size, (30, 20))
                fetch.assert_called_once()
                self.assertEqual(fetch.call_args.kwargs["timeout"], 10)
            files = list(Path(directory).iterdir())
            self.assertEqual(files, [cache.cache_path(source)])
            self.assertNotIn("secret", files[0].name)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)

    def test_corrupt_cache_is_refetched_and_failed_download_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ImageCache(directory)
            source = "https://example.test/icon"
            cache.cache_path(source).write_bytes(b"bad")
            with patch.object(cache.opener, "open", return_value=io.BytesIO(png())) as fetch:
                cache.load(source)
                fetch.assert_called_once()
            source += "-bad"
            with patch.object(cache.opener, "open", return_value=io.BytesIO(b"bad")):
                with self.assertRaises(ImageLoadError):
                    cache.load(source)
            self.assertFalse(cache.cache_path(source).exists())

    def test_remote_read_deadline_and_https_redirect(self):
        cache = ImageCache()
        with patch("images.time.monotonic", side_effect=[0, 11]):
            with self.assertRaises(ImageLoadError):
                cache._read(io.BytesIO(png()), remote=True)
        with self.assertRaises(ImageLoadError):
            _HTTPSRedirects().redirect_request(None, None, 302, "", {}, "http://example.test/icon")

    def test_local_file_refreshes_without_cache_and_http_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ImageCache(Path(directory) / "cache")
            path = Path(directory) / "icon.png"
            path.write_bytes(png())
            self.assertEqual(cache.load(path).size, (30, 20))
            path.write_bytes(png((5, 7)))
            self.assertEqual(cache.load(path).size, (5, 7))
            self.assertFalse(cache.directory.exists())
            with self.assertRaises(ImageLoadError):
                cache.load("http://example.test/icon")


class WidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_headless_render_loading_failure_and_clear(self):
        class Cache:
            def load(self, source):
                if source == "bad":
                    raise ImageLoadError("bad")
                return decode_image(png())
        widget = TerminalImage("good", cache=Cache(), renderer="halfcell")
        class Demo(App):
            def compose(self):
                yield widget
        async with Demo().run_test() as pilot:
            await pilot.pause()
            self.assertEqual(widget.state, "ready")
            self.assertTrue(widget.query_one(".picture").display)
            widget.set_source("bad")
            self.assertIsNone(widget.query_one(".picture").image)
            await pilot.pause()
            self.assertEqual(widget.state, "error")
            widget.set_source(None)
            self.assertEqual(widget.state, "empty")
            self.assertFalse(widget.query_one(".picture").display)

    async def test_slow_previous_source_cannot_replace_latest(self):
        started = threading.Event()
        release = threading.Event()
        class Cache:
            def load(self, source):
                if source == "slow":
                    started.set()
                    release.wait(3)
                    return decode_image(png((10, 10)))
                return decode_image(png((20, 20)))
        widget = TerminalImage("slow", cache=Cache(), renderer="halfcell")
        class Demo(App):
            def compose(self):
                yield widget
        try:
            async with Demo().run_test() as pilot:
                await asyncio.to_thread(started.wait, 2)
                widget.set_source("fast")
                await pilot.pause()
                self.assertEqual(widget.query_one(".picture").image.size, (20, 20))
                release.set()
                await pilot.pause()
                self.assertEqual(widget.query_one(".picture").image.size, (20, 20))
        finally:
            release.set()


class ImportTests(unittest.TestCase):
    def test_stdout_terminal_with_piped_stdin_does_not_probe(self):
        # Nix builders can give stdout a PTY while stdin is /dev/null.
        master, slave = pty.openpty()
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import images"],
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL, stdout=slave,
                stderr=subprocess.PIPE, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            os.close(slave)
            os.close(master)
