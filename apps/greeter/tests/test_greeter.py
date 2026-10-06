"""greetd IPC tests against a fake greetd socket, and headless UI tests."""

import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import queue
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client  # noqa: E402
import greeter  # noqa: E402


class FakeGreetd:
    """Answers each request from a script of replies and records the requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "greetd.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        connection, _ = self.server.accept()
        with connection:
            while self.replies:
                header = connection.recv(4)
                if not header:
                    return
                size = struct.unpack("=I", header)[0]
                self.requests.append(json.loads(connection.recv(size)))
                payload = json.dumps(self.replies.pop(0)).encode()
                connection.sendall(struct.pack("=I", len(payload)) + payload)

    def close(self):
        self.thread.join(timeout=5)
        self.server.close()
        self.directory.cleanup()


SECRET_PROMPT = {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"}
SUCCESS = {"type": "success"}


class GreetdTests(unittest.TestCase):
    def login(self, replies, secret="token"):
        fake = FakeGreetd(replies)
        self.addCleanup(fake.close)
        error = None
        try:
            greeter.Greetd(fake.path).login("n3bf0c63fcb", secret, ["uwsm", "start", "x"])
        except client.UserError as failure:
            error = str(failure)
        fake.thread.join(timeout=5)
        return [r["type"] for r in fake.requests], fake.requests, error

    def test_token_answers_prompt_and_starts_session(self):
        types, requests, error = self.login([SECRET_PROMPT, SUCCESS, SUCCESS])
        self.assertIsNone(error)
        self.assertEqual(types, ["create_session", "post_auth_message_response", "start_session"])
        self.assertEqual(requests[0]["username"], "n3bf0c63fcb")
        self.assertEqual(requests[1]["response"], "token")
        self.assertEqual(requests[2]["cmd"], ["uwsm", "start", "x"])

    def test_info_messages_are_acknowledged_without_secret(self):
        info = {"type": "auth_message", "auth_message_type": "info", "auth_message": "hi"}
        types, requests, error = self.login([info, SECRET_PROMPT, SUCCESS, SUCCESS])
        self.assertIsNone(error)
        self.assertNotIn("response", requests[1])
        self.assertEqual(requests[2]["response"], "token")

    def test_rejected_token_cancels_session(self):
        rejected = {"type": "error", "error_type": "auth_error", "description": "nope"}
        types, _, error = self.login([SECRET_PROMPT, rejected, SUCCESS])
        self.assertEqual(error, "Login was not accepted.")
        self.assertEqual(types[-1], "cancel_session")

    def test_secret_is_sent_only_once(self):
        types, _, error = self.login([SECRET_PROMPT, SECRET_PROMPT, SUCCESS])
        self.assertIsNotNone(error)
        self.assertEqual(types.count("post_auth_message_response"), 1)
        self.assertEqual(types[-1], "cancel_session")

    def test_requires_greetd(self):
        with self.assertRaisesRegex(client.UserError, "GREETD_SOCK"):
            greeter.Greetd("").login("kwak", "x", ["true"])


class ClientTests(unittest.TestCase):
    def test_unavailable_service(self):
        with self.assertRaisesRegex(client.UserError, "unavailable"):
            client.request("list_known", path="/nonexistent/kwak-userd.sock", timeout=1)


PEOPLE = [
    {"username": "n3bf0c63fcb", "npub": "npub1" + "a" * 58, "name": "fiatjaf",
     "method": "ncryptsec", "avatar": "", "temporary": False},
    {"username": "n82341f882b", "npub": "npub1" + "b" * 58, "name": "jack",
     "method": "bunker", "avatar": "", "temporary": False},
    {"username": "nab12cd34ef", "npub": "npub1" + "c" * 58, "name": "",
     "method": "nsec", "avatar": "", "temporary": True},
]


class FakeUserd:
    """Records requests; a bunker unlock waits until ``signer`` is set."""

    def __init__(self):
        self.calls = []
        self.signer = threading.Event()
        self.reader = False
        self.swipes = queue.Queue()

    def __call__(self, op, timeout=180, **fields):
        if op == "wait_card":
            # Not recorded: the greeter polls this all the time.
            try:
                card = self.swipes.get(timeout=0.2)
            except queue.Empty:
                card = None
            return {"reader": self.reader, "card": card, "seq": 0}
        self.calls.append((op, fields))
        if op == "switch_done":
            return {"closed": True}
        if getattr(self, "switched", False) and op in ("unlock", "card_login"):
            return {"username": fields.get("username", "n3bf0c63fcb"), "switched": True,
                    "name": "fiatjaf"}
        if op == "card_login":
            if fields.get("password") == "wrong":
                raise client.UserError("Wrong password.")
            return {"username": "ncard000000", "token": "t", "name": "Card"}
        if getattr(self, "fail_unlock", False) and op == "unlock":
            raise client.UserError("Wrong password.")
        if op == "list_known":
            return PEOPLE
        if op == "unlock" and fields["username"] == "n82341f882b":
            if not self.signer.wait(5):
                raise client.UserError("The signer did not answer.")
        if op == "create_identity" and len(fields["password"]) not in (0, 6):
            raise client.UserError("Choose a password of at least 4 characters.")
        return {"username": fields.get("username", "nnewnewnew0"), "token": "t"}


class RecordingGreetd:
    def __init__(self):
        self.logins = []

    def login(self, username, secret, command):
        self.logins.append((username, secret))


class GreeterAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.userd = FakeUserd()
        self.greetd = RecordingGreetd()
        self.app = greeter.GreeterApp(self.userd, self.greetd, ["true"])

    async def settle(self, pilot):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

    def ops(self):
        return [op for op, _ in self.userd.calls]

    async def test_account_list_has_focus_once_loaded(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            self.assertEqual(pilot.app.focused.id, "people-list")

    async def test_failed_password_keeps_focus_in_the_form(self):
        self.userd.fail_unlock = True
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("enter", *"wrong", "enter")
            await self.settle(pilot)
            self.assertEqual(pilot.app.focused.id, "unlock-password")

    async def test_guest_opens_without_a_password(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("down", "down", "enter")
            await self.settle(pilot)
        self.assertEqual(self.userd.calls[-1], ("unlock", {"username": "nab12cd34ef"}))
        self.assertEqual(self.greetd.logins, [("nab12cd34ef", "t")])

    async def test_password_account_asks_for_its_password(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("enter")
            self.assertEqual(pilot.app.page, "unlock")
            await pilot.press(*"hunter2", "enter")
            await self.settle(pilot)
        self.assertEqual(self.userd.calls[-1],
                         ("unlock", {"username": "n3bf0c63fcb", "password": "hunter2"}))
        self.assertEqual(self.greetd.logins, [("n3bf0c63fcb", "t")])

    async def test_remote_signer_shows_loading_until_it_answers(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("down", "enter")
            await pilot.pause()
            self.assertEqual(pilot.app.page, "waiting")
            self.assertEqual(self.greetd.logins, [])
            self.userd.signer.set()
            await self.settle(pilot)
        self.assertEqual(self.greetd.logins, [("n82341f882b", "t")])

    async def test_cancel_leaves_the_loading_page(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("down", "enter")
            await pilot.pause()
            await pilot.click("#waiting .back")
            await pilot.pause()
            self.assertEqual(pilot.app.page, "people")
            self.assertFalse(pilot.app.busy)
            self.userd.signer.set()
            await self.settle(pilot)
            self.assertEqual(self.greetd.logins, [])

    async def test_new_account_password_is_optional(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("end", "enter")
            self.assertEqual(pilot.app.page, "add")
            await pilot.press("enter")
            self.assertEqual(pilot.app.page, "create")
            await pilot.press(*"secret", "tab", *"secreX", "enter")
            await pilot.pause()
            self.assertIn("do not match", str(pilot.app.query_one("#status").render()))
            pilot.app.field("create-password").value = ""
            pilot.app.field("create-confirm").value = ""
            await pilot.click("#create-go")
            await self.settle(pilot)
        self.assertEqual(self.userd.calls[-1], ("create_identity", {"password": ""}))
        self.assertEqual(self.greetd.logins, [("nnewnewnew0", "t")])

    async def test_escape_goes_back(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("end", "enter", "down", "enter")
            self.assertEqual(pilot.app.page, "key")
            await pilot.press("escape")
            self.assertEqual(pilot.app.page, "add")
            await pilot.press("escape")
            self.assertEqual(pilot.app.page, "people")

    async def wait_for(self, pilot, condition):
        for _ in range(100):
            if condition():
                return
            await pilot.pause(0.02)
        self.fail("timed out")

    async def test_swipe_hint_shows_only_with_a_reader(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            hint = pilot.app.query_one("#swipe")
            self.assertFalse(hint.has_class("shown"))
            self.userd.reader = True
            await self.wait_for(pilot, lambda: hint.has_class("shown"))

    async def test_known_card_signs_in_on_swipe(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            self.userd.swipes.put({"id": "c1", "format": "SKC1", "username": "ncard000000",
                                   "name": "Card", "known": True, "password": "none"})
            await self.wait_for(pilot, lambda: self.greetd.logins)
        self.assertEqual(self.userd.calls[-1], ("card_login", {"card": "c1"}))
        self.assertEqual(self.greetd.logins, [("ncard000000", "t")])

    async def test_bunker_card_waits_for_its_signer(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            pilot.app.card_polled({"card": {"id": "c7", "format": "SKC3", "password": "none",
                                            "signer": True}})
            self.assertEqual(pilot.app.page, "waiting")
            await self.settle(pilot)
        self.assertEqual(self.userd.calls[-1], ("card_login", {"card": "c7"}))
        self.assertEqual(self.greetd.logins, [("ncard000000", "t")])

    async def test_new_card_password_is_optional(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            pilot.app.card_polled({"card": {"id": "c2", "format": "SKC1", "known": False,
                                            "username": "ncard000000", "password": "optional"}})
            await pilot.pause()
            self.assertEqual((pilot.app.page, pilot.app.focused.id), ("card", "card-password"))
            self.assertTrue(pilot.app.field("card-confirm").display)
            await pilot.press(*"secret", "tab", *"secret", "enter")
            await self.settle(pilot)
        self.assertEqual(self.userd.calls[-1],
                         ("card_login", {"card": "c2", "password": "secret"}))
        self.assertEqual(self.greetd.logins, [("ncard000000", "t")])

    async def test_new_card_without_password_is_a_guest(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            pilot.app.card_polled({"card": {"id": "c3", "format": "SKC1", "known": False,
                                            "username": "ncard000000", "password": "optional"}})
            await pilot.pause()
            await pilot.click("#card-go")
            await self.settle(pilot)
        self.assertEqual(self.userd.calls[-1], ("card_login", {"card": "c3", "password": ""}))

    async def test_encrypted_card_retries_its_password(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            pilot.app.card_polled({"card": {"id": "c4", "format": "SKC2",
                                            "password": "required"}})
            await pilot.pause()
            self.assertFalse(pilot.app.field("card-confirm").display)
            await pilot.press(*"wrong", "enter")
            await self.settle(pilot)
            self.assertEqual((pilot.app.page, pilot.app.focused.id), ("card", "card-password"))
            self.assertIn("Wrong password", str(pilot.app.query_one("#status").render()))
            pilot.app.field("card-password").value = "right"
            await pilot.press("enter")
            await self.settle(pilot)
        self.assertEqual(self.userd.calls[-1], ("card_login", {"card": "c4", "password": "right"}))
        self.assertEqual(self.greetd.logins, [("ncard000000", "t")])

    async def test_swipe_is_ignored_while_waiting_for_a_signer(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("down", "enter")
            await pilot.pause()
            pilot.app.card_polled({"card": {"id": "c5", "format": "SKC2",
                                            "password": "required"}})
            await pilot.pause()
            self.assertEqual(pilot.app.page, "waiting")
            self.assertIn("Finish or cancel", str(pilot.app.query_one("#status").render()))
            self.userd.signer.set()
            await self.settle(pilot)
        self.assertNotIn("card_login", self.ops())

    async def test_unreadable_card_says_so(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            pilot.app.card_polled({"card": {"error": "card_format"}})
            await pilot.pause()
            self.assertEqual(pilot.app.page, "people")
            self.assertIn("couldn't be read", str(pilot.app.query_one("#status").render()))

    async def test_card_page_fits_a_small_terminal(self):
        async with self.app.run_test(size=(32, 12)) as pilot:
            await self.settle(pilot)
            pilot.app.card_polled({"card": {"id": "c6", "format": "SKC1", "known": False,
                                            "username": "ncard000000", "password": "optional"}})
            await pilot.pause()
            panel = pilot.app.query_one("#panel").region
            self.assertLessEqual(panel.right, 32)
            self.assertLessEqual(panel.bottom, 12)
            self.assertEqual(pilot.app.focused.id, "card-password")

    async def test_already_signed_in_account_is_switched_to(self):
        self.userd.switched = True
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.settle(pilot)
            await pilot.press("down", "down", "enter")
            await self.settle(pilot)
            self.assertEqual(pilot.app.page, "people")
            self.assertIn("Switched to fiatjaf", str(pilot.app.query_one("#status").render()))
        self.assertEqual(self.greetd.logins, [])

    async def test_panel_fits_a_small_terminal(self):
        async with self.app.run_test(size=(32, 12)) as pilot:
            await self.settle(pilot)
            panel = pilot.app.query_one("#panel").region
            self.assertLessEqual(panel.right, 32)
            self.assertLessEqual(panel.bottom, 12)


class SwitchGreeterTests(unittest.IsolatedAsyncioTestCase):
    """A greeter opened by a swipe during a session, on its own VT."""

    INSTANCE = "ab" * 8

    def setUp(self):
        self.userd = FakeUserd()
        self.greetd = RecordingGreetd()
        self.app = greeter.GreeterApp(self.userd, self.greetd, ["true"], switch=self.INSTANCE)

    async def wait_for(self, pilot, condition):
        for _ in range(100):
            if condition():
                return
            await pilot.pause(0.02)
        self.fail("timed out")

    def closed(self):
        return ("switch_done", {"instance": self.INSTANCE}) in self.userd.calls

    async def test_handles_the_swipe_that_opened_it(self):
        self.userd.swipes.put({"id": "c1", "format": "SKC2", "password": "required"})
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.wait_for(pilot, lambda: pilot.app.page == "card")
            await pilot.press(*"right", "enter")
            await self.wait_for(pilot, lambda: self.greetd.logins)
        self.assertFalse(self.closed())

    async def test_closes_itself_without_a_swipe(self):
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.wait_for(pilot, self.closed)

    async def test_escape_goes_back_to_the_locked_session(self):
        self.userd.swipes.put({"id": "c1", "format": "SKC2", "password": "required"})
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.wait_for(pilot, lambda: pilot.app.page == "card")
            await pilot.press("escape")
            self.assertEqual(pilot.app.page, "people")
            self.assertEqual(pilot.app.query_one("#panel").border_title, "SWITCH ACCOUNT")
            self.assertFalse(self.closed())
            await pilot.press("escape")
            await self.wait_for(pilot, self.closed)

    async def test_switching_to_a_signed_in_account_closes_it(self):
        self.userd.switched = True
        self.userd.swipes.put({"id": "c1", "format": "SKC2", "password": "required"})
        async with self.app.run_test(size=(70, 30)) as pilot:
            await self.wait_for(pilot, lambda: pilot.app.page == "card")
            await pilot.press(*"right", "enter")
            await self.wait_for(pilot, self.closed)
        self.assertEqual(self.greetd.logins, [])


class SignOutAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_signs_out(self):
        calls = []
        app = greeter.SignOutApp(lambda op, **fields: calls.append(op))
        async with app.run_test(size=(50, 16)) as pilot:
            await pilot.click("#signout")
            await pilot.app.workers.wait_for_complete()
        self.assertEqual(calls, ["signout"])
        self.assertEqual(app.return_value, 0)

    async def test_escape_cancels(self):
        calls = []
        app = greeter.SignOutApp(lambda op, **fields: calls.append(op))
        async with app.run_test(size=(50, 16)) as pilot:
            await pilot.press("escape")
        self.assertEqual(calls, [])
        self.assertEqual(app.return_value, 1)


if __name__ == "__main__":
    unittest.main()
