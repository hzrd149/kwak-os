"""On-screen keyboard for controller text entry.

It edits the target field live, so a library filter narrows as letters are
chosen. X deletes, Y adds a space, Menu finishes and B closes keeping text.
"""
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

ROWS = ("1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm,.-")


class OnScreenKeyboard(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    CSS = """
    OnScreenKeyboard { align: center bottom; background: $background 40%; }
    #osk { width: auto; height: auto; padding: 0 1; border: heavy $primary; background: $surface; }
    #osk-preview { width: 100%; height: 1; color: $primary; text-style: bold; }
    .osk-row { width: auto; height: 1; }
    .osk-row Button { width: 5; min-width: 5; height: 1; border: none; margin: 0; padding: 0; }
    .osk-row Button:focus { background: $primary; color: $background; text-style: bold; }
    #osk-actions Button { width: 10; min-width: 10; }
    #osk-hint { height: 1; color: $foreground 60%; }
    """

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.shift = False

    def compose(self):
        with Vertical(id="osk"):
            yield Static("", id="osk-preview", markup=False)
            for index, row in enumerate(ROWS):
                with Horizontal(classes="osk-row"):
                    for char in row:
                        yield Button(char, classes="osk-key", name=char, id=f"osk-key-{ord(char)}")
            with Horizontal(classes="osk-row", id="osk-actions"):
                yield Button("Shift", id="osk-shift")
                yield Button("Space", id="osk-space")
                yield Button("Delete", id="osk-delete")
                yield Button("Clear", id="osk-clear")
                yield Button("Done", id="osk-done")
            yield Static("[X] Delete  [Y] Space  [Menu] Done  [B] Close", id="osk-hint", markup=False)

    def on_mount(self):
        self.show_preview()
        self.query(".osk-key").first().focus()

    @property
    def text(self):
        return self.target.text if isinstance(self.target, TextArea) else self.target.value

    def write(self, text):
        if isinstance(self.target, TextArea):
            self.target.text = text
        else:
            self.target.value = text
            self.target.cursor_position = len(text)
        self.show_preview()

    def show_preview(self):
        self.query_one("#osk-preview", Static).update((self.text or "")[-48:] + "▏")

    def type(self, char):
        limit = getattr(self.target, "max_length", None)
        if limit and len(self.text) >= limit:
            return
        self.write(self.text + char)

    def on_button_pressed(self, event):
        event.stop()
        button = event.button
        if button.has_class("osk-key"):
            char = button.name
            self.type(char.upper() if self.shift else char)
            if self.shift:
                self.toggle_shift()
        elif button.id == "osk-shift":
            self.toggle_shift()
        elif button.id == "osk-space":
            self.type(" ")
        elif button.id == "osk-delete":
            self.write(self.text[:-1])
        elif button.id == "osk-clear":
            self.write("")
        elif button.id == "osk-done":
            self.action_done()

    def toggle_shift(self):
        self.shift = not self.shift
        for key in self.query(".osk-key").results(Button):
            key.label = key.name.upper() if self.shift else key.name

    def action_close(self):
        self.dismiss(None)

    def action_done(self):
        target = self.target
        self.dismiss(None)
        if isinstance(target, Input):
            target.call_later(target.action_submit)

    def gamepad_x(self):
        self.write(self.text[:-1])
        return True

    def gamepad_y(self):
        self.type(" ")
        return True

    def gamepad_menu(self):
        self.action_done()
        return True
