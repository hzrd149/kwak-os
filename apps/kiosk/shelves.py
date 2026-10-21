"""Horizontal cover shelves for the My Games and Find Games home views."""
from textual import work
from textual.containers import HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import Static

from discovery import GameCard


class Shelf(Vertical):
    DEFAULT_CSS = """
    Shelf { height: auto; }
    Shelf .shelf-title { height: 1; color: $primary; text-style: bold; padding: 0 1; }
    Shelf .shelf-row { height: 10; width: 100%; scrollbar-size-horizontal: 0; }
    Shelf .shelf-row GameCard { width: 24; margin-right: 1; }
    """

    def __init__(self, title, rows):
        super().__init__()
        self.title = title
        self.rows = rows
        self.cards = []
        self.last_index = 0

    def compose(self):
        yield Static(f"{self.title} · {len(self.rows)}", classes="shelf-title", markup=False)
        with HorizontalScroll(classes="shelf-row"):
            for game, status in self.rows:
                yield GameCard(game, status, compact=True)

    def on_mount(self):
        self.cards = list(self.query(GameCard))
        self.watch(self.query_one(HorizontalScroll), "scroll_x", self.request_artwork, init=False)

    def update_rows(self, rows):
        self.rows = rows
        for card, (game, status) in zip(self.cards, rows):
            card.update_game(game, status)

    def on_descendant_focus(self, event):
        if event.widget in self.cards:
            self.last_index = self.cards.index(event.widget)

    def entry_card(self, index=None):
        index = self.last_index if index is None else index
        return self.cards[max(0, min(index, len(self.cards) - 1))] if self.cards else None

    def move(self, card, key):
        index = self.cards.index(card)
        if key in ("left", "right"):
            target = index + (1 if key == "right" else -1)
            if 0 <= target < len(self.cards):
                self.cards[target].focus()
                return True
            return False
        if key in ("home", "end"):
            self.cards[0 if key == "home" else -1].focus()
            return True
        return self.parent.shelf_move(self, key)

    def request_artwork(self):
        self.parent.request_artwork()


class ShelfView(VerticalScroll):
    DEFAULT_CSS = """
    ShelfView { width: 1fr; height: 1fr; }
    ShelfView #shelves-empty { height: auto; padding: 2; color: $foreground 70%; }
    """

    def __init__(self, empty="Nothing here yet.", **kwargs):
        super().__init__(**kwargs)
        self.empty = empty
        self.signature = None
        self._desired = []
        self._rebuilding = False
        self._artwork_scheduled = False
        self.address = None
        self.focus_after = None

    def compose(self):
        yield Static(self.empty, id="shelves-empty", markup=False)

    @property
    def shelves(self):
        return list(self.query(Shelf))

    def show_shelves(self, shelves, address=None):
        """`shelves` is [(title, [(game, status), ...]), ...]."""
        self.address = address
        self._desired = [(title, [(dict(game), status) for game, status in rows]) for title, rows in shelves]
        layout = [(title, [game["address"] for game, _ in rows]) for title, rows in self._desired]
        if layout == self.signature:
            for shelf, (_, rows) in zip(self.shelves, self._desired):
                shelf.update_rows(rows)
            self.select(address)
        elif not self._rebuilding:
            self._rebuilding = True
            self.rebuild()

    @work(group="shelves")
    async def rebuild(self):
        try:
            while True:
                desired = self._desired
                layout = [(title, [game["address"] for game, _ in rows]) for title, rows in desired]
                if layout == self.signature:
                    break
                focused = self.app.focused
                position = None
                if isinstance(focused, GameCard) and focused in self.walk_children():
                    shelf = focused.parent.parent
                    position = (shelf.title, shelf.cards.index(focused), focused.game["address"])
                position = position or self.focus_after
                await self.query(Shelf).remove()
                if desired:
                    await self.mount(*(Shelf(title, rows) for title, rows in desired))
                self.signature = layout
                self.query_one("#shelves-empty").display = not desired
                self.select(self.address)
                if position:
                    self.focus_after = None
                    shelf = next((shelf for shelf in self.shelves if shelf.title == position[0]), None)
                    card = None
                    if shelf is not None:
                        # The same game where it moved to, else the same place.
                        card = next((card for card in shelf.cards if card.game["address"] == position[2]),
                                    shelf.entry_card(position[1]))
                    card = card or self.first_card()
                    if card is not None:
                        card.focus()
                self.request_artwork()
        finally:
            self._rebuilding = False

    def first_card(self):
        shelves = self.shelves
        return shelves[0].entry_card(0) if shelves else None

    def focus_entry(self):
        """Arriving from the toolbar enters the top shelf where it was left."""
        shelves = self.shelves
        return shelves[0].entry_card() if shelves else None

    def selected_card(self):
        for shelf in self.shelves:
            for card in shelf.cards:
                if card.game["address"] == self.address:
                    return card
        return self.first_card()

    def request_focus_selected(self):
        card = self.selected_card()
        if card is not None and not self._rebuilding:
            card.focus()
        else:
            self.focus_after = ("", 0, None)

    def select(self, address):
        self.address = address
        for card in self.query(GameCard):
            card.set_class(card.game["address"] == address, "selected")

    def shelf_move(self, shelf, key):
        shelves = self.shelves
        index = shelves.index(shelf) + (1 if key == "down" else -1 if key == "up" else 0)
        if key not in ("up", "down") or not 0 <= index < len(shelves):
            return False
        target = shelves[index].entry_card()
        if target is None:
            return False
        target.focus()
        return True

    def watch_scroll_y(self, old_value, new_value):
        super().watch_scroll_y(old_value, new_value)
        if self.is_mounted:
            self.request_artwork()

    def on_resize(self):
        self.request_artwork()

    def request_artwork(self):
        if not self._artwork_scheduled:
            self._artwork_scheduled = True
            self.call_after_refresh(self.load_visible_artwork)

    def load_visible_artwork(self):
        self._artwork_scheduled = False
        if not self.display or not self.is_mounted:
            return
        for card in self.query(GameCard):
            if card.source and not card.image_started and card.region.overlaps(self.region):
                card.load_artwork()
