"""Responsive discovery cards backed by the launcher's catalog selection."""
from urllib.parse import urlsplit

from textual import work
from textual.containers import Grid, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from icons import icon_hash
from images import TerminalImage
from navigation import move_focus


def artwork(game):
    """Only public HTTPS artwork from daemon metadata may be fetched."""
    for key in ("image", "icon", "banner", "picture", "icon_url", "image_url"):
        value = game.get(key)
        if isinstance(value, str) and urlsplit(value).scheme == "https":
            return value
    return None


def card_source(game):
    """Public artwork first, else the icon kwakore verifies by hash."""
    source = artwork(game)
    if source:
        return source
    digest = icon_hash(game)
    return "kwak-icon:" + digest if digest else None


class GameCard(Widget):
    can_focus = True
    DEFAULT_CSS = """
    GameCard { height: 12; border: round $secondary; background: $panel; padding: 0 1; }
    GameCard:hover { border: round $primary; }
    GameCard:focus, GameCard.selected { border: heavy $primary; background: $primary 12%; }
    GameCard TerminalImage { width: 100%; height: 6; }
    GameCard .card-icon { height: 6; content-align: center middle; color: $primary; text-style: bold; background: $surface; }
    GameCard .card-name { height: 1; text-style: bold; }
    GameCard .card-author { height: 1; color: $foreground 60%; }
    GameCard .card-status { height: 1; color: $primary; }
    GameCard.compact { height: 10; }
    GameCard.compact TerminalImage, GameCard.compact .card-icon { height: 5; }
    GameCard.compact .card-author { display: none; }
    """

    class Chosen(Message):
        def __init__(self, card, activate=False):
            super().__init__()
            self.card = card
            self.activate = activate

    def __init__(self, game, status, compact=False):
        super().__init__(classes="compact" if compact else None)
        self.game = game
        self.status = status
        self.source = card_source(game)
        self.image_started = False

    def compose(self):
        name = self.game.get("name") or "Untitled game"
        if self.source:
            yield TerminalImage(classes="card-art")
        yield Static(f"◆\n{name[:2].upper()}", classes="card-icon", markup=False)
        yield Static(name, classes="card-name", markup=False)
        yield Static(self.game.get("author_name") or "Napplet", classes="card-author", markup=False)
        yield Static(self.status, classes="card-status", markup=False)

    def on_mount(self):
        self.query_one(".card-icon").display = not bool(self.source)

    def update_game(self, game, status):
        if game.get("name") != self.game.get("name"):
            self.query_one(".card-name", Static).update(game.get("name") or "Untitled game")
        if game.get("author_name") != self.game.get("author_name"):
            self.query_one(".card-author", Static).update(game.get("author_name") or "Napplet")
        if status != self.status:
            self.query_one(".card-status", Static).update(status)
        self.game = game
        self.status = status
        source = card_source(game)
        if source != self.source:
            self.source = source
            self.image_started = False
            self.replace_artwork()

    @work(exclusive=True, group="artwork-widget")
    async def replace_artwork(self):
        await self.query(TerminalImage).remove()
        if self.source:
            await self.mount(TerminalImage(classes="card-art"), before=0)
        self.query_one(".card-icon").display = not bool(self.source)
        self.parent.parent.request_artwork()

    def load_artwork(self):
        if self.source and not self.image_started and self.query(TerminalImage):
            self.image_started = True
            if self.source.startswith("kwak-icon:"):
                self.load_icon(self.source)
            else:
                self.query_one(TerminalImage).set_source(self.source)

    @work(group="card-icon")
    async def load_icon(self, source):
        icons = getattr(self.app, "icons", None)
        path = await icons.load(self.game) if icons is not None else None
        if source != self.source or not self.is_mounted:
            return
        if path is None:
            # No usable icon: keep the readable title fallback.
            self.query(TerminalImage).set(display=False)
            self.query_one(".card-icon").display = True
            return
        self.query_one(TerminalImage).set_source(str(path))

    def on_focus(self):
        self.post_message(self.Chosen(self))
        self.scroll_visible(animate=False)

    def on_click(self, event):
        event.stop()
        self.focus()
        self.post_message(self.Chosen(self, activate=True))

    def navigate(self, direction):
        return self.parent.parent.move(self, direction)

    def on_key(self, event):
        if event.key in ("left", "right", "up", "down", "home", "end"):
            event.stop()
            event.prevent_default()
            if not self.navigate(event.key) and event.key in ("left", "right", "up", "down"):
                # Leaving the grid at an edge reaches the toolbar and tabs.
                move_focus(self.screen, event.key, self)
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Chosen(self, activate=True))


