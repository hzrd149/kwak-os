#!/usr/bin/env python3
"""The desktop's window tiling mode and the terminal Settings app."""

import asyncio
import pwd
import uuid
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import tempfile

from rich.text import Text
from textual import work
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.theme import Theme
from textual.widgets import Button, Footer, Input, OptionList, Static, TabbedContent, TabPane, TextArea
from textual.widgets.option_list import Option


MODES = {
    "kwassik": ("Kwassik", "One main window per workspace"),
    "master": ("Master", "A main window with the others alongside"),
    "dwindle": ("Dwindle", "Split the workspace as windows open"),
    "scrolling": ("Scrolling", "Move through a horizontal strip of windows"),
}

PROFILE_FIELDS = (("name", "Username"), ("display_name", "Display name"),
                  ("about", "About"), ("picture", "Picture URL"),
                  ("banner", "Banner URL"), ("website", "Website"),
                  ("nip05", "NIP-05 address"), ("lud16", "Lightning address"))


def account_request(op, **fields):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(180)
            sock.connect("/run/kwak-userd.sock")
            stream = sock.makefile("rwb")
            stream.write(json.dumps({"op": op, **fields}).encode() + b"\n")
            stream.flush()
            response = json.loads(stream.readline() or b"{}")
    except (OSError, ValueError) as error:
        raise SettingsError(f"Cannot reach account service: {error}") from error
    if not response.get("ok"):
        raise SettingsError(response.get("error") or "Account service did not respond.")
    return response["result"]


def parse_lines(text, section):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if section == "relays":
        result = []
        for line in lines:
            parts = line.split()
            if len(parts) not in (1, 2) or (len(parts) == 2 and parts[1] not in ("read", "write", "both")):
                raise SettingsError("Use one relay URL per line, optionally followed by read or write.")
            result.append([parts[0], parts[1] if len(parts) == 2 else "both"])
        return result
    return lines


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


class AudioController:
    def __init__(self, run=subprocess.run):
        self.run = run

    def request(self, *args):
        try:
            result = self.run(["wpctl", *args], capture_output=True, text=True,
                              timeout=5, check=False, env={**os.environ, "LC_ALL": "C"})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SettingsError("Audio is unavailable. Check the output device and retry.") from error
        if result.returncode:
            raise SettingsError("Audio is unavailable. Check the output device and retry.")
        return result.stdout

    def volume(self):
        output = self.request("get-volume", "@DEFAULT_AUDIO_SINK@")
        match = re.fullmatch(r"Volume: ([0-9]+(?:\.[0-9]+)?)(?: (\[MUTED\]))?\s*", output)
        if not match:
            raise SettingsError("Could not read audio volume. Please retry.")
        return round(float(match[1]) * 100), bool(match[2])

    def change(self, action):
        if action == "mute":
            self.request("set-mute", "@DEFAULT_AUDIO_SINK@", "toggle")
        elif action in ("up", "down"):
            self.request("set-volume", "@DEFAULT_AUDIO_SINK@",
                         "5%+" if action == "up" else "5%-", "--limit", "1.0")
        else:
            raise SettingsError("Unsupported audio action.")
        return self.volume()


