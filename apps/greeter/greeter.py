#!/usr/bin/env python3
"""Terminal greetd greeter for Nostr identities, plus the sign-out dialog."""

import asyncio
import json
import os
import shlex
import socket
import struct
import sys
import threading
import time

from rich.text import Text
from textual import work
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.theme import Theme
from textual.widgets import (Button, ContentSwitcher, Footer, Input, LoadingIndicator,
                             OptionList, Static)
from textual.widgets.option_list import Option

import client


class Greetd:
    """greetd IPC: native-endian u32 length followed by a JSON message."""

    def __init__(self, path=None):
        self.path = path or os.environ.get("GREETD_SOCK", "")

    def _send(self, sock, message):
        payload = json.dumps(message).encode()
        sock.sendall(struct.pack("=I", len(payload)) + payload)
        header = self._read(sock, 4)
        return json.loads(self._read(sock, struct.unpack("=I", header)[0]))

    @staticmethod
    def _read(sock, size):
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise client.UserError("greetd closed the connection.")
            data += chunk
        return data

    def login(self, username, secret, command):
        """Authenticate ``username`` answering every secret prompt with ``secret``."""
        if not self.path:
            raise client.UserError("Not running under greetd (GREETD_SOCK is unset).")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(self.path)
            reply = self._send(sock, {"type": "create_session", "username": username})
            answered = False
            while reply.get("type") == "auth_message":
                if reply.get("auth_message_type") in ("secret", "visible"):
                    if answered:
                        break
                    answered = True
                    reply = self._send(
                        sock, {"type": "post_auth_message_response", "response": secret}
                    )
                else:
                    reply = self._send(sock, {"type": "post_auth_message_response"})
            if reply.get("type") == "success":
                reply = self._send(sock, {"type": "start_session", "cmd": command, "env": []})
                if reply.get("type") == "success":
                    return
            self._send(sock, {"type": "cancel_session"})
            detail = reply.get("description") or reply.get("auth_message") or "Login failed."
            if reply.get("error_type") == "auth_error":
                detail = "Login was not accepted."
            raise client.UserError(detail)


def session_command():
    return shlex.split(os.environ.get("KWAK_SESSION", "uwsm start hyprland-uwsm.desktop"))


THEME = Theme(
    name="kwak",
    primary="#33ff66",
    secondary="#1f9940",
    accent="#33ff66",
    foreground="#c8ffd4",
    background="#000000",
    surface="#030803",
    panel="#0a1a0d",
    error="#ff5f5f",
    warning="#ffcc33",
    success="#33ff66",
    dark=True,
)

CSS = """
Screen { align: center middle; background: $background; }
#panel {
    width: 100%; max-width: 64; height: auto; max-height: 100%;
    border: heavy $primary; border-title-align: left; padding: 1 2;
    scrollbar-size-vertical: 1;
    background: $surface;
}
ContentSwitcher, ContentSwitcher > Vertical { height: auto; }
.title { text-style: bold; color: $primary; }
.hint { color: $foreground 70%; margin-bottom: 1; }
Input { margin-bottom: 1; border: tall $primary 40%; background: $background; }
Input:focus { border: tall $primary; }
OptionList { height: auto; border: none; padding: 0; background: $surface; }
OptionList > .option-list--option-highlighted { background: $primary 25%; }
LoadingIndicator { height: 3; color: $primary; }
.buttons { height: auto; align-horizontal: right; }
Button { border: none; height: 1; min-width: 10; margin-left: 2; }
#status { margin-top: 1; color: $foreground 70%; display: none; }
#status.shown { display: block; }
#status.error { color: $error; }
#swipe { margin-top: 1; color: $primary; display: none; }
#swipe.shown { display: block; }
"""


def buttons(primary=None, primary_id=None, back="Back", variant="primary"):
    """A right-aligned row: a Back (or Cancel) button and an optional main button."""
    row = [Button(back, classes="back")]
    if primary:
        row.append(Button(primary, id=primary_id, variant=variant))
    return Horizontal(*row, classes="buttons")


class CardPolled(Message):
    """A wait_card result, posted from the card watcher thread."""

    def __init__(self, result, first=False):
        super().__init__()
        self.result = result
        self.first = first


