#!/usr/bin/env python3
"""Read-only Textual integration smoke test against the current user's kwak."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/kiosk"), str(ROOT / "apps/settings")]

from kiosk import Kiosk, Runtime
from overlay import DesktopActions, GameOverlay
from screenshots import ScreenshotStore
import tempfile
from textual.widgets import Button, Input, OptionList, Static
from settings import SettingsScreen


class ReadOnlyRuntime(Runtime):
    def request(self, *args):
        if not args or args[0] not in {"status", "installed", "windows", "discover"}:
            raise RuntimeError("Local smoke test permits only read operations")
        return super().request(*args)


async def check_size(runtime, size):
    # Passing a Runtime disables account-library synchronization and downloads.
    app = Kiosk(runtime=runtime)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        installed = len(app.games)
        assert app.query_one("#games", OptionList).option_count == installed
        assert "Loading" not in str(app.query_one("#status").render())
        nav = app.query_one("#nav")
        for button in nav.query(Button):
            assert button.region.width > 0 and button.region.right <= nav.region.right
        await pilot.click("#store")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.page == "discover"
        catalog = len(app.games)
        assert app.query_one("#games", OptionList).option_count == catalog
        search = app.query_one("#search", Input)
        search.value = "\u2603-no-such-local-smoke-game"
        await pilot.pause()
        assert app.query_one("#games", OptionList).option_count == 0
        search.value = ""
        await pilot.pause()
        assert app.query_one("#games", OptionList).option_count == catalog
        await pilot.click("#library")
        await app.workers.wait_for_complete()
        assert app.page == "installed" and len(app.games) == installed
        await pilot.click("#settings")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        rail = app.screen.query_one("#categories", OptionList)
        panel = app.screen.query_one("#panel")
        assert rail.region.right <= panel.region.x and panel.region.width > 30
        assert "kwakore ready" in str(app.screen.query_one("#runtime-status", Static).render())
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert app.page == "installed" and len(app.games) == installed
        return {"columns": size[0], "rows": size[1], "installed": installed,
                "catalog": catalog, "navigation": "passed", "search": "passed", "settings": "passed"}


async def main():
    runtime = ReadOnlyRuntime()
    status = await asyncio.to_thread(runtime.request, "status")
    assert status["health"]["ready"], "kwakore is not ready"
    before = await asyncio.to_thread(runtime.listing, "installed")
    windows_before = await asyncio.to_thread(runtime.request, "windows")
    results = [await check_size(runtime, size) for size in [(88, 33), (100, 32), (120, 40), (160, 50)]]
    # With no target supplied, the menu checks the empty target without touching
    # the compositor or closing a real window. Screenshot state is isolated.
    with tempfile.TemporaryDirectory(prefix="kwak-smoke-") as directory:
        overlay = GameOverlay(desktop=DesktopActions(target={}, runtime=runtime),
                              screenshots=ScreenshotStore(root=directory))
        async with overlay.run_test(size=(80, 28)) as pilot:
            await overlay.workers.wait_for_complete()
            await pilot.pause()
            assert overlay.query_one("#exit-game", Button).disabled
            assert overlay.query_one("#screenshot", Button).disabled
            assert overlay.focused is overlay.query_one("#resume", Button)
            for button in overlay.query_one("#menu").query(Button):
                assert button.region.width > 0 and button.region.right <= 80
            # Disabled game actions are skipped; keyboard navigation must reach
            # Session even when its row needs scrolling in a smaller terminal.
            for _ in range(4):
                await pilot.press("tab")
            await pilot.pause()
            session = overlay.query_one("#session", Button)
            assert overlay.focused is session
            assert session.region.y >= 0 and session.region.bottom <= 28
    after = await asyncio.to_thread(runtime.listing, "installed")
    assert sorted(after, key=lambda game: game["address"]) == sorted(before, key=lambda game: game["address"])
    assert await asyncio.to_thread(runtime.request, "windows") == windows_before
    print(json.dumps({"service_ready": True, "sizes": results,
                      "overlay_80x28": "passed", "installed_records_unchanged": True,
                      "windows_unchanged": True}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
