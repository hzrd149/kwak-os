"""Kitty watcher sharing the library's live point size with the game menu.

Runs inside Kitty, never inside Textual. No remote-control socket is enabled.
"""
import math
import os
from pathlib import Path


def synchronize(boss, window, data):
    from kitty.fast_data_types import os_window_font_size
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        return
    path = Path(runtime) / 'kwak-library-font'
    try:
        current = os_window_font_size(window.os_window_id)
        if os.environ.get('KWAK_TERMINAL_ROLE') == 'overlay':
            size = float(path.read_text())
            if math.isfinite(size) and 4 <= size <= 200 and abs(current - size) > 0.01:
                boss.change_font_size(True, None, size)
        elif current and math.isfinite(current):
            temporary = path.with_suffix('.tmp')
            temporary.write_text(str(current))
            temporary.chmod(0o600)
            temporary.replace(path)
    except (OSError, ValueError):
        # A missing library uses the configured font until the library appears.
        pass


on_resize = synchronize
on_focus_change = synchronize
