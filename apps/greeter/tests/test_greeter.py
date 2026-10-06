"""greetd IPC tests run against a fake greetd socket, without GTK."""

import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
