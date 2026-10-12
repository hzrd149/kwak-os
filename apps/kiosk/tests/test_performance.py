"""Operation-count regressions: deterministic, without machine speed thresholds."""
import unittest
from unittest.mock import patch

from textual.widgets import OptionList
from discovery import Discovery, GameCard
from images import TerminalImage
from kiosk import Kiosk


class Runtime:
    def listing(self, *args):
        return [dict(address=str(i), name=f"Game {i}", available=True) for i in range(110)]

    def request(self, *args):
        return {"items": []}


class PerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_refresh_keeps_options_cards_and_selection(self):
        app = Kiosk(Runtime())
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#store")
            await app.workers.wait_for_complete()
            await pilot.pause()
            browser = app.query_one(Discovery)
            listing = app.query_one(OptionList)
            cards = list(browser.query(GameCard))
            self.assertEqual(len(cards), 110)
            self.assertEqual(len(browser.query(TerminalImage)), 0)
            await pilot.press("down", "down")
            selected = app.selected()["address"]
            with patch.object(listing, "clear_options", wraps=listing.clear_options) as clear, \
                 patch.object(listing, "add_options", wraps=listing.add_options) as add, \
                 patch.object(app, "load_details", wraps=app.load_details) as details:
                for _ in range(5):
                    app.render_games()
                await pilot.pause()
                clear.assert_not_called()
                add.assert_not_called()
                details.assert_not_called()
            self.assertEqual(list(browser.query(GameCard)), cards)
            self.assertEqual(app.selected()["address"], selected)

    async def test_card_selection_updates_only_previous_and_current(self):
        app = Kiosk(Runtime())
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#store")
            await app.workers.wait_for_complete()
            await pilot.pause()
            browser = app.query_one(Discovery)
            old = browser.selected_card
            new = browser.cards[1]
            with patch.object(GameCard, "set_class", autospec=True) as all_cards:
                browser.select(new.game["address"])
                browser.select(new.game["address"])
                all_cards.assert_not_called()
            self.assertNotIn("selected", old.classes)
            self.assertIn("selected", new.classes)

    async def test_card_status_and_late_artwork_update_in_place(self):
        from PIL import Image
        from textual.widgets import Static
        app = Kiosk(Runtime())
        with patch("images.ImageCache.load", return_value=Image.new("RGB", (16, 16))) as load:
            async with app.run_test(size=(100, 32)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.click("#store")
                await app.workers.wait_for_complete()
                await pilot.pause()
                browser = app.query_one(Discovery)
                card = browser.cards[0]
                app.busy.add("0")
                app.games[0] = app.games[0] | {"icon": "https://example.test/late.png"}
                app.render_games()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertIs(browser.cards[0], card)
                self.assertEqual(str(card.query_one(".card-status", Static).render()), "Downloading…")
                self.assertEqual(card.query_one(TerminalImage).state, "ready")
                load.assert_called_once_with("https://example.test/late.png")
                app.games[0] = app.games[0] | {"icon": None}
                app.render_games()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(len(card.query(TerminalImage)), 0)
                self.assertTrue(card.query_one(".card-icon").display)

    async def test_refresh_below_modal_updates_launcher_without_crashing(self):
        from textual.screen import ModalScreen
        from textual.widgets import Static
        class Modal(ModalScreen):
            def compose(self):
                yield Static("Busy")
        app = Kiosk(Runtime())
        async with app.run_test(size=(80, 28)) as pilot:
            await app.workers.wait_for_complete()
            app.push_screen(Modal())
            await pilot.pause()
            app.load(background=True)
            await app.workers.wait_for_complete()
            app.render_games()
            app.pop_screen()
            await pilot.pause()
            self.assertEqual(app.selected()["name"], "Game 0")
