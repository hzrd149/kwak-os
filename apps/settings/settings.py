#!/usr/bin/env python3
"""The desktop's window tiling mode and the terminal Settings app."""

import asyncio
import os
from pathlib import Path
import subprocess
import tempfile

from rich.text import Text
from textual import work
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.theme import Theme
from textual.widgets import Button, Footer, OptionList, Static
from textual.widgets.option_list import Option


MODES = {
    "kwassik": ("Kwassik", "One main window per workspace"),
    "master": ("Master", "A main window with the others alongside"),
    "dwindle": ("Dwindle", "Split the workspace as windows open"),
    "scrolling": ("Scrolling", "Move through a horizontal strip of windows"),
}


class SettingsError(Exception):
    """An actionable failure that can be displayed in the settings window."""


class ModeController:
    def __init__(self, config_path=None, run=subprocess.run):
        config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else Path(config_home) / "kwak" / "tiling-mode"
        )
        self.run = run

    def _request(self, command, expression):
        try:
            result = self.run(
                ["hyprctl", command, expression],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SettingsError(f"Cannot reach the desktop: {error}") from error
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "IPC failed"
            raise SettingsError(f"The desktop rejected the request: {detail}")
        return result.stdout.strip()

    def current_mode(self):
        mode = self._request("repl", "return kwak_settings.mode")
        if mode not in MODES:
            raise SettingsError(
                "Window settings are unavailable in this desktop session. "
                f"Unexpected response: {mode or '(empty)'}"
            )
        return mode

    def _set_live(self, mode):
        response = self._request("eval", f'kwak_settings.apply("{mode}")')
        if response != "ok":
            raise SettingsError(f"The desktop could not apply the mode: {response or '(empty)'}")
        actual = self.current_mode()
        if actual != mode:
            raise SettingsError(f"The desktop is still using {MODES[actual][0]}.")

    def _validate_saved_mode(self):
        try:
            saved = self.config_path.read_text().strip()
        except FileNotFoundError:
            return
        except (OSError, UnicodeError) as error:
            raise SettingsError(f"Cannot read the saved window mode: {error}") from error
        if saved not in MODES:
            raise SettingsError(
                f"The saved window mode at {self.config_path} is invalid. "
                "Correct or remove that file before applying a mode."
            )

    def _save(self, mode):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.config_path.parent,
                prefix=".tiling-mode-", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(mode + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.config_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def apply(self, mode):
        if mode not in MODES:
            raise SettingsError("Choose a supported window mode.")
        previous = self.current_mode()
        self._validate_saved_mode()
        try:
            self._set_live(mode)
            self._save(mode)
        except (SettingsError, OSError) as error:
            try:
                self._set_live(previous)
            except SettingsError as rollback_error:
                raise SettingsError(
                    f"The change was not saved: {error} "
                    f"Restoring {MODES[previous][0]} also failed: {rollback_error} "
                    "Reopen Settings to check the active mode."
                ) from error
            raise SettingsError(
                f"The change was not saved: {error} "
                f"Restored {MODES[previous][0]}."
            ) from error
        return mode


THEME = Theme(
    name="kwak",
    primary="#33ff66",
    secondary="#1f9940",
    accent="#33ff66",
    foreground="#c8ffd4",
    background="#000000",
    surface="#030803",
    panel="#0a1a0d",
    error="#ff5f5f",
    warning="#ffcc33",
    success="#33ff66",
    dark=True,
)


class SettingsApp(App):
    """Choose the window tiling mode; a mode is applied as soon as it is chosen."""

    TITLE = "Settings"
    CSS = """
    Screen { align: center middle; background: $background; }
    #panel {
        width: 100%; max-width: 72; height: auto; max-height: 100%;
        border: heavy $primary; border-title-align: left; padding: 1 2;
        scrollbar-size-vertical: 1;
        background: $surface;
    }
    .title { text-style: bold; color: $primary; }
    .hint { color: $foreground 70%; margin-bottom: 1; }
    OptionList { height: auto; max-height: 100%; border: none; padding: 0; background: $surface; }
    OptionList > .option-list--option-highlighted { background: $primary 25%; }
    #status { margin-top: 1; color: $foreground 70%; }
    #status.error { color: $error; }
    #buttons { height: auto; align-horizontal: right; margin-top: 1; }
    Button { border: none; height: 1; min-width: 10; margin-left: 2; }
    """
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("escape,q", "quit", "Close")]

    def __init__(self, controller=None):
        super().__init__()
        self.controller = controller or ModeController()
        self.current = None

    def compose(self):
        with VerticalScroll(id="panel") as panel:
            panel.border_title = "SETTINGS"
            yield Static("Window tiling mode", classes="title")
            yield Static("Choose how your windows use the workspace.", classes="hint")
            yield OptionList(id="modes", disabled=True)
            yield Static("Reading desktop settings…", id="status")
            with Horizontal(id="buttons"):
                yield Button("Close", id="close")
        yield Footer()

    def on_mount(self):
        self.register_theme(THEME)
        self.theme = "kwak"
        self.load()

    def show_modes(self):
        modes = self.query_one("#modes", OptionList)
        modes.clear_options()
        for mode, (name, description) in MODES.items():
            mark = "(•)" if mode == self.current else "( )"
            prompt = Text.assemble((f"{mark} {name}", "bold"), "\n    ", (description, "dim"))
            modes.add_option(Option(prompt, id=mode))
        if self.current:
            modes.highlighted = list(MODES).index(self.current)

    def say(self, message, error=False):
        status = self.query_one("#status", Static)
        status.update(message)
        status.set_class(error, "error")

    @work(exclusive=True)
    async def load(self):
        try:
            self.current = await asyncio.to_thread(self.controller.current_mode)
        except SettingsError as error:
            self.say(str(error), error=True)
            return
        self.show_modes()
        modes = self.query_one("#modes", OptionList)
        modes.disabled = False
        modes.focus()
        self.say("Choose a mode to apply it. Your choice is saved for next time.")

    def on_option_list_option_selected(self, event):
        if event.option.id != self.current:
            self.apply(event.option.id)

    @work(exclusive=True)
    async def apply(self, mode):
        modes = self.query_one("#modes", OptionList)
        modes.disabled = True
        self.say(f"Applying {MODES[mode][0]}…")
        try:
            self.current = await asyncio.to_thread(self.controller.apply, mode)
            self.say(f"{MODES[mode][0]} is active. Your choice has been saved.")
        except SettingsError as error:
            self.say(str(error), error=True)
            # A failed rollback can leave the desktop in an unknown mode.
            try:
                self.current = await asyncio.to_thread(self.controller.current_mode)
            except SettingsError as refresh_error:
                self.current = None
                self.say(f"{error} {refresh_error}", error=True)
        self.show_modes()
        modes.disabled = False
        modes.focus()

    def on_button_pressed(self, event):
        if event.button.id == "close":
            self.exit()


def main():
    SettingsApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
