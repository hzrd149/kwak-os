"""Mouse and keyboard kiosk shell over kwakore's checked CLI transport."""
import asyncio
import fcntl
import hashlib
from pathlib import Path
import json
import os
import subprocess

from rich.text import Text
from textual import work
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option
from settings import THEME, SettingsScreen
from library import Library


class RemoveGame(ModalScreen):
    CSS = "RemoveGame { align: center middle; } #confirm { width: 64; height: auto; padding: 2; border: solid $primary; background: $surface; }"
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self):
        with Vertical(id="confirm"):
            yield Static("Remove this game from My Games on every kiosk? Local cached files may remain.")
            yield Button("Cancel", id="cancel", variant="primary")
            yield Button("Remove from My Games", id="confirm-remove")

    def on_mount(self):
        self.query_one("#cancel", Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "confirm-remove")


class Runtime:
    def __init__(self, run=subprocess.run, lock_root=None):
        self.run = run
        root = Path(os.environ.get("XDG_RUNTIME_DIR") or
                    Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kwak")
        self.lock_root = Path(lock_root) if lock_root is not None else root / "install-locks"

    def request(self, *args):
        if args and args[0] == "install" and len(args) == 2:
            # Library and overlay share a daemon but have separate busy sets.
            # Serialize each address, then check the first interface's result.
            # This runs in the caller's to_thread worker, never the UI loop.
            path = self.lock_root / hashlib.sha256(args[1].encode()).hexdigest()
            try:
                self.lock_root.mkdir(parents=True, mode=0o700, exist_ok=True)
                with open(path, "a", opener=lambda name, flags: os.open(name, flags, 0o600)) as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    if any(game["address"] == args[1] for game in self.listing("installed")):
                        return {"already_installed": True}
                    return self._request(*args)
            except OSError as error:
                raise RuntimeError("Cannot coordinate game downloads. Please retry.") from error
        return self._request(*args)

    def _request(self, *args):
        try:
            result = self.run([os.environ.get("KWAK_CLI", "kwak"), "--json", *args],
                              capture_output=True, text=True, timeout=190, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Game service unavailable. Please retry.") from error
        if result.returncode:
            raise RuntimeError("The game service could not complete this action. Retry or check Settings.")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError("The game service returned an invalid response.") from error

    def listing(self, command, *args):
        items, offset = [], 0
        while True:
            page = self.request(command, *args, "--offset", str(offset), "--limit", "100")
            items.extend(page["items"])
            next_offset = page.get("next_offset")
            if next_offset is None:
                return items
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise RuntimeError("The game service returned an invalid page.")
            offset = next_offset


class Kiosk(App):
    TITLE = "kwakOS · Games"
    BINDINGS = [("ctrl+r", "refresh", "Refresh")]
    CSS = """
    Screen { background: $background; color: $foreground; }
    #shell { width: 100%; height: 100%; padding: 1 3; }
    #nav, #actions { height: 3; }
    Button { margin-right: 1; }
    #nav Button { width: 1fr; min-width: 0; padding: 0 1; }
    #actions Button { min-width: 0; padding: 0 1; }
    #games { height: 1fr; border: solid $primary; }
    #detail { height: 4; padding: 1; }
    #status { height: 2; color: $warning; }
    #brand { height: 2; color: $primary; text-style: bold; }
    """

    def __init__(self, runtime=None, library=None):
        super().__init__()
        self.library = library if library is not None else (Library() if runtime is None else None)
        self.runtime = runtime or Runtime()
        self.page = "installed"
        self.games = []
        self.busy = set()
        self.installed = {}
        self.local_ready = False
        self.catalog = {}
        self.saved = []
        self.library_loaded = False
        self.failed = {}
        self.suggestions = []
        self.register_theme(THEME)
        self.theme = "kwak"

    def compose(self):
        with Vertical(id="shell"):
            yield Static("KWAK OS  /  PLAY", id="brand")
            with Horizontal(id="nav"):
                yield Button("My Games", id="library", variant="primary")
                yield Button("Find Games", id="store")
                yield Button("Switch games", id="workspaces")
                yield Button("Settings", id="settings")
                yield Button("Session", id="session")
            yield Input(placeholder="Search games…", id="search")
            yield Static("Checking library sync…" if self.library else "", id="sync", markup=False)
            yield OptionList(id="games")
            yield Static("", id="detail", markup=False)
            with Horizontal(id="actions"):
                yield Button("Play", id="play", variant="primary", disabled=True)
                yield Button("Install", id="install", disabled=True)
                yield Button("Remove", id="remove", disabled=True)
                yield Button("Refresh", id="refresh")
            yield Static("Loading your games…", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        self.query_one("#games", OptionList).focus()
        self.load()
        if self.library:
            self.sync_library()
            self.set_interval(30, self.sync_library)

    def action_refresh(self):
        self.failed.clear()
        self.load(refresh=True)
        if self.library:
            self.sync_library()

    def show_sync(self, snapshot):
        self.saved = snapshot["addresses"]
        self.library_loaded = snapshot["loaded"]
        message = ("Library change waiting to sync" if snapshot["pending"] else
                   "Library synced" if snapshot["loaded"] else "Loading your saved library…")
        if snapshot.get("error"):
            message += " · " + snapshot["error"]
        name = snapshot.get("name")
        self.query_one("#sync", Static).update((name + " · " if name else "") + message)

    @work(exclusive=True, group="sync")
    async def sync_library(self):
        try:
            snapshot = await asyncio.to_thread(self.library.sync)
        except RuntimeError as error:
            self.query_one("#sync", Static).update(str(error))
            return
        self.show_sync(snapshot)
        # Another interface can finish an install while this overlay is open.
        # Reconcile files independently of relay availability.
        before = set(self.installed)
        try:
            local = await asyncio.to_thread(self.runtime.listing, "installed")
        except RuntimeError:
            pass
        else:
            completed = {address: game for address, game in self.installed.items() if address not in before}
            self.installed = {game["address"]: game for game in local} | completed
            self.local_ready = True
            for address in self.installed:
                self.failed.pop(address, None)
        self.rebuild_library()
        self.ensure_downloads()
        if self.recommending and not self.suggestions:
            self.load_suggestions()

    @work(exclusive=True, group="suggestions")
    async def load_suggestions(self):
        try:
            self.suggestions = await asyncio.to_thread(self.runtime.listing, "discover")
            self.catalog.update({game["address"]: game for game in self.suggestions})
        except RuntimeError as error:
            if self.recommending:
                self.query_one("#status", Static).update(str(error))
            return
        if self.recommending:
            self.rebuild_library()
            self.query_one("#status", Static).update("Your library is empty. Try a game below or add it to My Games.")

    def rebuild_library(self):
        if self.library and self.page == "installed":
            self.games = [self.installed.get(address) or self.catalog.get(address) or
                          {"address": address, "name": address.split(":", 2)[-1] or "Game", "available": True}
                          for address in self.saved]
            if self.recommending:
                self.games = self.suggestions
            self.games.sort(key=lambda game: (game["address"] not in self.installed, game.get("name", "").casefold()))
            self.render_games()

    @property
    def recommending(self):
        return bool(self.library and self.library_loaded and not self.saved and self.page == "installed")

    def ensure_downloads(self):
        if not self.library or not self.local_ready:
            return
        for address in self.saved:
            if address not in self.installed and address not in self.busy and address not in self.failed:
                self.busy.add(address)
                self.mutate("install", address)

    @work(group="library-changes")
    async def change_library(self, address, action):
        try:
            snapshot = await asyncio.to_thread(self.library.change, address, action)
        except RuntimeError as error:
            self.query_one("#status", Static).update(str(error))
            return
        self.show_sync(snapshot)
        self.rebuild_library()
        self.ensure_downloads()
        self.sync_library()
        if self.recommending and not self.suggestions:
            self.load_suggestions()

    @work(exclusive=True, group="catalog")
    async def load(self, refresh=False):
        page = self.page
        self.query_one("#status", Static).update("Loading games…")
        try:
            if self.library:
                self.show_sync(await asyncio.to_thread(self.library.snapshot))
                self.rebuild_library()
                before = set(self.installed)
                local = await asyncio.to_thread(self.runtime.listing, "installed")
                completed = {address: game for address, game in self.installed.items() if address not in before}
                self.installed = {game["address"]: game for game in local} | completed
                self.local_ready = True
            args = ("--refresh",) if refresh and page == "discover" else ()
            games = (local if self.library and page == "installed" else
                     await asyncio.to_thread(self.runtime.listing, page, *args))
            if self.recommending:
                self.suggestions = await asyncio.to_thread(self.runtime.listing, "discover", *(('--refresh',) if refresh else ()))
                self.catalog.update({game["address"]: game for game in self.suggestions})
        except RuntimeError as error:
            self.query_one("#status", Static).update(str(error))
            return
        self.games = games
        self.catalog.update({game["address"]: game for game in games})
        self.rebuild_library()
        self.render_games()
        self.ensure_downloads()
        self.query_one("#status", Static).update(
            "Your library is empty. Try a game below or add it to My Games." if self.recommending else
            "" if self.games else
            "Loading your saved library. Find Games is available." if self.library and not self.library_loaded and page == "installed" else
            "No games yet. Choose Find Games to explore the catalog.")

    def render_games(self):
        query = self.query_one("#search", Input).value.casefold()
        listing = self.query_one("#games", OptionList)
        address = (listing.get_option_at_index(listing.highlighted).id
                   if listing.highlighted is not None else None)
        listing.clear_options()
        for game in self.games:
            if query not in game.get("name", "").casefold():
                continue
            status = "Downloading…" if game["address"] in self.busy else (
                "Ready to play" if self.page == "installed" and not self.recommending else "Play now · Add to My Games")
            if not game.get("available", False):
                status = "Unavailable"
            if self.library and self.page == "installed" and not self.recommending and game["address"] not in self.installed:
                status = "Download failed · Refresh to retry" if game["address"] in self.failed else "Downloading…"
            listing.add_option(Option(Text(f"{game.get('name') or 'Untitled game'}   /   {status}"), id=game["address"]))
            if game["address"] == address:
                listing.highlighted = listing.option_count - 1
        self.update_selection()

    def selected(self):
        listing = self.query_one("#games", OptionList)
        if listing.highlighted is None:
            return None
        address = listing.get_option_at_index(listing.highlighted).id
        return next((game for game in self.games if game["address"] == address), None)

    def update_selection(self):
        game = self.selected()
        enabled = bool(game and game.get("available") and game["address"] not in self.busy)
        if self.library and self.page == "installed" and not self.recommending and game and game["address"] not in self.installed:
            enabled = False
        self.query_one("#play", Button).label = "Play" if self.page == "installed" and not self.recommending else "Play Now"
        self.query_one("#play", Button).disabled = not enabled
        self.query_one("#install", Button).label = "Add to My Games" if self.library else "Install"
        self.query_one("#install", Button).disabled = (not enabled or (self.page != "discover" and not self.recommending) or
                                                       bool(self.library and game and game["address"] in self.saved))
        self.query_one("#remove", Button).disabled = not bool(self.library and game and game["address"] in self.saved)
        self.query_one("#detail", Static).update(game.get("description", "") if game else "")

    def on_input_changed(self):
        self.render_games()

    def on_input_submitted(self):
        self.query_one("#games", OptionList).focus()

    def on_option_list_option_highlighted(self):
        self.update_selection()

    def on_option_list_option_selected(self):
        self.start_game()

    def start_game(self):
        game = self.selected()
        if game and game.get("available") and game["address"] not in self.busy and (
                not self.library or self.page != "installed" or self.recommending or game["address"] in self.installed):
            self.mutate("launch" if self.page == "installed" and not self.recommending else "try", game["address"])

    @work(group="operations")
    async def mutate(self, command, address):
        self.busy.add(address)
        self.render_games()
        self.query_one("#status", Static).update("Downloading…" if command in ("install", "try") else "Opening game…")
        try:
            await asyncio.to_thread(self.runtime.request, command, address)
        except RuntimeError as error:
            if command == "install":
                self.failed[address] = str(error)
            self.query_one("#status", Static).update(str(error))
        else:
            if command == "install" and self.library:
                self.installed[address] = self.catalog.get(address) or {
                    "address": address, "name": address.split(":", 2)[-1] or "Game", "available": True}
            self.query_one("#status", Static).update("Installed. Find it in My Games." if command == "install" else "Game opened.")
        finally:
            self.busy.discard(address)
            self.rebuild_library()
            self.render_games()

    def on_button_pressed(self, event):
        action = event.button.id
        if action in ("library", "store"):
            self.page = "installed" if action == "library" else "discover"
            self.query_one("#library", Button).variant = "primary" if action == "library" else "default"
            self.query_one("#store", Button).variant = "primary" if action == "store" else "default"
            self.games = []
            self.render_games()
            self.load(refresh=self.page == "discover")
        elif action == "refresh":
            self.action_refresh()
        elif action == "play":
            self.start_game()
        elif action == "install":
            game = self.selected()
            if game:
                if self.library:
                    self.change_library(game["address"], "add")
                else:
                    self.mutate("install", game["address"])
        elif action == "remove":
            game = self.selected()
            if game:
                address = game["address"]
                self.push_screen(RemoveGame(), lambda confirmed: self.change_library(address, "remove") if confirmed else None)
        elif action == "settings":
            self.push_screen(SettingsScreen(runtime=self.runtime, local=self.library is None, initial="overview"))
        elif action in ("session", "workspaces"):
            commands = {"session": ["kwak-session"],
                        "workspaces": ["hyprctl", "eval", "hl.plugin.hyprflow.toggle()"]}
            self.system_action(commands[action])

    @work(group="system")
    async def system_action(self, command):
        try:
            result = await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=180)
            if result.returncode:
                raise OSError()
        except (OSError, subprocess.TimeoutExpired):
            self.query_one("#status", Static).update("This system action is unavailable. Please retry.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="kwakOS game library")
    parser.add_argument("--local", action="store_true",
                        help="Use this user's kwakore library without kiosk account sync")
    parser.add_argument("--overlay", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.overlay:
        from overlay import GameOverlay
        GameOverlay().run()
    else:
        Kiosk(runtime=Runtime() if args.local else None).run()
