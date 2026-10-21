"""Session-local overlay command transport and serialized cold startup."""
import fcntl
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def launch(kitty, config, desktop):
    directory = Path(os.environ['XDG_RUNTIME_DIR'])
    endpoint = str(directory / 'kwak-overlay.sock')
    with (directory / 'kwak-overlay.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        target = desktop.capture()
        if target.get('class') == 'org.kwak.Overlay':
            message = {'hide': True}
        else:
            desktop.target = target
            if not desktop.launch_available():
                return 0
            message = {'target': target}
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as control:
            control.settimeout(0.2)
            try:
                control.sendto(json.dumps(message).encode(), endpoint)
                return 0
            except (FileNotFoundError, ConnectionRefusedError):
                Path(endpoint).unlink(missing_ok=True)
            except TimeoutError:
                return 1
        if message.get('hide'):
            return 0
        environment = {**os.environ, 'KWAK_OVERLAY_TARGET': json.dumps(target),
                       'KWAK_OVERLAY_SOCKET': endpoint, 'KWAK_TERMINAL_ROLE': 'overlay'}
        try:
            size = float((directory / 'kwak-library-font').read_text())
            if not math.isfinite(size) or not 4 <= size <= 200:
                size = 18
        except (OSError, ValueError):
            size = 18
        process = subprocess.Popen([kitty, '--config', config,
            '--override', 'background_opacity=0.95', '--override', f'font_size={size}',
            '--override', 'initial_window_width=72c', '--override', 'initial_window_height=28c',
            '--watcher', str(Path(__file__).with_name('terminal_font.py')),
            '--class', 'org.kwak.Overlay', '--title', 'Game Menu', sys.executable,
            str(Path(__file__).with_name('overlay.py'))], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        for _ in range(100):
            if Path(endpoint).exists():
                return 0
            if process.poll() is not None:
                return process.returncode
            time.sleep(0.05)
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return 1


if __name__ == '__main__':
    from desktop_actions import DesktopActions, OverlayError
    try:
        raise SystemExit(launch(*sys.argv[1:3], DesktopActions()))
    except (OverlayError, OSError):
        raise SystemExit(1)
