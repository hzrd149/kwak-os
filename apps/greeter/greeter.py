#!/usr/bin/env python3
"""greetd greeter that signs in with Nostr identities, plus the sign-out dialog."""

import json
import os
from pathlib import Path
import shlex
import socket
import struct
import sys
import threading

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


def main_greeter():
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

    greetd = Greetd()

    class Greeter(Gtk.Window):
        def __init__(self):
            super().__init__(title="Sign in")
            self.busy = False
            self.selected = None
            self.pending = None
            self.connect("destroy", Gtk.main_quit)
            self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            frame.set_halign(Gtk.Align.CENTER)
            frame.set_valign(Gtk.Align.CENTER)
            frame.set_size_request(460, -1)
            frame.get_style_context().add_class("panel")
            frame.pack_start(self.stack, False, False, 0)
            self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=52)
            self.status.get_style_context().add_class("status")
            self.status.set_margin_top(16)
            frame.pack_start(self.status, False, False, 0)
            self.add(frame)
            self.build_people()
            self.build_unlock()
            self.build_key()
            self.build_create()
            self.build_backup()
            self.build_local()
            self.fullscreen()
            self.refresh()

        # Layout helpers --------------------------------------------------

        def page(self, name, title, subtitle):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            heading = Gtk.Label(label=title, xalign=0)
            heading.get_style_context().add_class("section-title")
            box.pack_start(heading, False, False, 0)
            if subtitle:
                detail = Gtk.Label(label=subtitle, xalign=0, wrap=True, max_width_chars=52)
                detail.get_style_context().add_class("description")
                box.pack_start(detail, False, False, 0)
            self.stack.add_named(box, name)
            return box, heading

        @staticmethod
        def entry(placeholder, secret=False):
            field = Gtk.Entry(placeholder_text=placeholder, visibility=not secret)
            field.set_activates_default(True)
            return field

        def buttons(self, box, primary, action, back=True):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_halign(Gtk.Align.END)
            row.set_margin_top(8)
            if back:
                button = Gtk.Button.new_with_mnemonic("_Back")
                button.connect("clicked", lambda *_: self.show("people"))
                row.pack_start(button, False, False, 0)
            go = Gtk.Button.new_with_mnemonic(primary)
            go.get_style_context().add_class("apply")
            go.set_can_default(True)
            go.connect("clicked", lambda *_: action())
            row.pack_start(go, False, False, 0)
            box.pack_start(row, False, False, 0)
            return go

        def show(self, name, focus=None):
            self.stack.set_visible_child_name(name)
            for widget in self.defaults.get(name, []):
                widget.grab_default()
            if focus is not None:
                focus.grab_focus()
            self.say("")

        def say(self, message, error=False):
            self.status.set_text(message)
            context = self.status.get_style_context()
            (context.add_class if error else context.remove_class)("error")

        def job(self, work, done, message):
            if self.busy:
                return
            self.busy = True
            self.stack.set_sensitive(False)
            self.say(message)

            def worker():
                try:
                    value, error = work(), None
                except client.UserError as failure:
                    value, error = None, str(failure)
                except OSError as failure:
                    value, error = None, f"Unexpected error: {failure}"
                GLib.idle_add(finished, value, error)

            def finished(value, error):
                self.busy = False
                self.stack.set_sensitive(True)
                if error:
                    self.say(error, error=True)
                else:
                    done(value)
                return False

            threading.Thread(target=worker, daemon=True).start()

        # Pages ------------------------------------------------------------

        def build_people(self):
            self.defaults = {}
            box, _ = self.page("people", "kwakOS", "Choose your identity to sign in.")
            self.people = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.pack_start(self.people, False, False, 6)
            for label, page in (
                ("Sign in with a Nostr key…", "key"),
                ("Create a new identity…", "create"),
                ("Local account…", "local"),
            ):
                button = Gtk.Button(label=label)
                button.get_style_context().add_class("link-row")
                button.connect("clicked", lambda _, p=page: self.open(p))
                box.pack_start(button, False, False, 0)

        def open(self, page):
            focus = {"key": self.key_text, "create": self.create_password,
                     "local": self.local_user}[page]
            self.show(page, focus)

        def refresh(self):
            self.job(lambda: client.request("list_known", timeout=10), self.listed,
                     "Loading identities…")

        def listed(self, people):
            for child in self.people.get_children():
                self.people.remove(child)
            for person in people:
                button = Gtk.Button()
                button.get_style_context().add_class("mode-row")
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                image = Gtk.Image.new_from_icon_name("avatar-default", Gtk.IconSize.DIALOG)
                if person["avatar"]:
                    try:
                        image.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(
                            person["avatar"], 48, 48, True))
                    except GLib.Error:
                        pass
                row.pack_start(image, False, False, 0)
                labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                name = Gtk.Label(label=person["name"] or person["username"], xalign=0)
                name.get_style_context().add_class("mode-name")
                labels.pack_start(name, False, False, 0)
                short = person["npub"][:16] + "…" + person["npub"][-6:]
                detail = Gtk.Label(label=short, xalign=0)
                detail.get_style_context().add_class("description")
                labels.pack_start(detail, False, False, 0)
                row.pack_start(labels, True, True, 0)
                button.add(row)
                button.connect("clicked", lambda _, p=person: self.choose(p))
                self.people.pack_start(button, False, False, 0)
            self.people.show_all()
            self.say("" if people else "No identities on this computer yet.")

        def choose(self, person):
            self.selected = person
            self.unlock_title.set_text(person["name"] or person["username"])
            bunker = person["method"] == "bunker"
            self.unlock_password.set_visible(not bunker)
            self.unlock_password.set_text("")
            self.unlock_hint.set_text(
                "Approve the login request in your remote signer." if bunker
                else "Enter the password for this identity on this computer."
            )
            self.show("unlock", None if bunker else self.unlock_password)
            if bunker:
                self.unlock()

        def build_unlock(self):
            box, self.unlock_title = self.page("unlock", "", "")
            self.unlock_hint = Gtk.Label(xalign=0, wrap=True)
            self.unlock_hint.get_style_context().add_class("description")
            box.pack_start(self.unlock_hint, False, False, 0)
            self.unlock_password = self.entry("Password", secret=True)
            box.pack_start(self.unlock_password, False, False, 0)
            self.unlock_password.set_no_show_all(True)
            self.defaults["unlock"] = [self.buttons(box, "_Sign in", self.unlock)]

        def unlock(self):
            username = self.selected["username"]
            password = self.unlock_password.get_text()
            waiting = ("Waiting for your signer to approve…"
                       if self.selected["method"] == "bunker" else "Unlocking…")
            self.job(lambda: client.request("unlock", username=username, password=password),
                     self.granted, waiting)

        def build_key(self):
            box, _ = self.page(
                "key", "Sign in with a key", "Paste an nsec, an ncryptsec, or a bunker:// URI."
            )
            self.key_text = self.entry("nsec1… / ncryptsec1… / bunker://…", secret=True)
            self.key_text.connect("changed", self.key_changed)
            box.pack_start(self.key_text, False, False, 0)
            self.key_password = self.entry("", secret=True)
            self.key_password.set_no_show_all(True)
            box.pack_start(self.key_password, False, False, 0)
            self.key_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=52)
            self.key_hint.get_style_context().add_class("description")
            box.pack_start(self.key_hint, False, False, 0)
            self.defaults["key"] = [self.buttons(box, "_Sign in", self.sign_in_key)]
            self.key_changed()

        def key_changed(self, *_):
            text = self.key_text.get_text().strip().lower()
            if text.startswith("bunker://"):
                placeholder, hint = None, (
                    "Your remote signer will be asked to confirm this identity. "
                    "It stays on this computer until you sign out of it."
                )
            elif text.startswith("ncryptsec"):
                placeholder, hint = "ncryptsec password", (
                    "Your encrypted key is kept on this computer; "
                    "sign in again with the same password."
                )
            else:
                placeholder, hint = "Password (optional)", (
                    "With a password, your key is kept on this computer as an ncryptsec. "
                    "Without one, you sign in as a temporary user that is deleted, "
                    "with all its files, when you log out."
                )
            self.key_password.set_visible(placeholder is not None)
            self.key_password.set_placeholder_text(placeholder or "")
            self.key_hint.set_text(hint)

        def sign_in_key(self):
            text = self.key_text.get_text().strip()
            password = self.key_password.get_text()
            lowered = text.lower()
            if lowered.startswith("bunker://"):
                work = lambda: client.request("login_bunker", uri=text)
                message = "Waiting for your signer to approve…"
            elif lowered.startswith("ncryptsec"):
                work = lambda: client.request("login_ncryptsec", ncryptsec=text, password=password)
                message = "Decrypting…"
            else:
                work = lambda: client.request("login_nsec", nsec=text, password=password)
                message = "Setting up your identity…"
            self.job(work, self.granted, message)

        def build_create(self):
            box, _ = self.page(
                "create", "Create a new identity",
                "A new Nostr key is generated on this computer. With a password it is kept "
                "here as an ncryptsec; without one you get a temporary guest identity that is "
                "deleted when you log out.",
            )
            self.create_password = self.entry("Password (optional)", secret=True)
            self.create_confirm = self.entry("Repeat password", secret=True)
            box.pack_start(self.create_password, False, False, 0)
            box.pack_start(self.create_confirm, False, False, 0)
            self.defaults["create"] = [self.buttons(box, "_Create", self.create)]

        def create(self):
            password = self.create_password.get_text()
            if password != self.create_confirm.get_text():
                self.say("The passwords do not match.", error=True)
                return
            self.job(lambda: client.request("create_identity", password=password),
                     self.created, "Creating your identity…")

        def build_backup(self):
            box, _ = self.page(
                "backup", "Back up your key",
                "This is the only time your key is shown. Without it you cannot use this "
                "identity again once it is removed from this computer.",
            )
            self.backup = Gtk.Label(xalign=0, selectable=True, wrap=True)
            self.backup.set_line_wrap_mode(2)  # Pango.WrapMode.CHAR
            self.backup.get_style_context().add_class("secret")
            box.pack_start(self.backup, False, False, 0)
            self.defaults["backup"] = [
                self.buttons(box, "_I saved it — continue", self.continue_backup, back=False)
            ]

        def created(self, grant):
            self.pending = grant
            text = grant["nsec"]
            if grant.get("ncryptsec"):
                text += f"\n\nEncrypted (NIP-49):\n{grant['ncryptsec']}"
            self.backup.set_text(text)
            self.show("backup")

        def continue_backup(self):
            grant, self.pending = self.pending, None
            self.backup.set_text("")
            self.granted(grant)

        def build_local(self):
            box, _ = self.page("local", "Local account", "Sign in with a Unix account.")
            self.local_user = self.entry("Username")
            self.local_password = self.entry("Password", secret=True)
            box.pack_start(self.local_user, False, False, 0)
            box.pack_start(self.local_password, False, False, 0)
            self.defaults["local"] = [self.buttons(box, "_Sign in", self.sign_in_local)]

        def sign_in_local(self):
            username = self.local_user.get_text().strip()
            password = self.local_password.get_text()
            self.job(lambda: greetd.login(username, password, session_command()),
                     lambda _: Gtk.main_quit(), "Starting session…")

        def granted(self, grant):
            for field in (self.key_text, self.key_password, self.create_password,
                          self.create_confirm, self.unlock_password):
                field.set_text("")
            self.job(lambda: greetd.login(grant["username"], grant["token"], session_command()),
                     lambda _: Gtk.main_quit(), "Starting session…")

    provider = Gtk.CssProvider()
    provider.load_from_path(str(Path(__file__).with_name("greeter.css")))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    window = Greeter()
    window.show_all()
    window.show("people")
    Gtk.main()
    return 0


def main_signout():
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE,
        text="Sign out of this computer?",
    )
    dialog.format_secondary_text(
        "Your account, home folder, and every file in it are permanently deleted from "
        "this computer. Your Nostr identity itself is not affected."
    )
    dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("_Delete and sign out", Gtk.ResponseType.ACCEPT).get_style_context() \
        .add_class("destructive-action")
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    answer = dialog.run()
    dialog.destroy()
    if answer != Gtk.ResponseType.ACCEPT:
        return 1
    try:
        client.request("signout", timeout=10)
    except client.UserError as error:
        failure = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR,
                                    buttons=Gtk.ButtonsType.CLOSE, text=str(error))
        failure.run()
        return 1
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--signout"]:
        raise SystemExit(main_signout())
    raise SystemExit(main_greeter())
