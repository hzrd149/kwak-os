"""Small game action menu; target is captured before its terminal opens."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata

from textual import work
from textual.app import App
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static, TextArea
from settings import THEME, apply_saved_theme
from kiosk import Runtime

if __name__ == "__main__":
    sys.modules.setdefault("overlay", sys.modules[__name__])


class OverlayError(RuntimeError):
    pass


class DesktopActions:
    def __init__(self, target=None, run=subprocess.run, spawn=subprocess.Popen, runtime=None):
        self.target = target if target is not None else self.environment_target()
        self.run = run
        self.spawn = spawn
        self.runtime = runtime or Runtime()

    @staticmethod
    def environment_target():
        try:
            target = json.loads(os.environ.get("KWAK_OVERLAY_TARGET", "{}"))
            return target if isinstance(target, dict) else {}
        except ValueError:
            return {}

    def request(self, *args, json_output=False):
        try:
            result = self.run(["hyprctl", *args], capture_output=True, text=True,
                              timeout=5, check=False)
            if result.returncode:
                raise OverlayError("Desktop action unavailable. Please retry.")
            return json.loads(result.stdout) if json_output else result.stdout
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise OverlayError("Desktop action unavailable. Please retry.") from error

    @staticmethod
    def process_identity(pid):
        if not isinstance(pid, int) or pid <= 0:
            return None
        try:
            directory = Path(f"/proc/{pid}")
            if directory.stat().st_uid != os.getuid():
                return None
            executable = (directory / "exe").resolve(strict=True)
            # Start time guards against PID reuse while the menu was open.
            start = (directory / "stat").read_text().rsplit(")", 1)[1].split()[19]
            return {"exe": str(executable), "start": start}
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def canonical_game(address):
        if not isinstance(address, str) or len(address.encode()) > 4096:
            return False
        parts = address.split(":", 2)
        return (len(parts) == 3 and parts[0] == "35129" and
                bool(re.fullmatch(r"[0-9a-f]{64}", parts[1])) and
                not any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in parts[2]))

    def runtime_managed(self):
        return self.target.get("runtime_managed", bool(re.fullmatch(r"[0-9a-f]{32}", str(self.target.get("window_id", "")))))

    def capture(self):
        try:
            window = self.request("-j", "activewindow", json_output=True)
            if not isinstance(window, dict):
                return {}
            target = {key: window.get(key) for key in ("address", "pid", "class", "title")} | {
                "process": self.process_identity(window.get("pid"))}
            if target["process"] and Path(target["process"]["exe"]).name in ("kwaklet", ".kwaklet-wrapped", "napplet", ".napplet-wrapped"):
                environment = dict(entry.split(b"=", 1) for entry in
                                   Path(f"/proc/{target['pid']}/environ").read_bytes().split(b"\0") if b"=" in entry)
                token = environment.get(b"KWAKORE_INSTANCE_ID", b"").decode("utf-8")
                address = environment.get(b"KWAKORE_NAPP_ID", b"").decode("utf-8")
                if self.canonical_game(address):
                    # Some trial renderers expose an internal serial ID rather
                    # than the daemon's opaque public stop token.
                    target.update(window_id=token, napplet_address=address,
                                  runtime_managed=bool(re.fullmatch(r"[0-9a-f]{32}", token)))
            return target
        except (OverlayError, RuntimeError, OSError, ValueError, KeyError):
            return {}

    def live_target(self, game_only=False):
        target = self.target
        if not re.fullmatch(r"0x[0-9a-fA-F]+", str(target.get("address", ""))):
            return None
        identity = self.process_identity(target.get("pid"))
        if not identity or identity != target.get("process"):
            return None
        if game_only and Path(identity["exe"]).name not in ("kwaklet", ".kwaklet-wrapped", "napplet", ".napplet-wrapped"):
            return None
        clients = self.request("-j", "clients", json_output=True)
        return next((client for client in clients if client.get("address") == target["address"]
                     and client.get("pid") == target["pid"] and client.get("class") == target.get("class")), None)

    def game_available(self):
        if not self.live_target(game_only=True):
            return False
        if not self.canonical_game(self.target.get("napplet_address")):
            return False
        if not self.runtime_managed():
            return bool(re.fullmatch(r"[1-9][0-9]{0,19}", str(self.target.get("window_id", ""))))
        token = self.target.get("window_id")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            return False
        try:
            return any(game.get("window_id") == token and game.get("address") == self.target.get("napplet_address")
                       for game in self.runtime.request("windows")["items"])
        except (RuntimeError, KeyError) as error:
            raise OverlayError("Cannot reach this game. Please retry.") from error

    def focus_window(self, address):
        self.request("eval", "hl.dispatch(hl.dsp.focus({window=" + json.dumps("address:" + address, ensure_ascii=False) + "}))")

    def resume(self):
        if self.live_target():
            self.focus_window(self.target["address"])

    def exit_game(self):
        if not self.game_available():
            raise OverlayError("That game has already closed. Return to the library to choose another.")
        if not self.runtime_managed():
            # This renderer ID cannot be matched to a public RPC stop token.
            # Recheck ownership, PID start time and compositor identity, then
            # close exactly this window; never infer an RPC token from its name.
            if not self.live_target(game_only=True):
                raise OverlayError("That game has already closed.")
            self.request("eval", "hl.dispatch(hl.dsp.window.close({window=" +
                         json.dumps("address:" + self.target["address"]) + "}))")
            return
        # Opaque runtime IDs cannot be reused by a restarted daemon.
        try:
            result = self.runtime.request("stop", self.target["window_id"])
            if not result.get("closed"):
                raise OverlayError("The game could not close. Please retry.")
        except RuntimeError as error:
            raise OverlayError("The game could not close. Please retry.") from error

    def library(self):
        self.request("eval", "hl.dispatch(hl.dsp.focus({workspace=1}))")
        clients = self.request("-j", "clients", json_output=True)
        library = next((client for client in clients if client.get("class") == "org.kwak.Library"), None)
        if library is None:
            self.open_app("kwak-kiosk")
            for _ in range(20):
                time.sleep(.1)
                clients = self.request("-j", "clients", json_output=True)
                library = next((client for client in clients if client.get("class") == "org.kwak.Library"), None)
                if library:
                    break
        if library:
            self.focus_window(library["address"])

    def switch_games(self):
        self.request("eval", "hl.plugin.hyprflow.toggle()")

    def open_app(self, command):
        try:
            self.spawn([command], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            raise OverlayError("This system app is unavailable. Please retry.") from error


class PublishScreenshot(ModalScreen):
    CSS = """
    PublishScreenshot { align: center middle; }
    #publish-dialog { width: 64; height: auto; padding: 1 2; border: heavy $primary; background: $surface; }
    #caption { height: 5; margin: 1 0; }
    #publish-dialog Button { width: 100%; height: 3; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, snapshot):
        super().__init__()
        self.snapshot = snapshot

    def compose(self):
        with Vertical(id="publish-dialog"):
            yield Static("Publish this screenshot to your Nostr account? It will be public.", markup=False)
            yield Static(f"{self.snapshot['filename']} · {self.snapshot.get('width', '?')}×{self.snapshot.get('height', '?')}",
                         id="screenshot-info", markup=False)
            yield Static("Optional caption")
            yield TextArea(id="caption")
            yield Button("Cancel", id="cancel-publish", variant="primary")
            yield Button("Publish screenshot", id="confirm-publish")

    def on_mount(self):
        self.query_one("#cancel-publish", Button).focus()

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "confirm-publish":
            caption = self.query_one("#caption", TextArea).text
            if len(caption) > 1000:
                self.query_one("#caption", TextArea).focus()
                return
            self.dismiss(caption)
        else:
            self.dismiss(None)


