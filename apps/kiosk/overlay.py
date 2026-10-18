"""Small game action menu; target is captured before its terminal opens."""
import asyncio
import socket
import json
import os
from pathlib import Path
import sys

from textual import work
from textual.app import App
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static, TextArea
from settings import THEME, apply_saved_theme
from desktop_actions import DesktopActions, OverlayError
from navigation import GamepadMixin
from images import TerminalImage

if __name__ == "__main__":
    sys.modules.setdefault("overlay", sys.modules[__name__])


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


class GameOverlay(GamepadMixin, App):
    TITLE = "kwakOS · Game Menu"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("escape", "resume", "Resume")]
    CSS = """
    Screen { background: $background; color: $foreground; align: center middle; }
    #overlay-layout { width: 100%; height: 100%; }
    #menu { width: 3fr; height: 100%; padding: 0 2; }
    #capture-panel { width: 2fr; height: 100%; padding: 1 2; background: $surface; }
    #capture-panel .section { height: 2; }
    #shot-preview { width: 100%; height: 1fr; }
    #shot-description { height: auto; margin-top: 1; color: $foreground 70%; }
    Screen.compact #capture-panel { display: none; }
    Screen.compact #menu { width: 100%; }
    .section { color: $primary; height: 1; text-style: bold; }
    #exit-game { color: $error; }
    #brand { color: $primary; text-style: bold; height: 1; }
    #current { color: $foreground 70%; height: 2; }
    #menu Button { width: 100%; height: 2; min-height: 2; border: none; margin-bottom: 0; }
    #menu #resume, #menu #exit-game { height: 3; }
    #menu-status { color: $warning; height: 2; }
    """

    def __init__(self, desktop=None, screenshots=None, gamepad=None):
        super().__init__()
        self.gamepad = gamepad
        self.desktop = desktop or DesktopActions()
        from screenshots import ScreenshotStore
        self.screenshots = screenshots or ScreenshotStore()
        self.snapshot = None
        self.control = None
        self.hidden = False
        self.register_theme(THEME)
        self.theme = "kwak"

    def compose(self):
        with Horizontal(id="overlay-layout"):
            with VerticalScroll(id="menu"):
                yield Static("IN GAME", id="brand")
                yield Static(self.desktop.target.get("title") or "Game", id="current", markup=False)
                yield Button("Resume", id="resume", variant="primary")
                yield Button("Exit Game", id="exit-game", disabled=True)
                yield Static("CAPTURE & SHARE", classes="section")
                yield Button("Take Screenshot", id="screenshot", disabled=True)
                yield Button("Publish Screenshot", id="publish-screenshot", disabled=True)
                yield Static("NAVIGATE", classes="section")
                yield Button("Back to Library", id="back-library")
                yield Button("Switch Games", id="switch-games")
                yield Static("SYSTEM", classes="section")
                yield Button("Settings", id="settings")
                yield Button("Session", id="session")
                yield Static("", id="menu-status", markup=False)
            with Vertical(id="capture-panel"):
                yield Static("LATEST SCREENSHOT", classes="section")
                yield TerminalImage(id="shot-preview")
                yield Static("Capture a moment from this game. Screenshots stay private until you choose Publish Screenshot.",
                             id="shot-description", markup=False)
        yield Footer()

    async def on_mount(self):
        self.update_layout()
        await apply_saved_theme(self)
        if self.gamepad:
            # Guide over another game is the launcher's job, not the menu's.
            self.start_gamepad(None if self.gamepad is True else self.gamepad, guide_elsewhere=None)
        self.default_screen.query_one("#resume", Button).focus()
        self.check_game()
        endpoint = os.environ.get("KWAK_OVERLAY_SOCKET")
        if endpoint:
            self.control = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.control.bind(endpoint)
            os.chmod(endpoint, 0o600)
            self.control.setblocking(False)
            self.listen_control()

    def update_layout(self):
        self.default_screen.set_class(self.size.width < 72, "compact")

    def on_resize(self):
        self.update_layout()

    def update_snapshot(self):
        snapshot = self.snapshot or {}
        preview = self.default_screen.query_one("#shot-preview", TerminalImage)
        if preview.source != snapshot.get("path"):
            preview.set_source(snapshot.get("path"))
        if not snapshot:
            preview.query_one(".placeholder", Static).update("No screenshots yet")
        description = (snapshot.get("filename", "Screenshot saved") +
                       "\nChoose Publish Screenshot to share this moment." if snapshot else
                       "Capture a moment from this game. Screenshots stay private until you choose Publish Screenshot.")
        self.default_screen.query_one("#shot-description", Static).update(description)

    @work(group="control")
    async def listen_control(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                message = json.loads(await loop.sock_recv(self.control, 16384))
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            # Never cancel an action thread or change its target mid-operation.
            while any(worker.group in ("actions", "visibility") and worker.is_running
                      for worker in self.workers):
                await asyncio.sleep(0.01)
            if message.get("hide"):
                self.action_resume()
            elif isinstance(message.get("target"), dict):
                self.reopen(message["target"])

    @work(exclusive=True, group="visibility")
    async def reopen(self, target):
        while len(self.screen_stack) > 1:
            await self.pop_screen()
        self.desktop.target = target
        self.snapshot = None
        self.update_snapshot()
        self.default_screen.query_one("#current", Static).update(target.get("title") or "Game")
        self.default_screen.query_one("#menu-status", Static).update("")
        self.enable_menu(True)
        self.default_screen.query_one("#resume", Button).focus()
        try:
            await asyncio.to_thread(self.desktop.show_overlay)
            self.hidden = False
        except OverlayError as error:
            self.default_screen.query_one("#menu-status", Static).update(str(error))

    @work(exclusive=True, group="target")
    async def check_game(self):
        import copy
        desktop = copy.copy(self.desktop)
        target = dict(desktop.target)
        try:
            available = await asyncio.to_thread(desktop.game_available)
        except OverlayError:
            available = False
        snapshot = await asyncio.to_thread(self.screenshots.latest, target.get("napplet_address"))
        if self.desktop.target != target:
            return
        self.default_screen.query_one("#exit-game", Button).disabled = not available
        self.default_screen.query_one("#screenshot", Button).disabled = not available
        self.snapshot = snapshot
        self.update_snapshot()
        self.default_screen.query_one("#publish-screenshot", Button).disabled = snapshot is None

    def on_unmount(self):
        self.stop_gamepad()
        if self.control:
            endpoint = self.control.getsockname()
            self.control.close()
            Path(endpoint).unlink(missing_ok=True)

    def gamepad_guide(self):
        if self.screen is self.screen_stack[0]:
            self.action_resume()
        else:
            self.pop_screen()
        return True

    def action_resume(self):
        if not any(worker.group == "actions" and worker.is_running for worker in self.workers):
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
        self.default_screen.query_one("#menu-status", Static).update("Capturing game…")
        try:
            self.snapshot = await asyncio.to_thread(self.screenshots.capture, self.desktop)
            self.update_snapshot()
            self.default_screen.query_one("#menu-status", Static).update("Screenshot saved. Choose Publish Screenshot to share it.")
        except OverlayError as error:
            self.default_screen.query_one("#menu-status", Static).update(str(error))
        finally:
            self.enable_menu(True)

    @work(exclusive=True, group="actions")
    async def publish(self, snapshot, caption):
        self.enable_menu(False)
        self.default_screen.query_one("#menu-status", Static).update("Publishing screenshot…")
        try:
            await asyncio.to_thread(self.screenshots.publish, snapshot, caption)
            self.default_screen.query_one("#menu-status", Static).update("Screenshot published to your Nostr account.")
        except OverlayError as error:
            self.default_screen.query_one("#menu-status", Static).update(str(error))
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
        for button in self.default_screen.query(Button):
            button.disabled = True
        try:
            await asyncio.to_thread(actions[action])
        except OverlayError as error:
            self.default_screen.query_one("#menu-status", Static).update(str(error))
            for button in self.default_screen.query(Button):
                button.disabled = False
            self.check_game()
        else:
            if self.control:
                try:
                    await asyncio.to_thread(self.desktop.hide_overlay)
                    self.hidden = True
                except OverlayError as error:
                    self.default_screen.query_one("#menu-status", Static).update(str(error))
                self.enable_menu(True)
            else:
                self.exit()


def main():
    if "--launch" in sys.argv:
        from overlay_control import launch
        try:
            return launch(*sys.argv[2:4], DesktopActions())
        except OverlayError:
            return 1
    GameOverlay(gamepad=True).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