class GreeterApp(App):
    """Sign-in screen: accounts on this computer, then other ways to sign in."""

    TITLES = {
        "people": "KWAKOS", "unlock": "UNLOCK", "add": "SIGN IN", "create": "NEW ACCOUNT",
        "key": "EXISTING ACCOUNT", "bunker": "REMOTE SIGNER", "local": "LINUX USER",
        "waiting": "REMOTE SIGNER", "welcome": "WELCOME", "card": "SWIPE CARD",
    }
    BACK = {"unlock": "people", "add": "people", "create": "add", "key": "add",
            "bunker": "add", "local": "add", "card": "people"}
    ADD = (
        ("create", "New account", "Generate a new Nostr key on this computer."),
        ("key", "Existing account", "Sign in with your nsec or ncryptsec."),
        ("bunker", "Remote signer", "Sign in with a bunker:// URI and approve it in your signer."),
        ("local", "Linux user", "Sign in to a local account, such as the kwak administrator."),
    )
    ADD_FOCUS = {"create": "create-password", "key": "key-text", "bunker": "bunker-uri",
                 "local": "local-user"}
    CSS = CSS
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, request=client.request, greetd=None, command=None, switch=None):
        super().__init__()
        self.request = request
        # A switch greeter's instance: it was opened by a swipe during a session,
        # on its own VT, and closes once the swipe is handled or cancelled.
        self.switch = switch
        self.greetd = greetd or Greetd()
        self.command = command or session_command()
        self.busy = False
        self.selected = None
        self.people = []
        self.waiting_back = "people"
        self.card = None
        self.stopping = threading.Event()
        self.started = time.monotonic()

    def compose(self):
        with VerticalScroll(id="panel"):
            with ContentSwitcher(initial="people", id="pages"):
                with Vertical(id="people"):
                    yield Static(
                        "Choose an account or swipe a card. Esc goes back to the locked "
                        "session." if self.switch else "Choose who is signing in.",
                        classes="hint")
                    yield OptionList(id="people-list")
                    yield Static("▮ Or swipe your card to sign in.", id="swipe")
                with Vertical(id="unlock"):
                    yield Static(id="unlock-name", classes="title")
                    yield Static("Enter this account's password to decrypt its key.",
                                 classes="hint")
                    yield Input(placeholder="Password", password=True, id="unlock-password")
                    yield buttons("Sign in", "unlock-go")
                with Vertical(id="add"):
                    yield Static("Choose how to sign in.", classes="hint")
                    yield OptionList(*(
                        Option(Text.assemble((f"> {name}", "bold"), "\n  ", (detail, "dim")),
                               id=page)
                        for page, name, detail in self.ADD
                    ), id="add-list")
                    yield buttons()
                with Vertical(id="create"):
                    yield Static(
                        "With a password, your new key is kept on this computer as an "
                        "ncryptsec. Without one, you sign in as a guest that is deleted, "
                        "with its key and all its files, when you log out.", classes="hint")
                    yield Input(placeholder="Password (optional)", password=True,
                                id="create-password")
                    yield Input(placeholder="Repeat password", password=True,
                                id="create-confirm")
                    yield buttons("Create", "create-go")
                with Vertical(id="key"):
                    yield Static("Paste your nsec or ncryptsec.", classes="hint")
                    yield Input(placeholder="nsec1… or ncryptsec1…", password=True, id="key-text")
                    yield Input(password=True, id="key-password")
                    yield Static(id="key-hint", classes="hint")
                    yield buttons("Sign in", "key-go")
                with Vertical(id="bunker"):
                    yield Static("Paste the bunker:// URI from your signer. It is kept on "
                                 "this computer until you sign out of it.", classes="hint")
                    yield Input(placeholder="bunker://…", password=True, id="bunker-uri")
                    yield buttons("Connect", "bunker-go")
                with Vertical(id="local"):
                    yield Static("Sign in with a local account.", classes="hint")
                    yield Input(placeholder="Username", id="local-user")
                    yield Input(placeholder="Password", password=True, id="local-password")
                    yield buttons("Sign in", "local-go")
                with Vertical(id="waiting"):
                    yield Static(id="waiting-name", classes="title")
                    yield LoadingIndicator()
                    yield Static("Connecting to your signer. Approve the login request there.",
                                 classes="hint")
                    yield buttons(back="Cancel")
                with Vertical(id="card"):
                    yield Static(id="card-name", classes="title")
                    yield Static(id="card-hint", classes="hint")
                    yield Input(password=True, id="card-password")
                    yield Input(placeholder="Repeat password", password=True,
                                id="card-confirm")
                    yield buttons("Sign in", "card-go")
                with Vertical(id="welcome"):
                    yield Static(id="welcome-name", classes="title")
                    yield LoadingIndicator()
                    yield Static("Starting your desktop…", classes="hint")
            yield Static(id="status")
        yield Footer()

    def on_mount(self):
        self.register_theme(THEME)
        self.theme = "kwak"
        self.key_changed()
        self.show("people")
        self.refresh_people()
        # A plain thread, so waiting for a swipe never holds up the app's workers.
        threading.Thread(target=self.watch_cards, daemon=True).start()

    def on_unmount(self):
        self.stopping.set()

    # Helpers -----------------------------------------------------------------

    @property
    def page(self):
        return self.query_one("#pages", ContentSwitcher).current

    def field(self, name):
        return self.query_one(f"#{name}", Input)

    def check_action(self, action, parameters):
        if action == "back":
            return (self.page in self.BACK or self.page == "waiting"
                    or (self.switch is not None and self.page == "people"))
        return True

    def show(self, page, focus=None):
        self.query_one("#pages", ContentSwitcher).current = page
        self.refresh_bindings()
        self.query_one("#panel").border_title = (
            "SWITCH ACCOUNT" if self.switch and page == "people" else self.TITLES[page])
        self.say("")
        if focus:
            self.query_one(focus).focus()
        else:
            # Otherwise the page's first control: its list, field, or button.
            controls = self.query_one(f"#{page}").query("OptionList, Input, Button")
            if controls:
                controls.first().focus()

    def say(self, message, error=False):
        status = self.query_one("#status", Static)
        status.update(message)
        status.set_class(error, "error")
        status.set_class(bool(message), "shown")

    def job(self, work, done, message, failed=None):
        """Run blocking ``work`` in a thread, then ``done(result)``; on error, ``failed()``."""
        if self.busy:
            return
        self.busy = True
        pages = self.query_one("#pages")
        # Disabling the page drops keyboard focus, so it is put back afterwards.
        focused = self.focused
        # The loading page stays usable so that its Cancel button works.
        pages.disabled = self.page != "waiting"
        self.say(message)

        def finish():
            self.busy = pages.disabled = False
            if focused is not None and self.focused is None:
                focused.focus()

        async def run():
            try:
                value = await asyncio.to_thread(work)
            except (client.UserError, OSError) as error:
                finish()
                if failed:
                    failed()
                self.say(str(error), error=True)
                return
            finish()
            done(value)

        self.run_worker(run(), group="job")

    # Accounts on this computer -----------------------------------------------

    def refresh_people(self):
        self.job(lambda: self.request("list_known", timeout=10), self.listed,
                 "Loading accounts…")

    def listed(self, people):
        self.people = people
        options = self.query_one("#people-list", OptionList)
        options.clear_options()
        for index, person in enumerate(people):
            kind = ("Guest" if person.get("temporary") else
                    "Remote signer" if person["method"] == "bunker" else "Password")
            npub = f"{person['npub'][:12]}…{person['npub'][-6:]}"
            options.add_option(Option(Text.assemble(
                (f"@ {person['name'] or person['username']}", "bold"), "\n  ",
                (f"{npub} · {kind}", "dim"),
            ), id=str(index)))
        if people:
            options.add_option(None)
        options.add_option(Option(Text("+ Sign in with another account…"), id="add"))
        options.highlighted = 0
        options.focus()
        self.say("")

    def on_option_list_option_selected(self, event):
        if event.option_list.id == "add-list":
            self.show(event.option.id, f"#{self.ADD_FOCUS[event.option.id]}")
        elif event.option.id == "add":
            self.show("add")
        else:
            self.choose(self.people[int(event.option.id)])

    def choose(self, person):
        self.selected = person
        name = person["name"] or person["username"]
        unlock = lambda: self.request("unlock", username=person["username"])
        if person.get("temporary"):
            # A guest has no password and no stored key, so it opens directly.
            self.job(unlock, self.granted, "Starting session…")
        elif person["method"] == "bunker":
            self.wait_for_signer(name, unlock, back="people")
        else:
            self.query_one("#unlock-name", Static).update(name)
            self.field("unlock-password").value = ""
            self.show("unlock", "#unlock-password")

    def unlock(self):
        username = self.selected["username"]
        password = self.field("unlock-password").value
        self.job(lambda: self.request("unlock", username=username, password=password),
                 self.granted, "Decrypting…")

    # Swipe cards -------------------------------------------------------------

    def watch_cards(self):
        """Long-poll kwak-userd for card swipes while the greeter is open."""
        # after=0 also picks up a swipe from just before the greeter started,
        # such as the one that opened a switch greeter.
        after, first = 0, True
        while not self.stopping.is_set():
            try:
                result = self.request("wait_card", timeout=40, after=after,
                                      wait=0 if first else 30)
            except client.UserError:
                self.stopping.wait(5)
                continue
            after = result.get("seq", after)
            # post_message is thread-safe and never waits on the app.
            self.post_message(CardPolled(result, first))
            first = False

    def on_card_polled(self, message):
        if self.stopping.is_set():
            return
        try:
            if message.first and self.switch and not message.result.get("card"):
                # Restarted after its session ended, with no swipe to handle.
                self.close_switch()
                return
            if (self.switch and message.result.get("on_screen") is False
                    and time.monotonic() - self.started > 15 and not self.busy):
                # Another swipe switched away from this VT; nobody is using it.
                self.close_switch()
                return
            self.card_polled(message.result)
        except NoMatches:  # The app is closing and its widgets are gone.
            pass

    def close_switch(self):
        """Close this switch greeter; kwak-userd goes back to a session."""
        async def run():
            try:
                await asyncio.to_thread(self.request, "switch_done", instance=self.switch,
                                        timeout=10)
            except client.UserError as error:
                self.say(str(error), error=True)
                return
            self.exit()

        self.run_worker(run(), group="switch")

    def card_polled(self, result):
        self.query_one("#swipe").set_class(bool(result.get("reader")), "shown")
        if result.get("card"):
            self.card_swiped(result["card"])

    def card_swiped(self, card):
        if self.busy or self.page in ("waiting", "welcome"):
            self.say("Finish or cancel this sign-in first, then swipe again.", error=True)
            return
        if card.get("error"):
            self.say("That card couldn't be read. Swipe it again.", error=True)
            return
        self.card = card
        for name in ("card-password", "card-confirm"):
            self.field(name).value = ""
        if card.get("signer"):
            # An SKC3 card: its bunker is already paired, so just wait for it.
            self.selected = None
            self.wait_for_signer(card.get("name") or "Bunker card",
                                 lambda: self.request("card_login", card=card["id"]),
                                 back="people")
            return
        if card["password"] == "none":
            # An account already on this computer: the card itself is the key.
            self.selected = {"username": card["username"], "name": card.get("name", "")}
            self.job(lambda: self.request("card_login", card=card["id"]), self.granted,
                     "Starting session…")
            return
        new = card["password"] == "optional"
        self.query_one("#card-name", Static).update(
            "New card" if new else card.get("name") or "Encrypted card")
        self.query_one("#card-hint", Static).update(
            "With a password, this card's key is kept on this computer as an ncryptsec. "
            "Without one, you sign in as a guest that is deleted, with all its files, "
            "when you log out." if new else
            "Enter the card's password to decrypt its key. The encrypted key is kept on "
            "this computer, so the password alone signs you in next time.")
        self.field("card-password").placeholder = "Password (optional)" if new else "Card password"
        self.field("card-confirm").display = new
        self.show("card", "#card-password")

    def sign_in_card(self):
        card, password = self.card, self.field("card-password").value
        if card["password"] == "optional" and password != self.field("card-confirm").value:
            self.say("The passwords do not match.", error=True)
            return
        self.job(lambda: self.request("card_login", card=card["id"], password=password),
                 self.granted, "Decrypting…" if password else "Setting up your account…")

    # Another account ---------------------------------------------------------

    def create(self):
        password = self.field("create-password").value
        if password != self.field("create-confirm").value:
            self.say("The passwords do not match.", error=True)
            return
        self.job(lambda: self.request("create_identity", password=password),
                 self.granted, "Creating your account…")

    def on_input_changed(self, event):
        if event.input.id == "key-text":
            self.key_changed()

    def key_changed(self):
        if self.field("key-text").value.strip().lower().startswith("ncryptsec"):
            placeholder, hint = "ncryptsec password", (
                "Your encrypted key is kept on this computer; "
                "sign in again with the same password."
            )
        else:
            placeholder, hint = "Password (optional)", (
                "With a password, your key is kept on this computer as an ncryptsec. "
                "Without one, you sign in as a guest that is deleted, "
                "with all its files, when you log out."
            )
        self.field("key-password").placeholder = placeholder
        self.query_one("#key-hint", Static).update(hint)

    def sign_in_key(self):
        text = self.field("key-text").value.strip()
        password = self.field("key-password").value
        if text.lower().startswith("ncryptsec"):
            work = lambda: self.request("login_ncryptsec", ncryptsec=text, password=password)
            message = "Decrypting…"
        else:
            work = lambda: self.request("login_nsec", nsec=text, password=password)
            message = "Setting up your account…"
        self.job(work, self.granted, message)

    def sign_in_bunker(self):
        uri = self.field("bunker-uri").value.strip()
        if not uri.lower().startswith("bunker://"):
            self.say("Paste a bunker:// URI.", error=True)
            return
        self.wait_for_signer("Remote signer", lambda: self.request("login_bunker", uri=uri),
                             back="bunker")

    def sign_in_local(self):
        username = self.field("local-user").value.strip()
        password = self.field("local-password").value
        self.job(lambda: self.greetd.login(username, password, self.command),
                 lambda _: self.exit(), "Starting session…")

    # Waiting for a remote signer ---------------------------------------------

    def wait_for_signer(self, name, work, back):
        """Show the loading page until the signer answers; errors return to ``back``."""
        self.query_one("#waiting-name", Static).update(name)
        self.waiting_back = back
        self.query_one("#waiting .back").disabled = False
        self.show("waiting")
        self.job(work, self.granted, "", failed=lambda: self.show(self.waiting_back))

    def cancel_waiting(self):
        # kwak-userd finishes the request; the greeter ignores its result.
        self.workers.cancel_group(self, "job")
        self.busy = False
        self.show(self.waiting_back)

    # Navigation and the session ----------------------------------------------

    def action_back(self):
        if self.page == "waiting":
            if not self.query_one("#waiting .back").disabled:
                self.cancel_waiting()
        elif not self.busy and self.page in self.BACK:
            self.show(self.BACK[self.page])
        elif not self.busy and self.switch and self.page == "people":
            self.close_switch()

    def on_button_pressed(self, event):
        if event.button.has_class("back"):
            self.action_back()
        else:
            self.submit()

    def on_input_submitted(self, event):
        self.submit()

    def submit(self):
        action = {"unlock": self.unlock, "create": self.create, "key": self.sign_in_key,
                  "bunker": self.sign_in_bunker, "local": self.sign_in_local,
                  "card": self.sign_in_card}.get(self.page)
        if action and not self.busy:
            action()

    def granted(self, grant):
        for name in ("key-text", "key-password", "bunker-uri", "unlock-password",
                     "create-password", "create-confirm", "card-password", "card-confirm"):
            self.field(name).value = ""
        self.card = None
        if grant.get("switched"):
            # The account was already signed in; kwak-userd switched to it.
            if self.switch:
                self.close_switch()
            else:
                self.show("people")
                self.say(f"Switched to {grant.get('name') or grant['username']}.")
            return
        # Once greetd is starting the session it is too late to cancel.
        self.query_one("#waiting .back").disabled = True
        waiting = self.page == "waiting"
        name = grant.get("name") or (
            self.selected.get("name") if self.selected and
            self.selected.get("username") == grant["username"] else ""
        ) or grant["username"]
        self.query_one("#welcome-name", Static).update(f"Welcome {name}")
        self.show("welcome")
        self.job(lambda: self.greetd.login(grant["username"], grant["token"], self.command),
                 lambda _: self.exit(), "",
                 failed=lambda: self.show(self.waiting_back if waiting else "people"))


