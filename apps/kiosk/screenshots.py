"""Local game screenshots and explicit account publication."""
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time
import uuid
from library import account_request
from overlay import OverlayError


class ScreenshotStore:
    def __init__(self, root=None, run=subprocess.run, request=account_request):
        self.root = Path(root) if root is not None else Path.home() / '.local/share/kwak/screenshots'
        self.run = run
        self.request = request

    def _safe_root(self):
        if any(component.is_symlink() for component in (self.root, *self.root.parents)):
            raise OverlayError('Screenshot folder unavailable.')

    def _file(self, filename):
        self._safe_root()
        if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.png', filename):
            raise OverlayError('Screenshot unavailable.')
        path = self.root / filename
        if path.is_symlink() or not path.is_file() or self.root.is_symlink():
            raise OverlayError('Screenshot unavailable.')
        return path

    def latest(self, game):
        try:
            metadata = self.root / 'latest.json'
            if metadata.is_symlink():
                return None
            snapshot = json.loads(metadata.read_text())
            if not isinstance(snapshot, dict) or snapshot.get('game') != game:
                return None
            snapshot['path'] = str(self._file(snapshot['filename']))
            return snapshot
        except (OSError, ValueError, KeyError, OverlayError):
            return None

    def capture(self, desktop):
        if not desktop.game_available():
            raise OverlayError('Open a game before taking a screenshot.')
        game = desktop.live_target(game_only=True)
        if not game:
            raise OverlayError('That game has already closed.')
        try:
            self._safe_root()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
        except OSError as error:
            raise OverlayError('Screenshot folder unavailable.') from error
        filename = time.strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex + '.png'
        path = self.root / filename
        overlay = desktop.request('-j', 'activewindow', json_output=True)
        hidden = isinstance(overlay, dict) and overlay.get('class') == 'org.kwak.Overlay'
        try:
            if hidden:
                desktop.request('eval', 'hl.dispatch(hl.dsp.window.move({window=' + json.dumps('address:' + overlay['address']) + ',workspace="special:kwak-capture",follow=false}))')
            desktop.resume()
            time.sleep(.15)
            game = desktop.live_target(game_only=True)
            if not game:
                raise OverlayError('That game has already closed.')
            x, y = game['at']
            width, height = game['size']
            if not all(isinstance(v, int) for v in (x, y, width, height)) or width <= 0 or height <= 0:
                raise OverlayError('Game window unavailable.')
            result = self.run(['grim', '-g', f'{x},{y} {width}x{height}', str(path)], capture_output=True, timeout=10, check=False)
            if result.returncode:
                raise OverlayError('Screenshot failed. Please retry.')
            os.chmod(self._file(filename), 0o600)
            with path.open('rb') as image:
                header = image.read(24)
            if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[8:16] != b'\x00\x00\x00\x0dIHDR':
                raise OverlayError('Screenshot failed. Please retry.')
            width, height = struct.unpack('>II', header[16:24])
            if not width or not height or width * height > 40_000_000:
                raise OverlayError('Screenshot failed. Please retry.')
            snapshot = dict(filename=filename, path=str(path), width=width, height=height,
                            game=desktop.target['napplet_address'])
            temporary = self.root / ('latest-' + uuid.uuid4().hex + '.json')
            temporary.write_text(json.dumps(snapshot))
            os.chmod(temporary, 0o600)
            temporary.replace(self.root / 'latest.json')
            return snapshot
        except (OverlayError, OSError, subprocess.TimeoutExpired, KeyError, ValueError) as error:
            path.unlink(missing_ok=True)
            raise OverlayError('Screenshot failed. Please retry.') from error
        finally:
            if hidden:
                workspace = overlay['workspace']['name']
                try:
                    desktop.request('eval', 'hl.dispatch(hl.dsp.window.move({window=' + json.dumps('address:' + overlay['address']) + ',workspace=' + json.dumps(workspace, ensure_ascii=False) + ',follow=false}))')
                finally:
                    desktop.request('eval', 'hl.dispatch(hl.dsp.focus({window=' + json.dumps('address:' + overlay['address']) + '}))')

    def publish(self, snapshot, caption=''):
        try:
            filename, game = snapshot['filename'], snapshot['game']
            path = self._file(filename)
            current = self.latest(game)
            if not current or current['filename'] != filename or snapshot.get('path') != str(path):
                raise OverlayError('Choose the latest screenshot of this game.')
        except (KeyError, TypeError) as error:
            raise OverlayError('Screenshot unavailable.') from error
        try:
            return self.request('publish_screenshot', filename=snapshot['filename'], caption=caption, game=snapshot['game'])
        except (RuntimeError, OSError) as error:
            setup = 'Add a media server in Settings before publishing screenshots.'
            if str(error) == setup:
                raise OverlayError(setup + ' Your screenshot is saved.') from error
            raise OverlayError('Publishing failed. Your screenshot is saved; please retry.') from error