class Discovery(VerticalScroll):
    DEFAULT_CSS = """
    Discovery { width: 1fr; height: 1fr; }
    Discovery Grid { height: auto; grid-size: 2; grid-columns: 1fr; grid-rows: 12; grid-gutter: 1; }
    #discovery-empty { height: auto; padding: 2; color: $foreground 70%; }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.columns = 2
        self.signature = None
        self._desired_rows = []
        self._desired_signature = None
        self._rebuilding = False
        self._focus_pending = False
        self.address = None
        self.cards = []
        self.card_indices = {}
        self.cards_by_address = {}
        self.selected_card = None
        self._artwork_scheduled = False
        self._focus_intent = False

    def compose(self):
        yield Static("No games match your search.", id="discovery-empty")
        yield Grid(id="cards")

    def watch_scroll_y(self, old_value, new_value):
        super().watch_scroll_y(old_value, new_value)
        if self.is_mounted:
            self.request_artwork()

    def on_resize(self):
        self.columns = max(1, self.size.width // 24)
        self.query_one(Grid).styles.grid_size_columns = self.columns
        self.request_artwork()

    def request_artwork(self):
        if not self._artwork_scheduled:
            self._artwork_scheduled = True
            self.call_after_refresh(self.load_visible_artwork)

    def load_visible_artwork(self):
        self._artwork_scheduled = False
        if not self.display or not self.is_mounted:
            return
        for card in self.cards_by_address.values():
            if card.source and not card.image_started and card.region.overlaps(self.region):
                card.load_artwork()

    def show_games(self, rows, address):
        self.address = address
        # Desired state and mounted state are distinct. Never cancel a DOM
        # remove/mount transaction halfway through to service a newer snapshot.
        self._desired_rows = [(dict(game), status) for game, status in rows]
        self._desired_signature = self._desired_rows
        if self._desired_signature != self.signature:
            if not self._rebuilding:
                self._rebuilding = True
                self.rebuild()
        else:
            self.select(address)
            self.request_artwork()

    @work(group="cards")
    async def rebuild(self):
        try:
            while self.signature != self._desired_signature:
                rows = self._desired_rows
                await self.reconcile(rows)
                # Publish only a completely mounted snapshot; newer requests
                # coalesce into the next iteration without losing their cards.
                self.signature = rows
        finally:
            self._rebuilding = False
        if self._focus_pending:
            self.request_focus_selected()

    async def reconcile(self, rows):
        grid = self.query_one(Grid)
        cards = list(grid.query(GameCard))
        if [card.game["address"] for card in cards] == [game["address"] for game, _ in rows]:
            for card, (game, status) in zip(cards, rows):
                card.update_game(game, status)
            self.select(self.address)
            return
        # A queued card focus is not applied yet; keep that intent too.
        focused = isinstance(self.app.focused, GameCard) or self._focus_intent
        await grid.remove_children()
        if rows:
            await grid.mount(*(GameCard(game, status) for game, status in rows))
        self.cards = list(grid.query(GameCard))
        self.card_indices = {card.game["address"]: index for index, card in enumerate(self.cards)}
        self.cards_by_address = {card.game["address"]: card for card in self.cards}
        self.selected_card = None
        self.query_one("#discovery-empty").display = not bool(rows)
        self.select(self.address)
        if self.display and (focused or getattr(self.app.focused, "id", None) == "store"):
            self._focus_pending = True
        self.request_artwork()

    def select(self, address):
        self.address = address
        card = self.cards_by_address.get(address)
        if card is self.selected_card:
            return
        if self.selected_card is not None:
            self.selected_card.remove_class("selected")
        if card is not None:
            card.add_class("selected")
        self.selected_card = card

    def request_focus_selected(self):
        """Focus after an in-flight reconciliation, using its latest selection."""
        self._focus_pending = True
        if not self._rebuilding and self.cards and self.screen is self.app.screen:
            self._focus_pending = False
            self.focus_selected()

    def focus_entry(self):
        return self.cards_by_address.get(self.address)

    def on_descendant_focus(self):
        self._focus_intent = False

    def focus_selected(self):
        card = self.cards_by_address.get(self.address) or (self.cards[0] if self.cards else None)
        if card:
            self._focus_intent = True
            card.focus()
        else:
            self.focus()

    def move(self, card, key):
        """Move within the grid; False at an edge so focus can leave it."""
        cards = self.cards
        index = self.card_indices.get(card.game["address"])
        if index is None or not cards:
            return False
        last, column = len(cards) - 1, index % self.columns
        if key == "left" and column == 0 or key == "right" and (column == self.columns - 1 or index == last):
            return False
        if key == "up" and index < self.columns:
            return False
        if key == "down" and index - column + self.columns > last:
            return False
        target = {"left": index - 1, "right": index + 1,
                  "up": index - self.columns, "down": index + self.columns,
                  "home": 0, "end": last}[key]
        cards[max(0, min(target, last))].focus()
        return True
