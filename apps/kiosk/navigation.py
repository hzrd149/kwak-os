"""Gamepad dispatch and spatial focus for Textual screens.

Screens, widgets and apps opt in to button behaviour by defining
``gamepad_<button>()`` methods that return True when they handled it. The
lookup runs from the focused widget up to the app, then falls back to the
defaults here: directions move focus spatially, A activates, and B sends
Escape so every screen's existing back binding applies.
"""
import time

from textual import events
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Checkbox, Input, OptionList, Switch, TextArea

from gamepad import FocusGate, GamepadReader, open_overlay

VECTORS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


class GamepadPressed(Message):
    def __init__(self, button, repeat=False):
        super().__init__()
        self.button = button
        self.repeat = repeat


def span(region, vertical):
    # Terminal cells are about twice as tall as wide.
    if vertical:
        return region.y * 2, (region.y + region.height) * 2
    return region.x, region.x + region.width


def gap(a, b):
    return max(0, b[0] - a[1], a[0] - b[1])


def move_focus(screen, direction, origin=None):
    """Focus the nearest widget in a direction, after steamduck's store.

    Distances are between edges, so a wide search box directly above a
    cover counts as aligned with it. The cross-axis distance weighs triple.
    """
    origin = origin or screen.focused
    # Scroll containers are focusable only to scroll; their children are targets.
    candidates = [widget for widget in screen.focus_chain if widget is not origin and not (
        isinstance(widget, ScrollableContainer) and any(child.focusable for child in widget.walk_children()))]
    if origin is None or not origin.region.area:
        if candidates:
            candidates[0].focus()
            return True
        return False
    dx, dy = VECTORS[direction]
    vertical = dy != 0
    sign = dx + dy
    o_along, o_across = span(origin.region, vertical), span(origin.region, not vertical)
    o_mid = sum(o_along) / 2
    best, best_score = None, None
    for widget in candidates:
        if not widget.region.area:
            continue
        along, across = span(widget.region, vertical), span(widget.region, not vertical)
        if (sum(along) / 2 - o_mid) * sign <= 0:
            continue
        distance = gap(o_along, along)
        offset = gap(o_across, across)
        # Ties prefer the candidate whose middle lines up best.
        centre = abs(sum(across) / 2 - sum(o_across) / 2) / 100
        score = distance + 3 * offset + centre
        if best_score is None or score < best_score:
            best, best_score = widget, score
    if best is None:
        return False
    # Entering a grid or shelf lands on its remembered selection.
    for ancestor in best.ancestors:
        entry = getattr(ancestor, "focus_entry", None)
        if callable(entry) and origin not in ancestor.walk_children():
            best = entry() or best
            break
    best.focus()
    return True


def activate(app, widget):
    if isinstance(widget, Button):
        widget.press()
    elif isinstance(widget, (Input, TextArea)):
        from osk import OnScreenKeyboard
        app.push_screen(OnScreenKeyboard(widget))
    elif isinstance(widget, (Switch, Checkbox)):
        widget.toggle()
    elif isinstance(widget, OptionList):
        widget.action_select()
    elif widget is not None and callable(getattr(widget, "activate", None)):
        widget.activate()
    else:
        simulate(app, "enter")


def simulate(app, key):
    event = events.Key(key, None)
    event._kwak_gamepad = True
    app.post_message(event)


def option_list_move(widget, direction):
    """Up/down walks an option list until its edge, then focus moves on."""
    if direction not in ("up", "down") or not widget.option_count:
        return False
    highlighted = widget.highlighted
    if direction == "up" and (highlighted or 0) <= 0:
        return False
    if direction == "down" and highlighted is not None and highlighted >= widget.option_count - 1:
        return False
    widget.action_cursor_up() if direction == "up" else widget.action_cursor_down()
    return True


class GamepadMixin:
    """Mix into an App before ``App``; call ``start_gamepad`` on mount."""

    input_mode = "keyboard"

    def start_gamepad(self, reader=None, guide_elsewhere=open_overlay):
        if reader is None:
            reader = GamepadReader(self.receive_gamepad, FocusGate(),
                                   on_guide_elsewhere=guide_elsewhere)
            reader.start()
        self.gamepad_reader = reader

    def stop_gamepad(self):
        reader = getattr(self, "gamepad_reader", None)
        if reader is not None:
            reader.stop()

    def receive_gamepad(self, event):
        """Reader thread entry point; post_message is thread-safe."""
        if not self.post_message(GamepadPressed(event.button, event.repeat)):
            raise RuntimeError("closed")

    def set_input_mode(self, mode):
        if mode != self.input_mode:
            self.input_mode = mode
            self.set_class(mode == "gamepad", "-gamepad")
            from hints import ButtonHints
            for screen in self.screen_stack:
                for hints in screen.query(ButtonHints):
                    hints.display = mode == "gamepad"

    async def on_event(self, event):
        if isinstance(event, (events.Key, events.MouseDown)) and not getattr(event, "_kwak_gamepad", False):
            self.set_input_mode("keyboard")
        await super().on_event(event)

    def on_gamepad_pressed(self, message):
        message.stop()
        self.set_input_mode("gamepad")
        self.gamepad_dispatch(message.button)

    def gamepad_handlers(self):
        screen = self.screen
        node = screen.focused
        while node is not None and node is not screen:
            yield node
            node = node.parent
        yield screen
        yield self

    def gamepad_dispatch(self, button):
        for node in self.gamepad_handlers():
            handler = getattr(node, "gamepad_" + button, None)
            if callable(handler) and handler():
                return True
        focused = self.screen.focused
        if button in VECTORS:
            if isinstance(focused, OptionList) and option_list_move(focused, button):
                return True
            navigate = getattr(focused, "navigate", None)
            if callable(navigate) and navigate(button):
                return True
            return move_focus(self.screen, button)
        if button == "a":
            activate(self, focused)
            return True
        if button == "b":
            simulate(self, "escape")
            return True
        return False


class ConfirmPress:
    """Press the same control again within a short window to confirm."""

    def __init__(self, window=3.0, clock=time.monotonic):
        self.window = window
        self.clock = clock
        self.pending = None

    def confirm(self, key):
        now = self.clock()
        if self.pending and self.pending[0] == key and now - self.pending[1] <= self.window:
            self.pending = None
            return True
        self.pending = (key, now)
        return False

    def cancel(self):
        self.pending = None

