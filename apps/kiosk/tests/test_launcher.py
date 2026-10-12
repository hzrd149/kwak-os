import asyncio
import unittest

from kiosk import Kiosk
from discovery import Discovery, GameCard
from game_details import GameDetails
from textual.widgets import Button, OptionList, Static
from textual.worker import WorkerCancelled

ADDRESS = '35129:' + 'a' * 64 + ':one'
OTHER = '35129:' + 'b' * 64 + ':two'
GAME = {'address': ADDRESS, 'name': 'First Game', 'description': 'First description', 'available': True,
        'version': {'event_id': 'a' * 64, 'created_at': 1700000000},
        'update_available': {'event_id': 'b' * 64, 'created_at': 1700001000}}
SECOND = dict(GAME, address=OTHER, name='Second Game', description='Second description')


class LauncherRuntime:
    def __init__(self):
        self.calls = []
        self.instances = [{'address': ADDRESS, 'window_id': 'first-instance'},
                          {'address': ADDRESS, 'window_id': 'second-instance'},
                          {'address': OTHER, 'window_id': 'other-instance'}]
        self.fail_windows = False
        self.fail_refresh = False

    def listing(self, command, *args):
        if self.fail_refresh:
            raise RuntimeError('Retry refresh')
        return [GAME, SECOND]

    def windows(self):
        if self.fail_windows:
            raise RuntimeError('Unavailable')
        return self.instances.copy()

    def details(self, address):
        game = GAME if address == ADDRESS else SECOND
        return dict(game, versions=[game['version']], installed_version=game['version'])

    def request(self, *args):
        self.calls.append(args)
        if args[0] == 'stop':
            self.instances = [item for item in self.instances if item['window_id'] != args[1]]
        return {}


class LauncherTests(unittest.IsolatedAsyncioTestCase):
    async def settle(self, app, pilot):
        for _ in range(3):
            try:
                await app.workers.wait_for_complete()
            except WorkerCancelled:
                pass
            await pilot.pause()

    async def open_first(self, app, pilot):
        app.launcher_query_one(Discovery).focus_selected()
        await pilot.press('enter')
        await self.settle(app, pilot)
        self.assertIsInstance(app.screen, GameDetails)

    async def test_cover_grid_selection_does_not_launch_and_fits_small_terminals(self):
        for size in [(80, 28), (88, 33), (120, 40)]:
            runtime = LauncherRuntime()
            app = Kiosk(runtime)
            async with app.run_test(size=size) as pilot:
                await self.settle(app, pilot)
                browser = app.query_one(Discovery)
                self.assertFalse(app.query_one('#games').display)
                self.assertFalse(app.query_one('#game-panel').display)
                browser.cards[1].focus()
                await pilot.pause()
                self.assertEqual(app.selected()['address'], OTHER)
                self.assertEqual(runtime.calls, [])
                for card in browser.cards:
                    self.assertLessEqual(card.region.right, browser.region.right)
                await pilot.press('enter')
                await self.settle(app, pilot)
                self.assertIsInstance(app.screen, GameDetails)
                self.assertEqual(str(app.screen.query_one('#store-title', Static).content), 'Second Game')
                self.assertEqual(runtime.calls, [])
                for button in app.screen.query_one('#store-actions').query(Button):
                    self.assertLessEqual(button.region.right, size[0])
                    self.assertLessEqual(button.region.bottom, size[1])

    async def test_stop_only_selected_games_instances_and_update_exact_address(self):
        runtime = LauncherRuntime()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 36)) as pilot:
            await self.settle(app, pilot)
            await self.open_first(app, pilot)
            self.assertFalse(app.screen.query_one('#play', Button).disabled)
            await pilot.click('#play')
            await self.settle(app, pilot)
            self.assertEqual(runtime.calls, [('stop', 'first-instance'), ('stop', 'second-instance')])
            self.assertEqual(runtime.instances[0]['window_id'], 'other-instance')
            self.assertEqual(str(app.screen.query_one('#play', Button).label), 'Play')
            await pilot.click('#update')
            await self.settle(app, pilot)
            self.assertEqual(runtime.calls[-1], ('update', ADDRESS))
            self.assertIn('Installed release:', str(app.launcher_query_one('#game-version', Static).content))
            self.assertIn('Release selection is not available yet', str(app.launcher_query_one('#game-version', Static).content))

    async def test_poll_failure_disables_stop_and_update_refresh_failure_is_reported(self):
        runtime = LauncherRuntime()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 36)) as pilot:
            await self.settle(app, pilot)
            await self.open_first(app, pilot)
            runtime.fail_windows = True
            app.poll_windows()
            await self.settle(app, pilot)
            self.assertEqual(str(app.screen.query_one('#play', Button).label), 'Play')
            self.assertIn('unavailable', str(app.launcher_query_one('#game-state', Static).content))
            runtime.fail_refresh = True
            await pilot.click('#update')
            await self.settle(app, pilot)
            self.assertIn('Action completed. Library refresh failed', str(app.launcher_query_one('#status', Static).content))

    async def test_updates_are_checked_automatically_and_action_requires_new_release(self):
        class CurrentRuntime(LauncherRuntime):
            checked = False
            def listing(self, command, *args):
                return [GAME] if self.checked else [{key: value for key, value in GAME.items() if key != "update_available"}]
            def details(self, address):
                return self.listing("installed")[0]
            def check_updates(self):
                self.checked = True
                self.calls.append(("updates",))
        runtime = CurrentRuntime()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 36)) as pilot:
            await self.settle(app, pilot)
            self.assertIn(("updates",), runtime.calls)
            self.assertEqual(str(app.query_one("#update", Button).label), "Update")
            self.assertFalse(app.query_one("#update", Button).disabled)
            self.assertFalse(app.query('#refresh'))
            self.assertFalse(any(call[0] == "update" for call in runtime.calls))

    async def test_stale_detail_cannot_replace_new_selection(self):
        import threading
        gate = threading.Event()
        class SlowRuntime(LauncherRuntime):
            def details(self, address):
                if address == ADDRESS:
                    gate.wait(3)
                return super().details(address)
        runtime = SlowRuntime()
        app = Kiosk(runtime)
        try:
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.pause()
                app.query_one('#games', OptionList).highlighted = 1
                await pilot.pause()
                gate.set()
                await self.settle(app, pilot)
                self.assertEqual(str(app.query_one('#game-title', Static).content), 'Second Game')
        finally:
            gate.set()

    async def test_primary_button_toggles_play_and_stop_with_runtime_state(self):
        class ToggleRuntime(LauncherRuntime):
            def __init__(self):
                super().__init__()
                self.instances = []
            def request(self, *args):
                if args[0] == 'launch':
                    self.instances.append({'address': args[1], 'window_id': 'launched-instance'})
                return super().request(*args)
        runtime = ToggleRuntime()
        app = Kiosk(runtime)
        async with app.run_test(size=(80, 28)) as pilot:
            await self.settle(app, pilot)
            await self.open_first(app, pilot)
            button = app.screen.query_one('#play', Button)
            self.assertEqual(str(button.label), 'Play')
            self.assertFalse(app.screen.query('#stop'))
            await pilot.click('#play')
            await self.settle(app, pilot)
            self.assertEqual(str(button.label), 'Stop')
            await pilot.click('#play')
            await self.settle(app, pilot)
            self.assertEqual(str(button.label), 'Play')
            self.assertEqual(runtime.calls, [('launch', ADDRESS), ('stop', 'launched-instance')])
