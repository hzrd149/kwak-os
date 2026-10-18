"""Mouse, keyboard and controller kiosk shell over kwakore's checked CLI transport."""
import asyncio
import fcntl
import hashlib
from pathlib import Path
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from textual import work
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static
from settings import THEME, SettingsScreen, apply_saved_theme
from library import Library
from discovery import Discovery, GameCard
from game_details import GameDetails
from hints import ButtonHints
from icons import IconStore
from game_menu import GameMenu
from navigation import GamepadMixin
from osk import OnScreenKeyboard
from shelves import ShelfView
from shell_state import Activity
import browse


class RemoveGame(ModalScreen):
    CSS = "RemoveGame { align: center middle; } #confirm { width: 64; height: auto; padding: 2; border: solid $primary; background: $surface; }"
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self):
        with Vertical(id="confirm"):
            yield Static("Uninstall this game here and on devices sharing this library? Running game windows will close.")
            yield Button("Cancel", id="cancel", variant="primary")
            yield Button("Remove from My Games", id="confirm-remove")

    def on_mount(self):
        self.query_one("#cancel", Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "confirm-remove")


class EventStream:
    """Iterates JSON event lines; ends when the service ends the stream."""

    def __init__(self, command, spawn=subprocess.Popen):
        try:
            self.process = spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True)
        except OSError as error:
            raise RuntimeError("Game service events unavailable.") from error

    def __iter__(self):
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("type"), str):
                yield event

    def close(self):
        # Cancelling the reading worker does not end the child process.
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


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

    @staticmethod
    def timeout(args):
        command = args[0] if args else ""
        if command in ("status", "diagnostics", "settings", "installed", "detail", "windows", "permissions"):
            return 8
        if command in ("discover", "versions", "snapshots"):
            # A relay refresh has a 30 second lookup deadline in kwakore.
            return 40 if "--refresh" in args else 8
        if command == "icon":
            return 30
        return 190

    def _request(self, *args):
        try:
            result = self.run([os.environ.get("KWAK_CLI", "kwak"), "--json", *args],
                              capture_output=True, text=True,
                              timeout=self.timeout(args), check=False)
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

    def discover(self, query=None, archetype=None, compatible=None, offset=0, limit=100, refresh=False):
        """One catalog page with kwakore's own filters applied."""
        args = ["discover", "--offset", str(offset), "--limit", str(limit)]
        if query:
            args += ["--query", query]
        if archetype:
            args += ["--archetype", archetype]
        if compatible is not None:
            args.append("--compatible" if compatible else "--compatible=false")
        if refresh:
            args.append("--refresh")
        return self.request(*args)

    def icon(self, address):
        """The verified icon; with --json kwakore returns base64 PNG data."""
        return self.request("icon", address)

    def versions(self, address, refresh=False):
        return self.request("versions", address, *(("--refresh",) if refresh else ()))

    def snapshots(self, address, refresh=False):
        return self.request("snapshots", address, *(("--refresh",) if refresh else ()))

    def select(self, address, event_id):
        return self.request("select", address, event_id)

    def permissions(self, address):
        return self.request("permissions", "get", address)

    def set_permission(self, address, permission, decision, subject=""):
        return self.request("permissions", "set", address, permission, decision,
                            *(("--subject", subject) if subject else ()))

    def clear_permission(self, address, permission, subject=""):
        return self.request("permissions", "clear", address, permission,
                            *(("--subject", subject) if subject else ()))

    def events(self, types, spawn=subprocess.Popen):
        """Follow kwakore's service events; the caller must close() the stream."""
        return EventStream([os.environ.get("KWAK_CLI", "kwak"), "--json", "events",
                            *(arg for kind in types for arg in ("--type", kind))], spawn)

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


