"""Mouse and keyboard kiosk shell over kwakore's checked CLI transport."""
import asyncio
import fcntl
import hashlib
from pathlib import Path
import json
import os
import subprocess
from datetime import datetime, timezone

from rich.text import Text
from textual import work
from textual.app import App
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option
from settings import THEME, SettingsScreen
from library import Library
from discovery import Discovery, GameCard, artwork
from images import TerminalImage
from game_details import GameDetails


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

    def windows(self):
        return self.request("windows").get("items", [])

    def check_updates(self):
        return self.request("updates")

    def details(self, address):
        return self.request("detail", address)

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
    BINDINGS = []
    CATALOG_INTERVAL = 60
    UPDATE_INTERVAL = 300
    CSS = """
    Screen { background: $background; color: $foreground; }
    #shell { width: 100%; height: 100%; padding: 0; }
    #nav { height: 3; }
    Button { margin-right: 1; }
    #nav Button { width: 1fr; min-width: 0; height: 3; padding: 0 1; }
    #actions Button { min-width: 0; padding: 0 1; }
    #library-body { height: 1fr; }
    #games { width: 32%; min-width: 22; height: 100%; border: solid $primary; }
    #game-panel { width: 1fr; height: 100%; padding-left: 1; }
    #library-body.discover #game-panel { width: 30%; min-width: 26; max-width: 40; }
    #details { border: none; }
    #library-body.discover #actions { grid-size: 1; grid-rows: 3; }
    #library-body.discover #actions Button { height: 3; }
    #sync { height: auto; }
    #search, #discovery-search { margin: 0; }
    #details { height: 1fr; padding: 0 1; }
    #library-art { height: 5; width: 100%; background: $panel; }
    #game-title { height: auto; color: $primary; text-style: bold; padding: 0 1; }
    #game-state, #game-author { height: auto; padding: 0 1; }
    #game-author { color: $foreground 60%; }
    #game-version { height: auto; margin-bottom: 1; }
    #detail-tabs { height: 3; }
    #detail-tabs Button { width: 1fr; min-width: 0; padding: 0 1; margin: 0; }
    #detail { height: auto; margin-bottom: 1; }
    #actions { height: auto; layout: grid; grid-size: 3; grid-gutter: 0 1; }
    #actions Button { width: 100%; margin-right: 0; }

    #status { height: auto; color: $warning; }
    """

    def __init__(self, runtime=None, library=None):
        super().__init__()
        self.library = library if library is not None else (Library() if runtime is None else None)
        self.runtime = runtime or Runtime()
        self.page = "installed"
        self.games = []
        self.page_cache = {}
        self.catalog_error = False
        self.busy = set()
        self.installed = {}
        self.local_ready = False
        self.catalog = {}
        self.saved = []
        self.library_loaded = False
        self.failed = {}
        self.suggestions = []
        self.running = {}
        self.windows_available = True
        self.detail_address = None
        self.details = {}
        self._list_signature = None
        self._selection_signature = None
        self._library_art_source = None
        self.detail_tab = "info"
        self.register_theme(THEME)
        self.theme = "kwak"

    def compose(self):
        with Vertical(id="shell"):
            with Horizontal(id="nav"):
                yield Button("My Games", id="library", variant="primary")
                yield Button("Find Games", id="store")
                yield Button("Switch games", id="workspaces")
                yield Button("Settings", id="settings")
                yield Button("Session", id="session")
            yield Input(placeholder="Filter My Games…", id="search")
            yield Input(placeholder="Search Find Games…", id="discovery-search")
            yield Static("Checking library sync…" if self.library else "", id="sync", markup=False)
            with Horizontal(id="library-body"):
                yield OptionList(id="games")
                yield Discovery(id="discovery")
                with Vertical(id="game-panel"):
                    yield TerminalImage(id="library-art")
                    yield Static("Choose a game", id="game-title", markup=False)
                    yield Static("", id="game-author", markup=False)
                    yield Static("", id="game-state", markup=False)
                    with Vertical(id="actions"):
                        yield Button("Play", id="play", variant="primary", disabled=True)
                        yield Button("Stop", id="stop", disabled=True)
                        yield Button("Update", id="update", disabled=True)
                        yield Button("Install", id="install", disabled=True)
                        yield Button("Remove", id="remove", disabled=True)
                    with Horizontal(id="detail-tabs"):
                        yield Button("Game Info", id="info-tab", variant="primary")
                        yield Button("Releases", id="releases-tab")
                    with VerticalScroll(id="details"):
                        yield Static("", id="detail", markup=False)
                        yield Static("", id="game-version", markup=False)
            yield Static("Loading your games…", id="status", markup=False)

    def launcher_query_one(self, selector, expect_type=None):
        """Background workers always update the launcher, even below a modal."""
        return self._launcher_screen.query_one(selector, expect_type)

    def on_mount(self):
        self._launcher_screen = self.screen
        self.launcher_query_one("#discovery-search").display = False
        self.launcher_query_one("#discovery").display = False
        self.launcher_query_one("#library-art").display = False
        self.show_detail_tab()
        self.launcher_query_one("#sync").display = self.library is not None
        self.launcher_query_one("#games", OptionList).focus()
        self.load()
        self.poll_windows()
        self.set_interval(3, self.poll_windows)
        self.set_interval(self.CATALOG_INTERVAL, self.refresh_background)
        self.set_interval(self.UPDATE_INTERVAL, self.check_game_updates)
        if self.library:
            self.sync_library()
            self.set_interval(30, self.sync_library)

    @work(exclusive=True, group="windows")
    async def poll_windows(self):
        try:
            if not callable(getattr(self.runtime, "windows", None)):
                return
            windows = await asyncio.to_thread(self.runtime.windows)
            running = {}
            for window in windows:
                if isinstance(window, dict) and window.get("address") and window.get("window_id"):
                    running.setdefault(window["address"], []).append(window)
            changed = running != self.running or not self.windows_available
            self.running = running
            self.windows_available = True
        except (RuntimeError, TypeError, AttributeError):
            changed = bool(self.running) or self.windows_available
            self.running = {}
            self.windows_available = False
        if changed:
            self.render_games()

    def worker_running(self, group):
        return any(worker.group == group and not worker.is_finished for worker in self.workers)

    def refresh_background(self):
        if not self.is_running:
            return
        if self.screen is not self.screen_stack[0] or self.worker_running("catalog") or self.busy:
            return
        self.failed.clear()
        self.load(refresh=True, background=True)

    def check_game_updates(self):
        if not self.is_running:
            return
        if self.screen is not self.screen_stack[0]:
            return
        if not self.worker_running("update-check"):
            self._check_game_updates()

    @work(exclusive=True, group="update-check")
    async def _check_game_updates(self):
        if not callable(getattr(self.runtime, "check_updates", None)) or self.busy:
            return
        try:
            await asyncio.to_thread(self.runtime.check_updates)
        except RuntimeError:
            return  # Keep installed releases usable; retry on the next interval.
        self.details.clear()
        self.detail_address = None
        if not self.worker_running("catalog"):
            self.load(background=True)

    def show_sync(self, snapshot):
        self.saved = snapshot["addresses"]
        self.library_loaded = snapshot["loaded"]
        message = ("Library change waiting to sync" if snapshot["pending"] else
                   "" if snapshot["loaded"] else "Loading your saved library…")
        if snapshot.get("error"):
            message += " · " + snapshot["error"]
        name = snapshot.get("name")
        sync = self.launcher_query_one("#sync", Static)
        sync.update(" · ".join(part for part in (name, message) if part))
        sync.display = bool(name or message)

    @work(exclusive=True, group="sync")
    async def sync_library(self):
        try:
            snapshot = await asyncio.to_thread(self.library.sync)
        except RuntimeError as error:
            self.launcher_query_one("#sync", Static).update(str(error))
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
                self.launcher_query_one("#status", Static).update(str(error))
            return
        if self.recommending:
            self.rebuild_library()
            self.launcher_query_one("#status", Static).update("Your library is empty. Try a game below or add it to My Games.")

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
            self.launcher_query_one("#status", Static).update(str(error))
            return
        self.show_sync(snapshot)
        self.rebuild_library()
        self.ensure_downloads()
        self.sync_library()
        if self.recommending and not self.suggestions:
            self.load_suggestions()

    @work(exclusive=True, group="catalog")
    async def load(self, refresh=False, background=False):
        page = self.page
        if not background and not self.games:
            self.launcher_query_one("#status", Static).update("Loading games…")
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
            if not self.library and page == "installed":
                self.installed = {game["address"]: game for game in games}
                self.local_ready = True
            if self.recommending:
                self.suggestions = await asyncio.to_thread(self.runtime.listing, "discover", *(('--refresh',) if refresh else ()))
                self.catalog.update({game["address"]: game for game in self.suggestions})
        except RuntimeError as error:
            self.catalog_error = True
            self.launcher_query_one("#status", Static).update(
                "Games are temporarily unavailable. Retrying automatically." if background else str(error))
            return
        if not self.is_running:
            return
        recovered = self.catalog_error
        self.catalog_error = False
        self.page_cache[page] = games
        self.games = games
        self.catalog.update({game["address"]: game for game in games})
        self.rebuild_library()
        self.render_games()
        self.ensure_downloads()
        if not background and page == "installed":
            self.check_game_updates()
        if background and not recovered:
            return
        self.launcher_query_one("#status", Static).update(
            "Your library is empty. Try a game below or add it to My Games." if self.recommending else
            "" if self.games else
            "Loading your saved library. Find Games is available." if self.library and not self.library_loaded and page == "installed" else
            "No games yet. Choose Find Games to explore the catalog.")

    def active_search(self):
        return self.launcher_query_one("#discovery-search" if self.page == "discover" else "#search", Input)

    def render_games(self):
        if not self.is_running:
            return
        query = self.active_search().value.casefold()
        listing = self.launcher_query_one("#games", OptionList)
        address = (listing.get_option_at_index(listing.highlighted).id
                   if listing.highlighted is not None else None)
        scroll_y = listing.scroll_y
        rows = []
        discovering = self.page == "discover" or self.recommending
        listing.display = not discovering
        browser = self.launcher_query_one("#discovery", Discovery)
        browser.display = discovering
        self.launcher_query_one("#game-panel").display = not discovering
        self.launcher_query_one("#search").display = self.page != "discover"
        self.launcher_query_one("#discovery-search").display = self.page == "discover"
        self.launcher_query_one("#library-body").set_class(discovering, "discover")
        for game in self.games:
            if query not in game.get("name", "").casefold():
                continue
            status = "Downloading…" if game["address"] in self.busy else (
                "Ready to play" if self.page == "installed" and not self.recommending else "Play now · Install")
            if not game.get("available", False):
                status = "Unavailable"
            if self.library and self.page == "installed" and not self.recommending and game["address"] not in self.installed:
                status = "Download interrupted · Retrying automatically" if game["address"] in self.failed else "Downloading…"
            if game["address"] in self.running:
                status = "Running"
            rows.append((game | self.details.get(game["address"], {}), status))
        signature = tuple((game["address"], game.get("name") or "Untitled game", status)
                          for game, status in rows)
        if signature != self._list_signature:
            self._list_signature = signature
            # One invalidation for the whole catalog, rather than one per game.
            with listing.prevent(OptionList.OptionHighlighted):
                listing.clear_options()
                listing.add_options(Option(Text(f"{name}\n{status}"), id=game_address)
                                    for game_address, name, status in signature)
                listing.highlighted = next((index for index, row in enumerate(signature)
                                            if row[0] == address), 0 if signature else None)
                listing.scroll_to(y=scroll_y, animate=False)
        self.update_selection()
        selected = self.selected()
        browser.show_games(rows if discovering else [], selected["address"] if selected else None)
        if isinstance(self.screen, GameDetails):
            self.screen.update_page()

    def selected(self):
        listing = self.launcher_query_one("#games", OptionList)
        if listing.highlighted is None:
            return None
        address = listing.get_option_at_index(listing.highlighted).id
        return next((game for game in self.games if game["address"] == address), None)

    @work(exclusive=True, group="details")
    async def load_details(self, address):
        try:
            detail = await asyncio.to_thread(self.runtime.details, address)
        except RuntimeError:
            return
        if self.detail_address != address:
            return
        self.details[address] = detail
        self.update_selection()

    @staticmethod
    def release_label(version):
        if not isinstance(version, dict) or not version.get("event_id"):
            return "Unknown release"
        date = ""
        try:
            date = datetime.fromtimestamp(version["created_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + " · "
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            pass
        return date + str(version["event_id"])[:12]

    def update_selection(self):
        game = self.selected()
        address = game["address"] if game else None
        if address != self.detail_address:
            self.launcher_query_one("#details", VerticalScroll).scroll_home(animate=False)
            self.detail_address = address
            if address and address not in self.details and callable(getattr(self.runtime, "details", None)):
                self.load_details(address)
        if game:
            game = game | self.details.get(address, {})
        signature = (game, self.page, self.recommending, bool(self.library),
                     address in self.busy, self.installed.get(address), address in self.saved,
                     self.running.get(address, []), self.windows_available)
        if signature == self._selection_signature:
            return
        # Store copies: daemon snapshots are replaced, but callers may mutate lists.
        self._selection_signature = (dict(game) if game else None, *signature[1:-2],
                                     list(signature[-2]), signature[-1])
        enabled = bool(game and game.get("available") and game["address"] not in self.busy)
        if self.library and self.page == "installed" and not self.recommending and game and game["address"] not in self.installed:
            enabled = False
        self.launcher_query_one("#play", Button).label = "Play" if self.page == "installed" and not self.recommending else "Play Now"
        self.launcher_query_one("#play", Button).disabled = not enabled
        self.launcher_query_one("#install", Button).label = "Install" if self.page == "discover" or not self.library else "Add to My Games"
        self.launcher_query_one("#install", Button).disabled = (not enabled or (self.page != "discover" and not self.recommending) or
                                                       bool(self.library and game and game["address"] in self.saved))
        self.launcher_query_one("#install", Button).display = self.page == "discover" or self.recommending
        discovering = self.page == "discover" or self.recommending
        for control in ("stop", "update", "remove"):
            self.launcher_query_one(f"#{control}", Button).display = not discovering
        self.show_detail_tab()
        self.launcher_query_one("#remove", Button).disabled = not bool(self.library and game and game["address"] in self.saved)
        self.launcher_query_one("#remove", Button).display = bool(self.library and game and game["address"] in self.saved)
        installed = bool(game and game["address"] in self.installed)
        active = self.running.get(game["address"], []) if game else []
        self.launcher_query_one("#stop", Button).display = not discovering and bool(active)
        self.launcher_query_one("#stop", Button).disabled = not bool(active) or bool(game and game["address"] in self.busy)
        update_available = bool(game and (game.get("update_available") or self.installed.get(address, {}).get("update_available")))
        self.launcher_query_one("#update", Button).label = "Update"
        self.launcher_query_one("#update", Button).display = not discovering and update_available
        self.launcher_query_one("#update", Button).disabled = not installed or not update_available or bool(game and game["address"] in self.busy)
        self.launcher_query_one("#game-title", Static).update(game.get("name") or "Untitled game" if game else "Choose a game")
        creator = (game.get("author_name") or game.get("author")) if game else None
        author = self.launcher_query_one("#game-author", Static)
        author.display = bool(creator)
        author.update("By " + str(creator) if creator else "")
        # A landscape banner is the best match for the compact hero; retain
        # the shared HTTPS validator and other metadata artwork fallbacks.
        source = ((artwork({"image": game.get("banner")}) or artwork(game))
                  if game and not discovering else None)
        image = self.launcher_query_one("#library-art", TerminalImage)
        image.display = bool(source)
        if source != self._library_art_source:
            self._library_art_source = source
            # The widget initializes its placeholder after the first refresh.
            # Queue behind that initialization and reject superseded selections.
            image.call_after_refresh(self.apply_library_art, source)
        state = (f"Running · {len(active)} instance(s)" if active else
                 "Downloading…" if game and game["address"] in self.busy else
                 "Unavailable" if game and not game.get("available") else "Installed · Ready to play" if installed else "Available to try or install") if game else ""
        if game and not game.get("available"):
            state = game.get("unavailable_reason") or "Unavailable on this system"
        if game and not self.windows_available:
            state += " · Running status unavailable"
        self.launcher_query_one("#game-state", Static).update(state)
        version_text = ""
        if game:
            installed_game = self.installed.get(address, {})
            version = game.get("installed_version") or installed_game.get("version")
            if installed:
                version_text = "Installed release: " + self.release_label(version)
                update = game.get("update_available") or installed_game.get("update_available")
                if update:
                    version_text += "\nUpdate available: " + self.release_label(update)
                version_text += "\nInstalled version stays in use until you choose Update."
            versions = game.get("versions", [])
            if versions:
                version_text += "\n\nKnown releases\n" + "\n".join(self.release_label(item) for item in versions)
                version_text += "\nRelease selection is not available yet."
        self.launcher_query_one("#game-version", Static).update(version_text or "No release information is available yet.")
        self.launcher_query_one("#detail", Static).update(game.get("description") or "No description available yet." if game else
                                                "Select a game from your library to see its details and controls.")

    def apply_library_art(self, source):
        if self.is_running and source == self._library_art_source:
            self.launcher_query_one("#library-art", TerminalImage).set_source(source)

    def show_detail_tab(self):
        """Keep both detail sections mounted so polls preserve tab and scroll."""
        info = self.detail_tab == "info"
        self.launcher_query_one("#detail").display = info
        self.launcher_query_one("#game-version").display = not info
        self.launcher_query_one("#info-tab", Button).variant = "primary" if info else "default"
        self.launcher_query_one("#releases-tab", Button).variant = "default" if info else "primary"

    def on_input_changed(self, event):
        if event.input.id in ("search", "discovery-search") and event.input.screen is self._launcher_screen:
            self.render_games()

    def on_input_submitted(self, event):
        if event.input.id not in ("search", "discovery-search") or event.input.screen is not self._launcher_screen:
            return
        if self.page == "discover" or self.recommending:
            self.launcher_query_one(Discovery).focus_selected()
        else:
            self.launcher_query_one("#games", OptionList).focus()

    def on_option_list_option_highlighted(self):
        self.update_selection()
        game = self.selected()
        self.launcher_query_one(Discovery).select(game["address"] if game else None)

    def on_game_card_chosen(self, event):
        event.stop()
        listing = self.launcher_query_one("#games", OptionList)
        for index in range(listing.option_count):
            if listing.get_option_at_index(index).id == event.card.game["address"]:
                listing.highlighted = index
                break
        # Highlighting emits the selection event; avoid rendering details twice.
        if event.activate and not isinstance(self.screen, GameDetails):
            self.push_screen(GameDetails(event.card.game))

    def on_option_list_option_selected(self):
        self.update_selection()

    def start_game(self):
        game = self.selected()
        if game and game.get("available") and game["address"] not in self.busy and (
                not self.library or self.page != "installed" or self.recommending or game["address"] in self.installed):
            self.mutate("launch" if self.page == "installed" and not self.recommending else "try", game["address"])

    @work(group="stopping")
    async def stop_selected(self, address, instance_ids):
        self.busy.add(address)
        self.render_games()
        try:
            for instance_id in instance_ids:
                await asyncio.to_thread(self.runtime.request, "stop", instance_id)
        except RuntimeError as error:
            self.launcher_query_one("#status", Static).update(str(error))
        else:
            self.launcher_query_one("#status", Static).update("Game stopped.")
        finally:
            self.busy.discard(address)
            self.poll_windows()
            self.render_games()

    @work(group="operations")
    async def mutate(self, command, address):
        self.busy.add(address)
        self.render_games()
        self.launcher_query_one("#status", Static).update("Checking updates…" if command == "updates" else "Updating game…" if command == "update" else "Downloading…" if command in ("install", "try") else "Opening game…")
        try:
            await asyncio.to_thread(self.runtime.request, command, address)
        except RuntimeError as error:
            if command == "install":
                self.failed[address] = str(error)
            self.launcher_query_one("#status", Static).update(str(error))
        else:
            if command == "install" and self.library:
                self.installed[address] = self.catalog.get(address) or {
                    "address": address, "name": address.split(":", 2)[-1] or "Game", "available": True}
            if command in ("install", "update", "updates"):
                self.details.pop(address, None)
                self.detail_address = None
                try:
                    local = await asyncio.to_thread(self.runtime.listing, "installed")
                except RuntimeError as error:
                    self.launcher_query_one("#status", Static).update("Action completed. Library refresh failed: " + str(error))
                    return
                refreshed = {game["address"]: game for game in local}
                self.installed = (self.installed | refreshed) if command == "install" else refreshed
                if self.page == "installed" and not self.library:
                    self.games = local
            self.launcher_query_one("#status", Static).update(("Update available." if self.installed.get(address, {}).get("update_available") else "Up to date.") if command == "updates" else "Game updated." if command == "update" else "Installed. Find it in My Games." if command == "install" else "Game opened.")
            self.poll_windows()
        finally:
            self.busy.discard(address)
            self.rebuild_library()
            self.render_games()

    def on_button_pressed(self, event):
        action = event.button.id
        if action in ("info-tab", "releases-tab"):
            self.detail_tab = "info" if action == "info-tab" else "releases"
            self.show_detail_tab()
            self.launcher_query_one("#details", VerticalScroll).scroll_home(animate=False)
        elif action in ("library", "store"):
            self.page = "installed" if action == "library" else "discover"
            self.launcher_query_one("#library", Button).variant = "primary" if action == "library" else "default"
            self.launcher_query_one("#store", Button).variant = "primary" if action == "store" else "default"
            self.games = self.page_cache.get(self.page, [])
            self.render_games()
            self.load(refresh=self.page == "discover", background=bool(self.games))
            if self.page == "installed" and self.games:
                self.check_game_updates()
        elif action in ("play", "stop", "update", "install", "remove"):
            game = self.selected()
            if game:
                self.perform_game_action(action, game)
        elif action == "settings":
            self.push_screen(SettingsScreen(runtime=self.runtime, local=self.library is None, initial="overview"))
        elif action in ("session", "workspaces"):
            commands = {"session": ["kwak-session"],
                        "workspaces": ["hyprctl", "eval", "hl.plugin.hyprflow.toggle()"]}
            self.system_action(commands[action])

    def perform_game_action(self, action, game):
        address = game["address"]
        if address in self.busy:
            return
        if action == "play" and game.get("available"):
            self.mutate("launch" if address in self.installed else "try", address)
        elif action == "stop":
            instances = self.running.get(address, [])
            if instances:
                self.stop_selected(address, tuple(window["window_id"] for window in instances))
        elif action == "update" and address in self.installed:
            self.mutate("update", address)
        elif action == "install" and game.get("available") and address not in self.installed:
            if self.library:
                self.change_library(address, "add")
            else:
                self.mutate("install", address)
        elif action == "remove" and self.library and address in self.saved:
            self.push_screen(RemoveGame(), lambda confirmed: self.change_library(address, "remove") if confirmed else None)

    @work(group="system")
    async def system_action(self, command):
        try:
            result = await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=180)
            if result.returncode:
                raise OSError()
        except (OSError, subprocess.TimeoutExpired):
            self.launcher_query_one("#status", Static).update("This system action is unavailable. Please retry.")


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
