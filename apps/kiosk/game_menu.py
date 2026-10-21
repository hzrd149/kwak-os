"""Per-game options: favourite, collections and removal.

Favourites and collections are stored on this device only (see
shell_state.Activity); kwakore has no field for them yet.
"""
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from shell_state import MAX_COLLECTIONS, clean_name


class GameMenu(ModalScreen):
    """Dismisses with "remove", True when activity changed, or None."""
    BINDINGS = [("escape", "close", "Close")]
    CSS = """
    GameMenu { align: center middle; }
    #game-menu { width: 60%; min-width: 40; max-width: 72; height: auto; max-height: 90%; padding: 0 2; border: heavy $primary; background: $surface; }
    #game-menu-title { height: auto; color: $primary; text-style: bold; margin: 1 0; }
    #game-menu-items { height: auto; max-height: 20; }
    #game-menu Button { width: 100%; height: 3; margin: 0; }
    #new-collection { margin: 1 0 0 0; }
    #game-menu-note { height: auto; color: $foreground 60%; margin-bottom: 1; }
    """

    def __init__(self, game, activity, removable=False):
        super().__init__()
        self.game = game
        self.address = game["address"]
        self.activity = activity
        self.removable = removable
        self.changed = False

    def compose(self):
        with Vertical(id="game-menu"):
            yield Static(self.game.get("name") or "Untitled game", id="game-menu-title", markup=False)
            with VerticalScroll(id="game-menu-items"):
                yield Button("", id="menu-favorite", variant="primary")
                for index, name in enumerate(sorted(self.activity.collections)):
                    yield Button("", id=f"menu-collection-{index}", name=name, classes="collection")
                yield Input(placeholder="New collection name…", id="new-collection", max_length=40)
                yield Button("Add to new collection", id="menu-create")
                if self.removable:
                    yield Button("Remove from My Games", id="menu-remove")
                yield Button("Close", id="menu-close")
            yield Static("Favourites and collections are kept on this device.", id="game-menu-note", markup=False)

    def on_mount(self):
        self.refresh_labels()
        self.query_one("#menu-favorite", Button).focus()

    def refresh_labels(self):
        favorite = self.activity.favorite(self.address)
        self.query_one("#menu-favorite", Button).label = "★ Remove from Favourites" if favorite else "☆ Add to Favourites"
        members = set(self.activity.collections_for(self.address))
        for button in self.query(".collection").results(Button):
            button.label = ("✓ " if button.name in members else "+ ") + button.name
        full = len(self.activity.collections) >= MAX_COLLECTIONS
        self.query_one("#menu-create", Button).disabled = full

    def on_button_pressed(self, event):
        event.stop()
        button = event.button
        if button.id == "menu-favorite":
            self.activity.toggle_favorite(self.address)
            self.changed = True
        elif button.has_class("collection"):
            if button.name in self.activity.collections_for(self.address):
                self.activity.remove_from_collection(button.name, self.address)
            else:
                self.activity.add_to_collection(button.name, self.address)
            self.changed = True
        elif button.id == "menu-create":
            self.create_collection()
            return
        elif button.id == "menu-remove":
            self.dismiss("remove")
            return
        elif button.id == "menu-close":
            self.action_close()
            return
        self.refresh_labels()

    def on_input_submitted(self, event):
        event.stop()
        self.create_collection()

    def create_collection(self):
        field = self.query_one("#new-collection", Input)
        name = clean_name(field.value)
        if not name:
            field.focus()
            return
        if self.activity.add_to_collection(name, self.address):
            self.changed = True
            # Collections are listed when the menu opens; close to show it.
            self.dismiss(True)

    def action_close(self):
        self.dismiss(True if self.changed else None)
