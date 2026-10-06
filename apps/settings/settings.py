#!/usr/bin/env python3
"""The desktop's single window-layout preference and GTK settings window."""

import os
from pathlib import Path
import subprocess
import tempfile
import threading


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


def main():
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    GLib.set_prgname("org.kwak.Settings")
    Gdk.set_program_class("org.kwak.Settings")

    class SettingsApplication(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.kwak.Settings")
            self.controller = ModeController()
            self.window = None
            self.current = None
            self.busy = False
            self.rows = {}

        def do_startup(self):
            Gtk.Application.do_startup(self)
            close_action = Gio.SimpleAction.new("close", None)
            close_action.connect("activate", self.close)
            self.add_action(close_action)
            self.set_accels_for_action("app.close", ["Escape", "<Primary>w"])
            provider = Gtk.CssProvider()
            provider.load_from_path(str(Path(__file__).with_suffix(".css")))
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        def do_activate(self):
            if self.window is None:
                self.build_window()
            self.window.show_all()
            self.window.present()
            if not self.busy:
                self.run_job(self.controller.current_mode, self.loaded)

        def build_window(self):
            self.window = Gtk.ApplicationWindow(application=self, title="Settings")
            self.window.set_icon_name("preferences-system")
            self.window.set_default_size(560, 570)
            self.window.set_resizable(False)
            self.window.connect("delete-event", lambda *_: self.busy)
            self.window.connect("destroy", lambda *_: self.quit())
            header = Gtk.HeaderBar(title="Settings", show_close_button=True)
            self.window.set_titlebar(header)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            content.set_border_width(24)
            self.window.add(content)
            title = Gtk.Label(label="Window tiling mode", xalign=0)
            title.get_style_context().add_class("section-title")
            content.pack_start(title, False, False, 0)
            subtitle = Gtk.Label(label="Choose how your windows use the workspace.", xalign=0)
            subtitle.get_style_context().add_class("description")
            subtitle.set_margin_top(8)
            subtitle.set_margin_bottom(22)
            content.pack_start(subtitle, False, False, 0)

            choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            group = None
            for mode, (name, description) in MODES.items():
                row = Gtk.RadioButton.new_from_widget(group)
                group = row
                row.get_style_context().add_class("mode-row")
                labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
                labels.set_margin_start(12)
                label = Gtk.Label(label=name, xalign=0)
                label.get_style_context().add_class("mode-name")
                labels.pack_start(label, False, False, 0)
                detail = Gtk.Label(label=description, xalign=0)
                detail.get_style_context().add_class("description")
                labels.pack_start(detail, False, False, 0)
                row.add(labels)
                row.get_accessible().set_name(name)
                row.get_accessible().set_description(description)
                row.connect("toggled", self.selection_changed)
                self.rows[mode] = row
                choices.pack_start(row, False, False, 0)
            content.pack_start(choices, False, False, 0)

            self.status = Gtk.Label(label="Reading desktop settings…", xalign=0, yalign=0)
            self.status.set_line_wrap(True)
            self.status.set_max_width_chars(52)
            self.status.set_margin_top(18)
            self.status.set_margin_bottom(18)
            self.status.get_style_context().add_class("status")
            content.pack_start(self.status, True, True, 0)

            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            buttons.set_halign(Gtk.Align.END)
            self.close_button = Gtk.Button.new_with_mnemonic("_Close")
            self.close_button.connect("clicked", self.close)
            self.apply_button = Gtk.Button.new_with_mnemonic("_Apply")
            self.apply_button.get_style_context().add_class("apply")
            self.apply_button.set_can_default(True)
            self.apply_button.connect("clicked", self.apply_selection)
            buttons.pack_start(self.close_button, False, False, 0)
            buttons.pack_start(self.apply_button, False, False, 0)
            content.pack_end(buttons, False, False, 0)
            self.apply_button.grab_default()
            self.selection_changed()

        def close(self, *_):
            if not self.busy:
                self.quit()

        def selected_mode(self):
            return next(mode for mode, row in self.rows.items() if row.get_active())

        def selection_changed(self, *_):
            for row in self.rows.values():
                context = row.get_style_context()
                if row.get_active():
                    context.add_class("selected")
                else:
                    context.remove_class("selected")
            if hasattr(self, "apply_button"):
                self.apply_button.set_sensitive(
                    not self.busy and self.current is not None
                    and self.selected_mode() != self.current
                )

        def show_status(self, message, error=False):
            self.status.set_text(message)
            context = self.status.get_style_context()
            if error:
                context.add_class("error")
            else:
                context.remove_class("error")

        def run_job(self, work, complete):
            self.busy = True
            self.close_button.set_sensitive(False)
            for row in self.rows.values():
                row.set_sensitive(False)
            self.selection_changed()

            def worker():
                try:
                    value, error = work(), None
                except SettingsError as failure:
                    value, error = None, str(failure)
                GLib.idle_add(finished, value, error)

            def finished(value, error):
                self.busy = False
                self.close_button.set_sensitive(True)
                for row in self.rows.values():
                    row.set_sensitive(True)
                complete(value, error)
                self.selection_changed()
                return False

            threading.Thread(target=worker, daemon=True).start()

        def loaded(self, mode, error):
            self.current = mode
            if error:
                self.show_status(error, error=True)
            else:
                self.rows[mode].set_active(True)
                self.rows[mode].grab_focus()
                self.show_status("Select a mode, then Apply. Your choice is saved for next time.")

        def apply_selection(self, *_):
            mode = self.selected_mode()
            self.show_status("Applying window mode…")
            self.run_job(lambda: self.controller.apply(mode), self.applied)

        def applied(self, mode, error):
            if error:
                # A failed rollback can leave the compositor in an unknown mode.
                self.current = None
                self.show_status(error, error=True)

                def refreshed(actual, refresh_error):
                    self.current = actual
                    if refresh_error:
                        self.show_status(f"{error} {refresh_error}", error=True)

                self.run_job(self.controller.current_mode, refreshed)
            else:
                self.current = mode
                self.show_status(f"{MODES[mode][0]} is active. Your choice has been saved.")

    return SettingsApplication().run(None)


if __name__ == "__main__":
    raise SystemExit(main())
