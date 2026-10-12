import asyncio
import subprocess
import threading
import tempfile
import unittest
from kiosk import Kiosk, Runtime
from discovery import GameCard
from textual.widgets import Button, Input, OptionList, Static


GAME = {"address": "35129:" + "a" * 64 + ":game", "name": "Green Game",
        "description": "A game", "available": True}


class RuntimeTests(unittest.TestCase):
    def test_pagination_and_argument_boundaries(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            payload = '{"items":[],"next_offset":null}' if len(calls) > 1 else '{"items":[{"name":"first"}],"next_offset":100}'
            return subprocess.CompletedProcess(args, 0, payload, "")
        self.assertEqual(Runtime(run).listing("discover"), [{"name": "first"}])
        self.assertIn("100", calls[1])
        self.assertEqual(calls[0][:3], ["kwak", "--json", "discover"])

    def test_failure_is_actionable_and_does_not_echo_output(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime(lambda *a, **k: subprocess.CompletedProcess([], 1, "private", "secret"), lock_root=root)
            with self.assertRaisesRegex(RuntimeError, "Retry") as result:
                runtime.request("install", GAME["address"])
            self.assertNotIn("secret", str(result.exception))


class FakeRuntime:
    def __init__(self):
        self.calls = []
    def listing(self, command, *args):
        return [GAME]
    def request(self, *args):
        self.calls.append(args)
        return {}


class KioskTests(unittest.IsolatedAsyncioTestCase):
    async def test_library_navigation_fits_default_window(self):
        app = Kiosk(FakeRuntime())
        async with app.run_test(size=(110, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            nav = app.query_one("#nav")
            for button in nav.query(Button):
                self.assertGreater(button.region.width, 0)
                self.assertLessEqual(button.region.right, nav.region.right)
            self.assertIsInstance(app.focused, GameCard)

    async def test_selection_survives_reordering_and_search_enter_focuses_games(self):
        other = {**GAME, "address": "35129:" + "b" * 64 + ":second", "name": "Second"}
        app = Kiosk(FakeRuntime())
        async with app.run_test(size=(110, 36)) as pilot:
            await app.workers.wait_for_complete()
            app.games = [GAME, other]
            app.render_games()
            app.query_one("#games", OptionList).highlighted = 1
            await pilot.pause()
            app.games = [other, GAME]
            app.render_games()
            self.assertEqual(app.selected()["address"], other["address"])
            search = app.query_one("#search", Input)
            search.focus()
            search.value = "second"
            await pilot.press("enter")
            self.assertIsInstance(app.focused, GameCard)
            self.assertEqual(app.selected()["address"], other["address"])

    async def test_empty_library_suggestions_trial_then_saved_download(self):
        class EmptyLibrary:
            addresses = []
            def snapshot(self):
                return {"addresses": list(self.addresses), "loaded": True,
                        "pending": [], "name": "Player", "error": ""}
            sync = snapshot
            def change(self, address, action):
                self.addresses = [address] if action == "add" else []
                return self.snapshot()
        class EmptyRuntime(FakeRuntime):
            def listing(self, command, *args):
                return [GAME] if command == "discover" else []
        runtime, library = EmptyRuntime(), EmptyLibrary()
        app = Kiosk(runtime, library=library)
        async with app.run_test(size=(130, 40)) as pilot:
            await app.workers.wait_for_complete()
            self.assertEqual(app.page, "installed")
            self.assertTrue(app.recommending)
            app.query_one("#games", OptionList).highlighted = 0
            await pilot.pause()
            await pilot.click(app.query_one(GameCard))
            await pilot.click("#play")
            await app.workers.wait_for_complete()
            self.assertEqual(runtime.calls, [("try", GAME["address"])])
            self.assertEqual(library.addresses, [])
            await pilot.click("#install")
            await app.workers.wait_for_complete()
            self.assertEqual(library.addresses, [GAME["address"]])
            self.assertFalse(app.recommending)
            self.assertIn(("install", GAME["address"]), runtime.calls)

    async def test_saved_library_downloads_concurrently_then_enables_play(self):
        other = {**GAME, "address": "35129:" + "b" * 64 + ":second", "name": "Second"}
        barrier = threading.Barrier(2)
        class RuntimeWithDownloads(FakeRuntime):
            def listing(self, command, *args):
                return []
            def request(self, *args):
                self.calls.append(args)
                if args[0] == "install":
                    barrier.wait(timeout=3)
                return {}
        class SavedLibrary:
            def snapshot(self):
                return {"addresses": [GAME["address"], other["address"]], "loaded": True,
                        "pending": [], "name": "Player", "error": ""}
            sync = snapshot
        runtime = RuntimeWithDownloads()
        app = Kiosk(runtime, library=SavedLibrary())
        async with app.run_test(size=(130, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(set(app.installed), {GAME["address"], other["address"]})
            self.assertEqual(len(runtime.calls), 2)
            app.query_one("#games", OptionList).highlighted = 0
            await pilot.pause()
            await pilot.click(app.query_one(GameCard))
            await pilot.click("#play")
            await app.workers.wait_for_complete()
            self.assertEqual(runtime.calls[-1][0], "launch")

    async def test_browse_search_trial_and_install(self):
        class TrialRuntime(FakeRuntime):
            def listing(self, command, *args):
                return [GAME] if command == "discover" else []
        runtime = TrialRuntime()
        app = Kiosk(runtime)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#store")
            await app.workers.wait_for_complete()
            app.query_one("#games", OptionList).highlighted = 0
            await pilot.pause()
            await pilot.click(app.query_one(GameCard))
            await pilot.click("#play")
            await app.workers.wait_for_complete()
            self.assertEqual(runtime.calls, [("try", GAME["address"])])
            await pilot.click("#install")
            await app.workers.wait_for_complete()
            self.assertEqual(runtime.calls[-1], ("install", GAME["address"]))
            await pilot.press("escape")
            await pilot.click("#library")
            await app.workers.wait_for_complete()
            self.assertEqual(app.page, "installed")
            app.query_one("#search", Input).value = "absent"
            await pilot.pause()
            self.assertEqual(app.query_one("#games", OptionList).option_count, 0)

    async def test_error_does_not_look_like_empty_library(self):
        class FailedRuntime(FakeRuntime):
            def listing(self, *args):
                raise RuntimeError("Unavailable. Retry.")
        app = Kiosk(FailedRuntime())
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            self.assertIn("Retry", str(app.query_one("#status", Static).render()))


class IntegratedSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_stays_inside_launcher_and_preserves_library(self):
        from unittest.mock import patch
        from settings import SettingsScreen, SettingsError
        from textual.widgets import TabbedContent
        runtime = FakeRuntime()
        app = Kiosk(runtime)
        for size in ((80, 28), (110, 36)):
            app = Kiosk(runtime)
            with patch('settings.account_request', side_effect=SettingsError('Account service unavailable')):
                async with app.run_test(size=size) as pilot:
                    await app.workers.wait_for_complete()
                    app.query_one('#search', Input).value = 'Green'
                    await pilot.pause()
                    app.query_one('#games', OptionList).highlighted = 0
                    await pilot.click('#settings')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertIsInstance(app.screen, SettingsScreen)
                    screen = app.screen
                    categories = screen.query_one('#categories', OptionList)
                    panel = screen.query_one('#panel')
                    self.assertLessEqual(categories.region.right, panel.region.x)
                    self.assertGreater(panel.region.width, 30)
                    self.assertEqual(screen.query_one(TabbedContent).active, 'overview')
                    for index, (category, _) in enumerate(screen.CATEGORIES):
                        categories.highlighted = index
                        categories.focus()
                        await pilot.press('enter')
                        await pilot.pause()
                        self.assertEqual(screen.query_one(TabbedContent).active, category)
                    screen.query_one('#account-password', Input).value = 'secret'
                    await pilot.press('escape')
                    await pilot.pause()
                    self.assertNotIsInstance(app.screen, SettingsScreen)
                    self.assertEqual(app.query_one('#search', Input).value, 'Green')
                    self.assertEqual(app.selected()['address'], GAME['address'])
                    self.assertTrue(runtime.calls)
                    self.assertTrue(all(call == ('status',) for call in runtime.calls))

    async def test_service_failure_is_visible_without_closing_settings(self):
        from unittest.mock import patch
        from settings import SettingsError
        class OfflineRuntime(FakeRuntime):
            def request(self, *args):
                raise RuntimeError('Game service unavailable. Please retry.')
        app = Kiosk(OfflineRuntime())
        with patch('settings.account_request', side_effect=SettingsError('No account')):
            async with app.run_test(size=(80, 28)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.click('#settings')
                await app.workers.wait_for_complete()
                self.assertIn('retry', str(app.screen.query_one('#runtime-status', Static).render()))
                await pilot.click('#close')
                self.assertEqual(app.page, 'installed')


if __name__ == "__main__":
    unittest.main()