class SignOutApp(App):
    """Confirm, then delete this account from the computer."""

    CSS = CSS
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, request=client.request):
        super().__init__()
        self.request = request

    def compose(self):
        with VerticalScroll(id="panel") as panel:
            panel.border_title = "SIGN OUT"
            yield Static("Sign out of this computer?", classes="title")
            yield Static(
                "Your account, home folder, and every file in it are permanently deleted "
                "from this computer. Your Nostr identity itself is not affected.",
                classes="hint",
            )
            yield buttons("Delete and sign out", "signout", back="Cancel", variant="error")
            yield Static(id="status")
        yield Footer()

    def on_mount(self):
        self.register_theme(THEME)
        self.theme = "kwak"
        self.query_one(".back").focus()

    def action_cancel(self):
        self.exit(1)

    def on_button_pressed(self, event):
        if event.button.has_class("back"):
            self.exit(1)
        else:
            self.sign_out()

    @work(exclusive=True)
    async def sign_out(self):
        self.query_one(".buttons").disabled = True
        status = self.query_one("#status", Static)
        status.update("Signing out…")
        status.add_class("shown")
        try:
            await asyncio.to_thread(self.request, "signout", timeout=10)
        except client.UserError as error:
            status.update(str(error))
            status.add_class("error", "shown")
            self.query_one(".buttons").disabled = False
            return
        # The removal job ends this session; exit in case it has not yet.
        self.exit(0)


if __name__ == "__main__":
    if sys.argv[1:] == ["--signout"]:
        app = SignOutApp()
    elif sys.argv[1:2] == ["--switch"] and len(sys.argv) == 3:
        app = GreeterApp(switch=sys.argv[2])
    else:
        app = GreeterApp()
    result = app.run()
    raise SystemExit(result if isinstance(result, int) else 0)
