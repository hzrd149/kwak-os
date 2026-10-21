import unittest

from game_details import GameDetails
from kiosk import Kiosk
from navigation import GamepadPressed
from support import settle
from textual.widgets import Button, Static

ADDRESS = "35129:" + "a" * 64 + ":game"
OLD, NEW = "1" * 64, "2" * 64
GAME = dict(address=ADDRESS, name="Tabbed", available=True,
            version={"event_id": NEW, "created_at": 1700000100})
SNAPSHOT = "5129:" + "b" * 64 + ":" + "3" * 64


class Runtime:
    def __init__(self, installed=True):
        self.calls = []
        self.installed = installed
        self.rules = [{"permission": "notify", "subject": "", "decision": "allow"},
                      {"permission": "dispatch", "subject": "share", "decision": "deny"}]
    def listing(self, command, *args):
        return [GAME] if command != "installed" or self.installed else []
    def details(self, address):
        return GAME
    def request(self, *args):
        self.calls.append(args)
        return {}
    def versions(self, address):
        return {"items": [dict(event_id=NEW, created_at=1700000100, valid=True, selected=True),
                          dict(event_id=OLD, created_at=1700000000, valid=True, selected=False),
                          dict(event_id="9" * 64, created_at=1690000000, valid=False, selected=False)],
                "latest_observed": {"event_id": NEW}, "pinned": False}
    def select(self, address, event_id):
        self.calls.append(("select", address, event_id))
        return {"data_reset": True}
    def snapshots(self, address):
        return {"items": [dict(address=SNAPSHOT, event_id="3" * 64, created_at=1700000050, name="Speedrun build",
                               relation="derivative", valid=True)]}
    def permissions(self, address):
        return {"required_domains": ["relay.example"], "optional_domains": [], "saved_rules": self.rules}
    def set_permission(self, address, permission, decision, subject=""):
        self.calls.append(("set", permission, decision, subject))
        self.rules = [dict(rule, decision=decision) if rule["permission"] == permission else rule for rule in self.rules]
    def clear_permission(self, address, permission, subject=""):
        self.calls.append(("clear", permission, subject))
        self.rules = [rule for rule in self.rules if rule["permission"] != permission]


async def open_page(pilot):
    await settle(pilot)
    pilot.app.push_screen(GameDetails(GAME))
    await settle(pilot)
    return pilot.app.screen


class GameTabTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_selection_needs_second_press_and_never_while_running(self):
        runtime = Runtime()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 36)) as pilot:
            page = await open_page(pilot)
            await pilot.click("#store-releases-tab")
            await settle(pilot)
            buttons = list(page.query(".version").results(Button))
            self.assertEqual(len(buttons), 3)
            self.assertIn("installed, latest", str(buttons[0].label))
            self.assertTrue(buttons[0].disabled)
            self.assertTrue(buttons[2].disabled)  # Invalid manifest.
            self.assertNotIn("not available yet", str(page.query_one("#store-release", Static).content))
            app.running[ADDRESS] = [{"window_id": "w"}]
            buttons[1].press()
            await settle(pilot)
            self.assertIn("Stop the game", str(page.query_one("#store-feedback", Static).content))
            app.running.clear()
            buttons[1].press()
            await settle(pilot)
            self.assertIn("Press again", str(page.query_one("#store-feedback", Static).content))
            self.assertNotIn(("select", ADDRESS, OLD), runtime.calls)
            next(button for button in page.query(".version").results(Button) if button.name == OLD).press()
            await settle(pilot)
            self.assertIn(("select", ADDRESS, OLD), runtime.calls)
            self.assertIn("Saved data was reset", str(page.query_one("#store-feedback", Static).content))

    async def test_snapshots_play_without_install_and_permissions_change(self):
        runtime = Runtime()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 36)) as pilot:
            page = await open_page(pilot)
            for _ in range(2):
                app.post_message(GamepadPressed("r1"))
                await settle(pilot)
            self.assertEqual(page.detail_tab, "snapshots")
            snapshot = page.query_one(".snapshot", Button)
            self.assertIn("Speedrun build", str(snapshot.label))
            snapshot.press()
            await settle(pilot)
            self.assertIn(("try", SNAPSHOT), runtime.calls)
            app.post_message(GamepadPressed("r1"))
            await settle(pilot)
            self.assertEqual(page.detail_tab, "permissions")
            rows = list(page.query(".rule-row"))
            self.assertEqual(len(rows), 2)
            notify_allow, notify_deny, notify_forget = rows[0].query(Button)
            self.assertTrue(notify_allow.disabled)
            self.assertTrue(rows[1].query(Button).first().disabled)  # No new dispatch allows.
            notify_deny.press()
            await settle(pilot)
            self.assertIn(("set", "notify", "deny", ""), runtime.calls)
            page.query(".rule-row").last().query(Button).last().press()
            await settle(pilot)
            self.assertIn(("clear", "dispatch", "share"), runtime.calls)
            self.assertEqual(len(page.query(".rule-row")), 1)

    async def test_uninstalled_game_has_no_permissions_tab(self):
        app = Kiosk(Runtime(installed=False))
        async with app.run_test(size=(100, 36)) as pilot:
            page = await open_page(pilot)
            self.assertFalse(page.query_one("#store-permissions-tab").display)
            self.assertEqual(page.tabs(), ["info", "releases", "snapshots"])
            await pilot.click("#store-releases-tab")
            await settle(pilot)
            self.assertTrue(all(button.disabled for button in page.query(".version").results(Button)))


if __name__ == "__main__":
    unittest.main()