class GameOverlay(App):
    TITLE = "kwakOS · Game Menu"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("escape", "resume", "Resume")]
    CSS = """
    Screen { background: $background; color: $foreground; align: center middle; }
    #menu { width: 100%; height: auto; max-height: 100%; padding: 1 3; }
    #brand { color: $primary; text-style: bold; height: 2; }
    #current { color: $foreground 70%; height: 2; }
    #menu Button { width: 100%; height: 3; margin-bottom: 0; }
    #menu-status { color: $warning; height: 2; }
    """

    def __init__(self, desktop=None, screenshots=None):
        super().__init__()
        self.desktop = desktop or DesktopActions()
        from screenshots import ScreenshotStore
        self.screenshots = screenshots or ScreenshotStore()
        self.snapshot = None
        self.register_theme(THEME)
        self.theme = "kwak"

    def compose(self):
        with VerticalScroll(id="menu"):
            yield Static("KWAK OS  /  GAME MENU", id="brand")
            yield Static(self.desktop.target.get("title") or "System menu", id="current", markup=False)
            yield Button("Resume", id="resume", variant="primary")
            yield Button("Exit Game", id="exit-game", disabled=True)
            yield Button("Take Screenshot", id="screenshot", disabled=True)
            yield Button("Publish Screenshot", id="publish-screenshot", disabled=True)
            yield Button("Back to Library", id="back-library")
            yield Button("Switch Games", id="switch-games")
            yield Button("Settings", id="settings")
            yield Button("Session", id="session")
            yield Static("", id="menu-status", markup=False)
        yield Footer()

    async def on_mount(self):
        await apply_saved_theme(self)
        self.query_one("#resume", Button).focus()
        self.check_game()

    @work(exclusive=True, group="target")
    async def check_game(self):
        try:
            available = await asyncio.to_thread(self.desktop.game_available)
        except OverlayError:
            available = False
        self.query_one("#exit-game", Button).disabled = not available
        self.query_one("#screenshot", Button).disabled = not available
        self.snapshot = await asyncio.to_thread(self.screenshots.latest, self.desktop.target.get("napplet_address"))
        self.query_one("#publish-screenshot", Button).disabled = self.snapshot is None

    def action_resume(self):
        self.perform("resume")

    def on_button_pressed(self, event):
        if event.button.id == "publish-screenshot":
            if self.snapshot:
                snapshot = dict(self.snapshot)
                self.push_screen(PublishScreenshot(snapshot), lambda caption: self.publish(snapshot, caption) if caption is not None else None)
        elif event.button.id == "screenshot":
            self.take_screenshot()
        else:
            self.perform(event.button.id)

    def enable_menu(self, enabled):
        for button in self.default_screen.query(Button):
            button.disabled = not enabled
        if enabled:
            self.check_game()

    @work(exclusive=True, group="actions")
    async def take_screenshot(self):
        self.enable_menu(False)
        self.query_one("#menu-status", Static).update("Capturing game…")
        try:
            self.snapshot = await asyncio.to_thread(self.screenshots.capture, self.desktop)
            self.query_one("#menu-status", Static).update("Screenshot saved. Choose Publish Screenshot to share it.")
        except OverlayError as error:
            self.query_one("#menu-status", Static).update(str(error))
        finally:
            self.enable_menu(True)

    @work(exclusive=True, group="actions")
    async def publish(self, snapshot, caption):
        self.enable_menu(False)
        self.query_one("#menu-status", Static).update("Publishing screenshot…")
        try:
            await asyncio.to_thread(self.screenshots.publish, snapshot, caption)
            self.query_one("#menu-status", Static).update("Screenshot published to your Nostr account.")
        except OverlayError as error:
            self.query_one("#menu-status", Static).update(str(error))
        finally:
            self.enable_menu(True)

    @work(exclusive=True, group="actions")
    async def perform(self, action):
        actions = {"resume": self.desktop.resume, "exit-game": self.desktop.exit_game,
                   "back-library": self.desktop.library, "switch-games": self.desktop.switch_games,
                   "settings": lambda: self.desktop.open_app("kwak-settings"),
                   "session": lambda: self.desktop.open_app("kwak-session")}
        if action not in actions:
            return
        for button in self.query(Button):
            button.disabled = True
        try:
            await asyncio.to_thread(actions[action])
        except OverlayError as error:
            self.query_one("#menu-status", Static).update(str(error))
            for button in self.query(Button):
                button.disabled = False
            self.check_game()
        else:
            self.exit()


def main():
    if "--launch" in sys.argv:
        # The wrapper holds the one-overlay lock; capture BEFORE kitty changes focus.
        kitty, config = sys.argv[2:4]
        target = DesktopActions().capture()
        environment = {**os.environ, "KWAK_OVERLAY_TARGET": json.dumps(target)}
        return subprocess.call([kitty, "--config", config, "--override", "background_opacity=0.9",
                                "--override", "initial_window_width=72c", "--override", "initial_window_height=36c",
                                "--class", "org.kwak.Overlay", "--title", "Game Menu", sys.executable,
                                str(Path(__file__).resolve())], env=environment)
    GameOverlay().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