class Kiosk(GamepadMixin, App):
    TITLE = "kwakOS · Games"
    BINDINGS = []
    CATALOG_INTERVAL = 60
    UPDATE_INTERVAL = 300
    SEARCH_DELAY = 0.3
    PAGE_SIZE = 48
    # With a healthy event stream, window polling is only a safety net.
    EVENT_POLL_INTERVAL = 30
    EVENT_TYPES = ("window.opened", "window.closed", "napplet.installed", "napplet.updated",
                   "napplet.uninstalled", "updates.available", "sync.completed")
    # L1/R1 walk these in order, like Steam's library and store tabs.
    VIEWS = (("installed", "home"), ("installed", "all"), ("discover", "home"), ("discover", "browse"))
    CSS = """
    Screen { background: $background; color: $foreground; }
    #shell { width: 100%; height: 100%; padding: 0; }
    #nav { height: 3; }
    Button { margin-right: 1; }
    #nav Button { width: 1fr; min-width: 0; height: 3; padding: 0 1; }
    .toolbar { height: 1; margin: 0 0 1 0; }
    .toolbar Button { height: 1; width: auto; min-width: 0; border: none; padding: 0 1; margin: 0 1 0 0; }
    .toolbar Button.active { background: $primary 25%; color: $primary; text-style: bold; }
    .toolbar Input { height: 1; width: 1fr; min-width: 12; border: none; padding: 0 1; margin: 0 1 0 0; background: $panel; }
    .toolbar Input:focus { background: $primary 15%; }
    #library-body { height: 1fr; }
    #sync { height: auto; }
    #status { height: auto; color: $warning; }
    """

    def __init__(self, runtime=None, library=None, local=None, gamepad=None, activity=None, icons=None):
        super().__init__()
        self.gamepad = gamepad
        self.local = runtime is not None if local is None else local
        self.runtime = runtime or Runtime()
        self.library = library if library is not None else (Library(self.runtime) if runtime is None else None)
        # Tests and embedded uses keep activity in memory unless given a store.
        self.activity = activity if activity is not None else Activity(False)
        self.icons = icons if icons is not None else IconStore(self.runtime)
        self.page = "installed"
        self.views = {"installed": "home", "discover": "home"}
        self.sort = "name"
        self.show = "all"
        self.category = None
        self.compatible_only = False
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
        self.selected_address = None
        self.detail_address = None
        self.details = {}
        self.search_results = None
        self.search_next = None
        self.search_revision = 0
        self.search_timer = None
        self.sync_revision = 0
        self.events_healthy = False
        self.event_stream = None
        self.events_stop = threading.Event()
        self.last_window_poll = 0.0
        self.reload_timer = None
        self._initial_grid_focus_pending = True
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
            with Horizontal(id="library-toolbar", classes="toolbar"):
                yield Button("Home", id="library-home", classes="view active")
                yield Button("All games", id="library-all", classes="view")
                yield Input(placeholder="Filter My Games…", id="search")
                yield Button("Sort: Name", id="library-sort")
                yield Button("Show: All", id="library-show")
            with Horizontal(id="store-toolbar", classes="toolbar"):
                yield Button("Home", id="store-home", classes="view active")
                yield Button("Browse", id="store-browse", classes="view")
                yield Input(placeholder="Search Find Games…", id="discovery-search")
                yield Button("Category: All", id="store-category")
                yield Button("Runs here: Any", id="store-compatible")
            yield Static("Checking library sync…" if self.library else "", id="sync", markup=False)
            with Horizontal(id="library-body"):
                yield ShelfView(empty="No games to show yet.", id="shelves")
                yield Discovery(id="discovery")
            yield Static("Loading your games…", id="status", markup=False)
            yield ButtonHints(id="hints")

    def launcher_query_one(self, selector, expect_type=None):
        """Background workers always update the launcher, even below a modal."""
        return self._launcher_screen.query_one(selector, expect_type)

    async def on_mount(self):
        self._launcher_screen = self.screen
        await apply_saved_theme(self)
        self.launcher_query_one("#store-toolbar").display = False
        self.launcher_query_one("#sync").display = self.library is not None
        self.launcher_query_one("#search", Input).focus()
        self.show_view()
        if self.gamepad:
            self.start_gamepad(None if self.gamepad is True else self.gamepad)
        self.load()
        self.poll_windows()
        self.set_interval(3, self.poll_tick)
        if callable(getattr(self.runtime, "events", None)):
            self.follow_events()
        self.set_interval(self.CATALOG_INTERVAL, self.refresh_background)
        self.set_interval(self.UPDATE_INTERVAL, self.check_game_updates)
        if self.library:
            self.sync_library()
            self.set_interval(30, self.sync_library)

    def on_unmount(self):
        self.stop_gamepad()
        self.events_stop.set()
        stream = self.event_stream
        if stream is not None:
            stream.close()

    # Views -----------------------------------------------------------------

    def current_view(self):
        """Typing a query or an empty library always shows the grid."""
        if self.page == "installed":
            if self.recommending or self.active_search().value.strip():
                return "all"
            return self.views["installed"]
        if self.active_search().value.strip() or self.category or self.compatible_only:
            return "browse"
        return self.views["discover"]

    def browser(self):
        return self.launcher_query_one(ShelfView if self.current_view() == "home" else Discovery)

    def show_view(self):
        view = self.current_view()
        installed = self.page == "installed"
        self.launcher_query_one("#library-toolbar").display = installed
        self.launcher_query_one("#store-toolbar").display = not installed
        self.launcher_query_one(ShelfView).display = view == "home"
        self.launcher_query_one(Discovery).display = view != "home"
        for button_id, active in (("library-home", view == "home"), ("library-all", view == "all"),
                                  ("store-home", view == "home"), ("store-browse", view == "browse")):
            self.launcher_query_one("#" + button_id, Button).set_class(active, "active")
        self.launcher_query_one("#library-sort", Button).label = "Sort: " + browse.label(browse.SORTS, self.sort)
        self.launcher_query_one("#library-show", Button).label = "Show: " + browse.label(
            browse.show_options(self.activity.collections), self.show)
        self.launcher_query_one("#library-sort").display = view == "all" and not self.recommending
        self.launcher_query_one("#library-show").display = view == "all" and not self.recommending
        self.launcher_query_one("#store-category", Button).label = "Category: " + (self.category or "All")
        self.launcher_query_one("#store-compatible", Button).label = (
            "Runs here: Only" if self.compatible_only else "Runs here: Any")

    def set_view(self, page, view):
        if page != self.page:
            self.launcher_query_one("#library" if page == "installed" else "#store", Button).press()
        self.views[page] = view
        if page == "discover" and view == "home":
            self.category = None
            self.compatible_only = False
            self.launcher_query_one("#discovery-search", Input).value = ""
            self.search_results = None
        self.render_games()
        self.browser().request_focus_selected()

    def cycle_view(self, delta):
        current = (self.page, self.current_view())
        index = self.VIEWS.index(current) if current in self.VIEWS else 0
        self.set_view(*self.VIEWS[(index + delta) % len(self.VIEWS)])

    # Controller --------------------------------------------------------------

    def gamepad_hints(self, focused):
        if isinstance(focused, GameCard):
            hints = [("A", "Details"), ("X", "Favourite"), ("Menu", "Options"), ("Y", "Search")]
            if self.current_view() == "all":
                hints.append(("View", "Sort"))
            return hints + [("L1/R1", "Tabs"), ("B", "Top")]
        if isinstance(focused, Input):
            return [("A", "Keyboard"), ("B", "Games"), ("L1/R1", "Tabs")]
        return [("A", "Select"), ("B", "Games"), ("Y", "Search"), ("Guide", "Game menu")]

    def on_launcher(self):
        return self.screen is self._launcher_screen

    def gamepad_b(self):
        """Back walks out of the search box, then from the covers to the tabs."""
        if not self.on_launcher():
            return False
        if isinstance(self.focused, GameCard):
            self.launcher_query_one("#library" if self.page == "installed" else "#store", Button).focus()
        else:
            self.browser().request_focus_selected()
        return True

    def gamepad_y(self):
        if not self.on_launcher():
            return False
        search = self.active_search()
        search.focus()
        self.push_screen(OnScreenKeyboard(search))
        return True

    def gamepad_l1(self):
        if not self.on_launcher():
            return False
        self.cycle_view(-1)
        return True

    def gamepad_r1(self):
        if not self.on_launcher():
            return False
        self.cycle_view(1)
        return True

    def gamepad_x(self):
        if self.on_launcher() and isinstance(self.focused, GameCard):
            self.toggle_favorite(self.focused.game["address"])
            return True
        return False

    def gamepad_menu(self):
        if self.on_launcher() and isinstance(self.focused, GameCard):
            self.open_game_menu(self.focused.game)
            return True
        return False

    def gamepad_view(self):
        if not self.on_launcher():
            return False
        if self.page == "installed" and self.current_view() == "all":
            self.launcher_query_one("#library-sort", Button).press()
        elif self.page == "discover":
            self.launcher_query_one("#store-compatible", Button).press()
        return True

    def gamepad_guide(self):
        """Guide goes home; pressed at home it opens the system menu."""
        if not self.on_launcher():
            while len(self.screen_stack) > 1:
                self.pop_screen()
        home = self.launcher_query_one("#library", Button)
        if self.focused is home and self.page == "installed":
            from gamepad import open_overlay
            open_overlay()
        else:
            if self.page != "installed":
                home.press()
            home.focus()
        return True

    # Favourites and collections ---------------------------------------------

    def toggle_favorite(self, address):
        favorite = self.activity.toggle_favorite(address)
        self.launcher_query_one("#status", Static).update(
            "Added to Favourites." if favorite else "Removed from Favourites.")
        self.activity_changed()

    def open_game_menu(self, game):
        self.push_screen(GameMenu(game, self.activity, removable=bool(self.library and game["address"] in self.saved)),
                         lambda result: self.game_menu_closed(game, result))

    def game_menu_closed(self, game, result):
        if result == "remove":
            self.perform_game_action("remove", game)
        elif result:
            self.activity_changed()

    def activity_changed(self):
        self.show_view()
        self.render_games()
        self.save_activity()

    @work(group="activity")
    async def save_activity(self):
        try:
            await asyncio.to_thread(self.activity.save)
        except OSError:
            self.launcher_query_one("#status", Static).update("Favourites and recent games could not be saved.")

    def note_installed(self):
        if self.activity.seen(self.installed):
            self.save_activity()

    # Daemon reads ------------------------------------------------------------

    # Service events -----------------------------------------------------------

    @work(thread=True, exclusive=True, group="events")
    def follow_events(self):
        """Follow kwakore's events; reconnect with backoff when the stream ends."""
        delay = 1
        while not self.events_stop.is_set():
            try:
                self.event_stream = self.runtime.events(self.EVENT_TYPES)
                for event in self.event_stream:
                    delay = 1
                    self.call_from_thread(self.apply_event, event)
            except Exception:
                # Any stream failure, including call_from_thread after the
                # interface closed, is a lost connection; never crash the app.
                pass
            finally:
                if self.event_stream is not None:
                    self.event_stream.close()
                    self.event_stream = None
            if self.events_stop.is_set():
                return
            try:
                self.call_from_thread(self.events_lost)
            except RuntimeError:
                return
            self.events_stop.wait(delay)
            delay = min(delay * 2, 30)

    def events_lost(self):
        # Fall back to the regular polling until the stream returns.
        self.events_healthy = False

    def apply_event(self, event):
        kind = event.get("type")
        address = event.get("address")
        if kind != "events.overflow":
            self.events_healthy = True
        if kind == "window.opened" and address and event.get("window_id"):
            windows = self.running.setdefault(address, [])
            if not any(window.get("window_id") == event["window_id"] for window in windows):
                windows.append({"address": address, "window_id": event["window_id"], "name": event.get("name")})
            # Launches from the game menu or desktop entries count as play too.
            self.activity.touch(address)
            self.save_activity()
            self.render_games()
        elif kind == "window.closed" and address:
            windows = [window for window in self.running.get(address, []) if window.get("window_id") != event.get("window_id")]
            if windows:
                self.running[address] = windows
            else:
                self.running.pop(address, None)
            self.render_games()
        elif kind in ("napplet.installed", "napplet.updated", "napplet.uninstalled"):
            if address:
                self.details.pop(address, None)
            self.schedule_reload()
        elif kind == "updates.available":
            available = {item.get("address"): item.get("available_version") for item in event.get("items") or ()
                         if isinstance(item, dict)}
            for game_address, game in self.installed.items():
                game["update_available"] = available.get(game_address)
            self.render_games()
        elif kind == "sync.completed" and self.library:
            self.sync_library()
        elif kind == "events.overflow":
            # Missed events: re-read everything this interface follows.
            self.poll_windows()
            self.schedule_reload()

    def schedule_reload(self):
        """Several install events in a row become one re-read."""
        if self.reload_timer is not None:
            self.reload_timer.stop()
        self.reload_timer = self.set_timer(0.25, self.reload_after_event)

    def reload_after_event(self):
        self.reload_timer = None
        if self.library:
            self.sync_library()
        elif not self.worker_running("catalog"):
            self.load(background=True)

    def poll_tick(self):
        if self.events_healthy and time.monotonic() - self.last_window_poll < self.EVENT_POLL_INTERVAL:
            return
        self.poll_windows()

    @work(exclusive=True, group="windows")
    async def poll_windows(self):
        self.last_window_poll = time.monotonic()
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
        # Snapshots read concurrently can arrive out of order; keep the newest.
        revision = snapshot.get("revision", 0)
        if revision < self.sync_revision:
            return
        self.sync_revision = revision
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
            self.note_installed()
            for address in self.installed:
                self.failed.pop(address, None)
        self.rebuild_library()
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
            self.render_games()

    @property
    def recommending(self):
        return bool(self.library and self.library_loaded and not self.saved and self.page == "installed")

    @work(group="library-changes")
    async def change_library(self, address, action):
        if address in self.busy:
            return
        self.busy.add(address)
        self.render_games()
        self.launcher_query_one("#status", Static).update("Installing…" if action == "add" else "Uninstalling…")
        try:
            snapshot = await asyncio.to_thread(self.library.change, address, action)
        except RuntimeError as error:
            self.launcher_query_one("#status", Static).update(str(error))
            return
        finally:
            self.busy.discard(address)
            self.render_games()
        self.show_sync(snapshot)
        self.rebuild_library()
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
        self.note_installed()
        self.page_cache[page] = games
        self.games = games
        self.catalog.update({game["address"]: game for game in games})
        self.rebuild_library()
        self.render_games()
        if not background and page == "installed":
            self.check_game_updates()
        if background and not recovered:
            return
        self.launcher_query_one("#status", Static).update(
            "Your library is empty. Try a game below or add it to My Games." if self.recommending else
            "" if self.games else
            "Loading your saved library. Find Games is available." if self.library and not self.library_loaded and page == "installed" else
            "No games yet. Choose Find Games to explore the catalog.")

    # Store search ------------------------------------------------------------

    def search_key(self):
        query = self.launcher_query_one("#discovery-search", Input).value.strip()
        return (query, self.category, True if self.compatible_only else None)

    def schedule_store_query(self):
        self.search_revision += 1
        self.search_results = None
        self.search_next = None
        if self.search_timer is not None:
            self.search_timer.stop()
        if self.search_key() != ("", None, None) and callable(getattr(self.runtime, "discover", None)):
            self.search_timer = self.set_timer(self.SEARCH_DELAY, lambda: self.store_query(self.search_key()))

    @work(exclusive=True, group="discover-query")
    async def store_query(self, key, offset=0):
        """kwakore searches the whole catalog; the local filter covers the wait."""
        revision = self.search_revision
        query, category, compatible = key
        try:
            page = await asyncio.to_thread(self.runtime.discover, query=query or None, archetype=category,
                                           compatible=compatible, offset=offset, limit=self.PAGE_SIZE)
        except RuntimeError as error:
            self.launcher_query_one("#status", Static).update(str(error))
            return
        if revision != self.search_revision or key != self.search_key() or not self.is_running:
            return
        items = [game for game in page.get("items", []) if isinstance(game, dict) and game.get("address")]
        self.search_results = (self.search_results or []) + items if offset else items
        next_offset = page.get("next_offset")
        self.search_next = next_offset if isinstance(next_offset, int) and next_offset > offset else None
        self.catalog.update({game["address"]: game for game in items})
        if page.get("complete") is False:
            self.launcher_query_one("#status", Static).update("Catalog still loading from relays…")
        self.render_games()

    def load_more_results(self, card):
        if self.page != "discover" or self.search_results is None or self.search_next is None:
            return
        browser = self.launcher_query_one(Discovery)
        index = browser.card_indices.get(card.game["address"])
        if index is not None and index >= len(browser.cards) - browser.columns and not self.worker_running("discover-query"):
            self.store_query(self.search_key(), offset=self.search_next)

    def category_options(self):
        return [None] + [name for name, _ in browse.archetype_counts(self.page_cache.get("discover", [])).most_common(12)]

    # Rendering ---------------------------------------------------------------

    def active_search(self):
        return self.launcher_query_one("#discovery-search" if self.page == "discover" else "#search", Input)

    def game_status(self, game):
        address = game["address"]
        library_page = self.page == "installed" and not self.recommending
        status = "Downloading…" if address in self.busy else ("Ready to play" if library_page else "Play now · Install")
        if not game.get("available", False):
            status = "Unavailable"
        elif not library_page and address not in self.installed and game.get("missing_domains"):
            status = "Needs " + ", ".join(str(domain) for domain in game["missing_domains"])
        if self.library and library_page and address not in self.installed:
            status = "Download interrupted · Retrying automatically" if address in self.failed else "Downloading…"
        if address in self.running:
            status = "Running"
        if status == "Ready to play":
            status = "Installed · Ready to play"
        if browse.has_update(game, self.installed):
            status += " · Update available"
        if self.activity.favorite(address):
            status = "★ " + status
        return status

    def row(self, game):
        return game | self.details.get(game["address"], {}), self.game_status(game)

    def grid_games(self, query):
        if self.page == "installed":
            if self.recommending:
                return [game for game in self.games if browse.matches(game, query)]
            return browse.library_rows(self.games, self.installed, self.activity, self.sort, self.show, query)
        if self.search_results is not None:
            return self.search_results
        # Until kwakore answers, filter the cached catalog the same way.
        games = [game for game in self.games if browse.matches(game, query)]
        if self.category:
            games = [game for game in games if self.category in (game.get("archetypes") or ())]
        if self.compatible_only:
            games = [game for game in games if browse.compatible(game)]
        return games

    def render_games(self):
        if not self.is_running:
            return
        self.show_view()
        view = self.current_view()
        query = self.active_search().value
        if view == "home":
            shelves = (browse.library_shelves(self.games, self.installed, self.running, self.activity)
                       if self.page == "installed" else browse.store_shelves(self.games, self.installed))
            visible = [game for _, games in shelves for game in games]
            if self.selected_address not in {game["address"] for game in visible}:
                self.selected_address = visible[0]["address"] if visible else None
            self.launcher_query_one(ShelfView).show_shelves(
                [(title, [self.row(game) for game in games]) for title, games in shelves], self.selected_address)
        else:
            games = self.grid_games(query)
            if self.selected_address not in {game["address"] for game in games}:
                self.selected_address = games[0]["address"] if games else None
            self.launcher_query_one(Discovery).show_games([self.row(game) for game in games], self.selected_address)
            visible = games
        self.prefetch_details(self.selected_address)
        if visible and self._initial_grid_focus_pending:
            self._initial_grid_focus_pending = False
            # Enter browsing once the first covers mount, without taking focus
            # from a user who already started typing or opened another screen.
            # Mount-time focus may not have landed yet, so any launcher
            # control still counts as "not browsing" here.
            if self.screen is self._launcher_screen and not query:
                self.browser().request_focus_selected()
        if isinstance(self.screen, GameDetails):
            self.screen.update_page()

    def selected(self):
        address = self.selected_address
        if address is None:
            return None
        game = next((game for game in self.games if game["address"] == address), None)
        game = game or next((game for game in self.search_results or () if game["address"] == address), None)
        return game or self.catalog.get(address)

    def prefetch_details(self, address):
        if address != self.detail_address:
            self.detail_address = address
            if address and address not in self.details and callable(getattr(self.runtime, "details", None)):
                self.load_details(address)

    @work(exclusive=True, group="details")
    async def load_details(self, address):
        try:
            detail = await asyncio.to_thread(self.runtime.details, address)
        except RuntimeError:
            return
        if self.detail_address != address:
            return
        self.details[address] = detail

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

    # Events --------------------------------------------------------------------

    def on_input_changed(self, event):
        if event.input.screen is not self._launcher_screen:
            return
        if event.input.id == "discovery-search":
            self.schedule_store_query()
        if event.input.id in ("search", "discovery-search"):
            self.render_games()

    def on_input_submitted(self, event):
        if event.input.id not in ("search", "discovery-search") or event.input.screen is not self._launcher_screen:
            return
        self.browser().request_focus_selected()

    def on_game_card_chosen(self, event):
        event.stop()
        # A membership reconciliation can queue focus notifications from cards
        # that have already been replaced or lost focus. Keep the latest intent.
        if not event.activate and event.card is not self.focused:
            return
        self.selected_address = event.card.game["address"]
        self.launcher_query_one(Discovery).select(self.selected_address)
        self.launcher_query_one(ShelfView).select(self.selected_address)
        self.prefetch_details(self.selected_address)
        self.load_more_results(event.card)
        if event.activate and not isinstance(self.screen, GameDetails):
            self.push_screen(GameDetails(event.card.game))

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
            if command in ("launch", "try"):
                self.activity.record_launch(address)
                self.save_activity()
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
        if action in ("library", "store"):
            self.page = "installed" if action == "library" else "discover"
            self.launcher_query_one("#library", Button).variant = "primary" if action == "library" else "default"
            self.launcher_query_one("#store", Button).variant = "primary" if action == "store" else "default"
            # Keep the mounted covers while an uncached page loads. An empty
            # cached page is authoritative; a missing cache is still loading.
            if self.page in self.page_cache:
                self.games = self.page_cache[self.page]
            self.render_games()
            self.load(refresh=self.page == "discover", background=bool(self.games))
            if self.page == "installed" and self.games:
                self.check_game_updates()
        elif action in ("library-home", "library-all"):
            if action == "library-home":
                self.launcher_query_one("#search", Input).value = ""
            self.set_view("installed", "home" if action == "library-home" else "all")
        elif action in ("store-home", "store-browse"):
            self.set_view("discover", "home" if action == "store-home" else "browse")
        elif action == "library-sort":
            self.sort = browse.cycle(browse.SORTS, self.sort)
            self.render_games()
        elif action == "library-show":
            self.show = browse.cycle(browse.show_options(self.activity.collections), self.show)
            self.render_games()
        elif action == "store-category":
            options = self.category_options()
            self.category = options[(options.index(self.category) + 1) % len(options)] if self.category in options else None
            self.schedule_store_query()
            self.render_games()
        elif action == "store-compatible":
            self.compatible_only = not self.compatible_only
            self.schedule_store_query()
            self.render_games()
        elif action == "settings":
            self.push_screen(SettingsScreen(runtime=self.runtime, local=self.local, initial="overview"))
        elif action in ("session", "workspaces"):
            commands = {"session": ["kwak-session"],
                        "workspaces": ["hyprctl", "eval", "hl.plugin.hyprflow.toggle()"]}
            self.system_action(commands[action])

    def perform_game_action(self, action, game):
        address = game["address"]
        if action == "favorite":
            self.toggle_favorite(address)
            return
        if action == "options":
            self.open_game_menu(game)
            return
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
                        help="Use the local user's kwakore library and native sync configuration")
    parser.add_argument("--overlay", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.overlay:
        from overlay import GameOverlay
        GameOverlay().run()
    else:
        runtime = Runtime()
        Kiosk(runtime=runtime, library=Library(runtime), local=args.local, gamepad=True,
              activity=Activity()).run()