class NetworkController:
    """User-scoped, nonpersistent Wi-Fi profiles; secrets never enter argv."""

    def __init__(self, run=subprocess.run):
        self.run = run

    def request(self, *args):
        try:
            result = self.run(["nmcli", "--wait", "25", "--colors", "no", *args],
                              capture_output=True, text=True, timeout=30, check=False,
                              env={**os.environ, "LC_ALL": "C"})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SettingsError("Network request failed. Check the adapter and retry.") from error
        if result.returncode:
            # Never display nmcli output: it can contain credentials.
            raise SettingsError("Network request failed. Check the password, signal and access permissions, then retry.")
        return result.stdout

    @staticmethod
    def rows(output):
        # nmcli terse escapes both colons and backslashes, including in SSIDs.
        rows = []
        for line in output.splitlines():
            fields, field, escaped = [], "", False
            for char in line:
                if escaped:
                    field += char
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == ":":
                    fields.append(field)
                    field = ""
                else:
                    field += char
            fields.append(field)
            rows.append(fields)
        return rows

    def status(self):
        devices = self.rows(self.request("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"))
        return "\n".join(f"{row[0]}: {row[2]}" + (f" · {row[3]}" if row[3] not in ("", "--") else "")
                         for row in devices if len(row) == 4 and row[1] != "loopback") or "No network adapters found."

    def scan(self):
        rows = self.rows(self.request("-t", "-f", "SSID,SECURITY,SIGNAL,DEVICE", "device", "wifi", "list", "--rescan", "yes"))
        found = {}
        for row in rows:
            if len(row) == 4 and row[0] and row[2].isdigit():
                key = (row[0], row[1], row[3])
                found[key] = max(int(row[2]), found.get(key, 0))
        return [dict(ssid=key[0], security=key[1], device=key[2], signal=signal)
                for key, signal in sorted(found.items(), key=lambda pair: -pair[1])]

    def connect(self, network, password=""):
        security = network["security"]
        if security not in ("", "--") and not set(security.split()).issubset({"WPA", "WPA1", "WPA2", "WPA3"}):
            raise SettingsError("This network needs administrator setup. Choose an open or personal WPA network.")
        protected = security not in ("", "--")
        if protected and not password:
            raise SettingsError("Enter the Wi-Fi password first.")
        if "\n" in password or "\r" in password or "\x00" in password:
            raise SettingsError("The password contains unsupported characters.")
        identity = str(uuid.uuid4())
        args = ["connection", "add", "save", "no", "type", "wifi", "ifname", network["device"],
                "con-name", "kwak-wifi-" + identity, "connection.uuid", identity,
                "ssid", network["ssid"], "connection.permissions", "user:" + pwd.getpwuid(os.getuid()).pw_name,
                "connection.autoconnect", "no"]
        if protected:
            args += ["wifi-sec.key-mgmt", "sae" if security == "WPA3" else "wpa-psk",
                     "wifi-sec.psk-flags", "2"]
        try:
            self.request(*args)
            with tempfile.TemporaryDirectory(prefix="kwak-wifi-", dir=os.environ.get("XDG_RUNTIME_DIR")) as directory:
                credentials = Path(directory) / "password"
                with open(credentials, "w", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
                    if protected:
                        stream.write("802-11-wireless-security.psk:" + password + "\n")
                self.request("connection", "up", "uuid", identity, "passwd-file", str(credentials))
        except (SettingsError, OSError):
            try:
                self.request("connection", "delete", "uuid", identity)
            except SettingsError:
                pass
            raise
        return self.status()


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
    TabbedContent { height: auto; max-height: 100%; }
    TabPane { padding: 1 0; height: auto; }
    Input { margin-bottom: 1; }
    TextArea { height: 9; margin-bottom: 1; }
    #audio-buttons, #network-buttons { height: 3; }
    #networks { max-height: 8; }
    """
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("escape,q", "quit", "Close")]

    def __init__(self, controller=None, audio=None, network=None):
        super().__init__()
        self.controller = controller or ModeController()
        self.current = None
        self.account = None
        self.audio = audio or AudioController()
        self.network = network or NetworkController()
        self.networks = []

    def compose(self):
        with VerticalScroll(id="panel") as panel:
            panel.border_title = "SETTINGS"
            with TabbedContent(initial="window"):
                with TabPane("Sound", id="sound"):
                    yield Static("Game volume", classes="title")
                    yield Static("Reading volume…", id="volume", markup=False)
                    with Horizontal(id="audio-buttons"):
                        yield Button("− 5%", id="audio-down")
                        yield Button("+ 5%", id="audio-up")
                        yield Button("Mute", id="audio-mute")
                        yield Button("Refresh", id="audio-refresh")
                with TabPane("Network", id="network"):
                    yield Static("Reading connections…", id="network-status", markup=False)
                    yield Static("Wi-Fi changes affect this kiosk. Passwords are not saved.", classes="hint")
                    yield OptionList(id="networks")
                    yield Input(placeholder="Wi-Fi password", password=True, id="wifi-password")
                    with Horizontal(id="network-buttons"):
                        yield Button("Scan", id="network-scan")
                        yield Button("Connect", id="network-connect", disabled=True)
                with TabPane("Window", id="window"):
                    yield Static("Window tiling mode", classes="title")
                    yield Static("Choose how your windows use the workspace.", classes="hint")
                    yield OptionList(id="modes", disabled=True)
                with TabPane("Profile", id="profile"):
                    for field, label in PROFILE_FIELDS:
                        yield Static(label)
                        yield Input(id=f"profile-{field}")
                    yield Button("Publish profile", id="save-profile", disabled=True)
                with TabPane("Relays", id="relays"):
                    yield Static("One relay per line. Add read or write for a single direction.", classes="hint")
                    yield TextArea(id="relay-list")
                    yield Button("Publish relays", id="save-relays", disabled=True)
                with TabPane("Media servers", id="media_servers"):
                    yield Static("Blossom servers, one HTTPS URL per line.", classes="hint")
                    yield TextArea(id="media-list")
                    yield Button("Publish media servers", id="save-media_servers", disabled=True)
            yield Input(placeholder="Account password or nsec to publish", password=True, id="account-password")
            yield Static("Reading desktop settings…", id="status")
            with Horizontal(id="buttons"):
                yield Button("Close", id="close")
        yield Footer()

    def on_mount(self):
        self.register_theme(THEME)
        self.theme = "kwak"
        self.update_tab_controls()
        self.load()
        self.load_account()
        self.update_audio()
        self.update_network()

    def update_tab_controls(self):
        active = self.query_one(TabbedContent).active
        self.query_one("#account-password", Input).display = active in ("profile", "relays", "media_servers")
        self.query_one("#status", Static).display = active not in ("sound", "network")

    def on_tabbed_content_tab_activated(self, event):
        self.update_tab_controls()

    @work(exclusive=True, group="audio")
    async def update_audio(self, action="refresh"):
        buttons = [self.query_one(f"#audio-{name}", Button) for name in ("down", "up", "mute", "refresh")]
        for button in buttons:
            button.disabled = True
        try:
            volume, muted = await asyncio.to_thread(self.audio.volume if action == "refresh" else self.audio.change,
                                                   *(() if action == "refresh" else (action,)))
            self.query_one("#volume", Static).update(f"Volume {volume}%" + (" · Muted" if muted else ""))
            self.query_one("#audio-mute", Button).label = "Unmute" if muted else "Mute"
        except SettingsError as error:
            self.query_one("#volume", Static).update(str(error))
        finally:
            for button in buttons:
                button.disabled = False

    @work(exclusive=True, group="account-load")
    async def load_account(self):
        try:
            self.account = await asyncio.to_thread(account_request, "account_settings")
        except SettingsError as error:
            self.say(str(error), error=True)
            return
        profile = self.account["profile"]
        for field, _ in PROFILE_FIELDS:
            self.query_one(f"#profile-{field}", Input).value = str(profile.get(field) or "")
        self.query_one("#relay-list", TextArea).text = "\n".join(
            f"{tag[1]} {tag[2] if len(tag) > 2 else 'both'}" for tag in self.account["relays"])
        self.query_one("#media-list", TextArea).text = "\n".join(self.account["media_servers"])
        for section in ("profile", "relays", "media_servers"):
            self.query_one(f"#save-{section}", Button).disabled = False

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
        if event.option_list.id == "networks":
            self.query_one("#wifi-password", Input).focus()
            return
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
        elif event.button.id and event.button.id.startswith("network-"):
            self.update_network(event.button.id.removeprefix("network-"))
        elif event.button.id and event.button.id.startswith("audio-"):
            self.update_audio(event.button.id.removeprefix("audio-"))
        elif event.button.id and event.button.id.startswith("save-"):
            self.save_account(event.button.id.removeprefix("save-"))

    @work(exclusive=True, group="network")
    async def update_network(self, action="status"):
        connect = self.query_one("#network-connect", Button)
        scan = self.query_one("#network-scan", Button)
        choices = self.query_one("#networks", OptionList)
        field = self.query_one("#wifi-password", Input)
        selection = choices.highlighted
        password = field.value
        field.value = ""
        connect.disabled = scan.disabled = choices.disabled = True
        status = self.query_one("#network-status", Static)
        status.update("Connecting…" if action == "connect" else "Checking network…")
        try:
            if action == "connect":
                if selection is None or selection >= len(self.networks):
                    raise SettingsError("Select a network first.")
                message = await asyncio.to_thread(self.network.connect, self.networks[selection], password)
            else:
                message = await asyncio.to_thread(self.network.status)
                if action == "scan":
                    self.networks = await asyncio.to_thread(self.network.scan)
                    choices.clear_options()
                    for index, network in enumerate(self.networks):
                        choices.add_option(Option(Text(f"{network['ssid']} · {network['signal']}% · {network['security'] or 'Open'}"), id=str(index)))
                    if not self.networks:
                        message += "\nNo visible Wi-Fi networks. Check that Wi-Fi is enabled."
            status.update(message)
        except (SettingsError, OSError) as error:
            status.update(str(error) if isinstance(error, SettingsError) else "Network request failed. Please retry.")
        finally:
            scan.disabled = choices.disabled = False
            connect.disabled = not bool(self.networks)

    @work(exclusive=True, group="account-save")
    async def save_account(self, section):
        if section == "profile":
            value = {field: self.query_one(f"#profile-{field}", Input).value
                     for field, _ in PROFILE_FIELDS}
        else:
            source = "#relay-list" if section == "relays" else "#media-list"
            try:
                value = parse_lines(self.query_one(source, TextArea).text, section)
            except SettingsError as error:
                self.say(str(error), error=True)
                return
        password_field = self.query_one("#account-password", Input)
        password = password_field.value
        password_field.value = ""
        self.say(f"Publishing {section.replace('_', ' ')}…")
        try:
            result = await asyncio.to_thread(account_request, "publish_settings",
                                             section=section, value=value, password=password)
            self.say(f"Published to {len(result['relays'])} relays.")
        except SettingsError as error:
            self.say(str(error), error=True)


def main():
    SettingsApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
