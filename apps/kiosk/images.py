"""Reusable, bounded terminal images. Import before starting Textual for detection."""
import asyncio
import hashlib
import io
import os
from pathlib import Path
import tempfile
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from PIL import Image as PILImage, UnidentifiedImageError
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
# Detection and terminal cell-size queries must happen before App.run().
if (sys.__stdin__ is not None and sys.__stdin__.isatty()
        and sys.__stdout__ is not None and sys.__stdout__.isatty()):
    from textual_image.widget import Image, HalfcellImage
else:
    # Nix checks may have a TTY on stdout alone; protocol probing needs both.
    _terminal_stdout = sys.__stdout__
    try:
        sys.__stdout__ = io.StringIO()
        from textual_image.widget import Image, HalfcellImage
    finally:
        sys.__stdout__ = _terminal_stdout

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
THUMBNAIL_SIZE = 512


class ImageLoadError(ValueError):
    """An image could not be loaded within the supported limits."""


def decode_image(data: bytes) -> PILImage.Image:
    """Return an independent first-frame RGBA thumbnail, never a lazy decoder."""
    if len(data) > MAX_BYTES:
        raise ImageLoadError("Image exceeds the 8 MiB limit")
    try:
        with PILImage.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ImageLoadError("Image exceeds the 16 million pixel limit")
            image.seek(0)
            image.load()
            image.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE))
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError, PILImage.DecompressionBombError) as exc:
        raise ImageLoadError("Unsupported or damaged image") from exc


class _HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ImageLoadError("Image redirects must use HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ImageCache:
    """HTTPS thumbnail cache; local files are read afresh. Safe to call in a thread.

    Remote thumbnails expire after one day. Source URLs are hashed on disk; no
    runtime library or installed-game records are written.
    """
    def __init__(self, directory=None, *, timeout=10, ttl=86400):
        root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        self.directory = Path(directory) if directory else root / "kwak" / "images"
        self.timeout = timeout
        self.ttl = ttl
        self.opener = urllib.request.build_opener(_HTTPSRedirects())

    def cache_path(self, source):
        return self.directory / (hashlib.sha256(str(source).encode()).hexdigest() + ".png")

    def _read(self, stream, *, remote=False):
        chunks = []
        length = 0
        deadline = time.monotonic() + self.timeout
        read = getattr(stream, "read1", stream.read)
        while length <= MAX_BYTES:
            if remote and time.monotonic() >= deadline:
                raise ImageLoadError("Image download timed out")
            chunk = read(min(65536, MAX_BYTES + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        if length > MAX_BYTES:
            raise ImageLoadError("Image exceeds the 8 MiB limit")
        return decode_image(b"".join(chunks))

    def load(self, source):
        source = os.fspath(source)
        parsed = urlsplit(source)
        if parsed.scheme and parsed.scheme != "https":
            raise ImageLoadError("Use a local path or HTTPS image URL")
        if not parsed.scheme:
            try:
                with open(source, "rb") as stream:
                    return self._read(stream)
            except OSError as exc:
                raise ImageLoadError("Image file is unavailable") from exc
        path = self.cache_path(source)
        try:
            if time.time() - path.stat().st_mtime < self.ttl:
                with path.open("rb") as stream:
                    return self._read(stream)
        except (OSError, ImageLoadError):
            pass
        try:
            request = urllib.request.Request(source, headers={"User-Agent": "kwak-kiosk/0.1"})
            with self.opener.open(request, timeout=self.timeout) as stream:
                image = self._read(stream, remote=True)
        except ImageLoadError:
            raise
        except (OSError, ValueError) as exc:
            raise ImageLoadError("Image download failed") from exc
        # A read-only/full cache must not prevent displaying a valid image.
        temporary = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
                temporary = output.name
                image.save(output, format="PNG")
            os.replace(temporary, path)
        except OSError:
            pass
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return image


class TerminalImage(Widget):
    """Async reusable image pane with placeholders and stale-result protection.

    Call set_source(path_or_https_url), or set_source(None) to clear it. Use
    renderer='halfcell' for headless tests and terminals without pixel graphics.
    """
    DEFAULT_CSS = """
    TerminalImage { height: 16; width: 36; }
    TerminalImage .picture { width: 100%; height: 100%; }
    TerminalImage .placeholder { width: 100%; height: 100%; content-align: center middle; }
    """

    def __init__(self, source=None, *, cache=None, renderer="auto", **kwargs):
        super().__init__(**kwargs)
        if renderer not in ("auto", "halfcell"):
            raise ValueError("Unknown image renderer")
        self.source = source
        self.cache = cache or ImageCache()
        self.renderer = renderer
        self.generation = 0
        self.state = "empty"
        self._load_task = None

    def compose(self) -> ComposeResult:
        yield Static("No image", classes="placeholder", markup=False)
        yield (HalfcellImage if self.renderer == "halfcell" else Image)(classes="picture")

    def on_mount(self):
        self.call_after_refresh(self.set_source, self.source)

    def on_unmount(self):
        self.generation += 1
        if self._load_task:
            self._load_task.cancel()

    def set_source(self, source):
        self.source = source
        self.generation += 1
        if self._load_task:
            self._load_task.cancel()
        if not self.is_mounted:
            return
        picture = self.query_one(".picture")
        picture.image = None
        picture.display = False
        placeholder = self.query_one(".placeholder", Static)
        placeholder.display = True
        self.state = "loading" if source else "empty"
        placeholder.update("Loading image…" if source else "No image")
        if source:
            self._load_task = asyncio.create_task(self._load(source, self.generation))

    async def _load(self, source, generation):
        try:
            image = await asyncio.to_thread(self.cache.load, source)
        except asyncio.CancelledError:
            return
        except Exception:
            if generation == self.generation and self.is_mounted:
                self.state = "error"
                self.query_one(".placeholder", Static).update("Image unavailable")
            return
        if generation != self.generation or not self.is_mounted:
            return
        self.query_one(".picture").image = image
        self.query_one(".picture").display = True
        self.query_one(".placeholder").display = False
        self.state = "ready"
