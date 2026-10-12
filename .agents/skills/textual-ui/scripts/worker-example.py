"""Run with --self-test for a headless, backend-free worker example."""
import asyncio
import sys
import threading
import time

from textual import work
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class FakeBackend:
    """A controlled delay with an explicit deadline; no external services."""

    def __init__(self):
        self.slow_started = threading.Event()
        self.slow_finished = threading.Event()

    def details(self, address: str, *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        if address == "slow":
            self.slow_started.set()
            delay = 0.2
        else:
            delay = 0.01
        if time.monotonic() + delay > deadline:
            raise TimeoutError("Details request exceeded its deadline")
        time.sleep(delay)
        if address == "slow":
            self.slow_finished.set()
        return f"Details for {address}"


class Settings(ModalScreen):
    def compose(self) -> ComposeResult:
        yield Static("Example settings")


class Example(App):
    def __init__(self, backend=None):
        super().__init__()
        self.backend = backend or FakeBackend()
        self.address = None
        self.generation = 0
        self.owner = None

    def compose(self) -> ComposeResult:
        yield Input(id="search")
        yield Button("Select slow game", id="slow")
        yield Button("Select fast game", id="fast")
        yield Static("Select a game", id="details", markup=False)

    def on_mount(self) -> None:
        # Retain the screen owning these controls. self.screen may become a modal.
        self.owner = self.screen

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.select_game(event.button.id)

    def select_game(self, address: str) -> None:
        self.address = address
        self.generation += 1
        self.owner.query_one("#details", Static).update("Loading…")
        self.fetch_details(address, self.generation, self.owner)

    @work(exclusive=True, group="details")
    async def fetch_details(self, address, generation, owner) -> None:
        try:
            # Async worker remains on the UI thread; only backend runs in a thread.
            result = await asyncio.to_thread(self.backend.details, address, timeout=0.5)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, OSError) as error:
            result = f"Details unavailable: {error}"
        if (not self.is_running or not owner.is_mounted
                or self.address != address or self.generation != generation):
            return
        owner.query_one("#details", Static).update(result)


async def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Example did not reach expected state")
        await asyncio.sleep(0.005)


async def self_test():
    backend = FakeBackend()
    app = Example(backend)
    async with app.run_test(size=(80, 28)) as pilot:
        app.select_game("slow")
        await wait_for(backend.slow_started.is_set)
        search = app.owner.query_one("#search", Input)
        search.focus()
        await pilot.press("a", "b")
        assert search.value == "ab"
        # No wait for the slow thread: selection starts the replacement worker.
        app.select_game("fast")
        await app.push_screen(Settings())
        details = app.owner.query_one("#details", Static)
        await wait_for(lambda: str(details.content) == "Details for fast")
        assert isinstance(app.screen, Settings)
        await wait_for(backend.slow_finished.is_set)
        assert str(details.content) == "Details for fast"
        app.pop_screen()
        await pilot.pause()
        assert search.value == "ab"
    teardown_backend = FakeBackend()
    closing_app = Example(teardown_backend)
    async with closing_app.run_test(size=(80, 28)):
        closing_app.select_game("slow")
        await wait_for(teardown_backend.slow_started.is_set)
    await wait_for(teardown_backend.slow_finished.is_set)
    assert not closing_app.is_running
    print("PASS: responsive input, latest selection, modal-safe delivery, preserved search, teardown")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        asyncio.run(self_test())
    else:
        Example().run()
