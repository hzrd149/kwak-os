"""One-row controller hint footer, shown only after controller input."""
from textual.widgets import Static


class ButtonHints(Static):
    DEFAULT_CSS = """
    ButtonHints { height: 1; width: 100%; display: none; color: $foreground 70%; background: $panel; padding: 0 1; }
    """

    def __init__(self, **kwargs):
        super().__init__("", markup=False, **kwargs)

    def on_mount(self):
        self.display = getattr(self.app, "input_mode", "") == "gamepad"
        self.watch(self.screen, "focused", self.refresh_hints)
        self.refresh_hints()

    def refresh_hints(self, focused=None):
        screen = self.screen
        hints = getattr(screen, "gamepad_hints", None)
        if not callable(hints):
            hints = getattr(self.app, "gamepad_hints", None)
        pairs = hints(screen.focused) if callable(hints) else [("A", "Select"), ("B", "Back")]
        self.update("  ".join(f"[{button}] {label}" for button, label in pairs))
