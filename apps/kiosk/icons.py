"""Napplet icons from kwakore, cached by their verified manifest hash.

Descriptors carry only `icon: {sha256, mime}`; `kwak icon` returns the image
after kwakore checks the hash and re-encodes it as PNG. Files are named by
hash, so they never expire and a changed icon simply gets a new file.
"""
import asyncio
import base64
import os
from pathlib import Path
import re
import tempfile
import threading
import time

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 2 * 1024 * 1024
RETRY_UNAVAILABLE = 300


def icon_hash(game):
    icon = game.get("icon") if isinstance(game, dict) else None
    digest = icon.get("sha256") if isinstance(icon, dict) else None
    return digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None


class IconStore:
    """`load` runs on the UI loop with at most three fetches in flight, so
    waiting cards never occupy the shared worker threads."""

    def __init__(self, runtime, directory=None, clock=time.monotonic):
        root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        self.directory = Path(directory) if directory else root / "kwak" / "icons"
        self.runtime = runtime
        self.clock = clock
        self.slots = None
        self.lock = threading.Lock()
        self.failed = {}

    def path_for(self, game):
        digest = icon_hash(game)
        return self.directory / f"{digest}.png" if digest else None

    def cached(self, game):
        path = self.path_for(game)
        return path if path is not None and path.is_file() else None

    async def load(self, game):
        path = self.cached(game)
        if path is not None or self.path_for(game) is None:
            return path
        if self.slots is None:
            self.slots = asyncio.Semaphore(3)
        async with self.slots:
            return await asyncio.to_thread(self.fetch, game)

    def fetch(self, game):
        """Return a local PNG path for the game's icon, or None. Blocking."""
        path = self.path_for(game)
        if path is None or not callable(getattr(self.runtime, "icon", None)):
            return None
        if path.is_file():
            return path
        key = (game["address"], path.stem)
        with self.lock:
            retry = self.failed.get(key)
            if retry is not None and (retry is True or self.clock() < retry):
                return None
        try:
            result = self.runtime.icon(game["address"])
            data = base64.b64decode(result["data"], validate=True)
        except RuntimeError:
            # "Unavailable" may be a relay or Blossom outage; retry later.
            return self.remember(key, self.clock() + RETRY_UNAVAILABLE)
        except (KeyError, TypeError, ValueError):
            return self.remember(key, True)
        # `sha256` names the manifest's source image, not the PNG bytes.
        if not data.startswith(PNG_SIGNATURE) or len(data) > MAX_BYTES or result.get("sha256") != path.stem:
            return self.remember(key, True)
        self.write(path, data)
        return path

    def remember(self, key, retry):
        with self.lock:
            self.failed[key] = retry
        return None

    def write(self, path, data):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".icon-", delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
