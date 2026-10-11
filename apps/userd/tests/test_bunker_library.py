"""Bunker/NIP-42 integration without held local keys or external credentials.

Both cases use a real authenticated WebSocket library relay. The second also
uses actual nak NIP-46 login/signing over a separate local signer relay.
"""
import json
import os
import threading
import shutil
import socket
import subprocess
import time
from unittest.mock import patch

from test_userd import ManagerCase, SIGNING_PUBKEY, SIGNING_USER, userd


class BunkerLibraryAuthenticationTests(ManagerCase):
    def test_bunker_library_read_write_and_auth_share_identity_and_client_home(self):
        self._roundtrip()

    def test_actual_nip46_bunker_signs_library_and_relay_authentication(self):
        executable = os.environ.get("KWAK_TEST_NAK") or shutil.which("nak")
        if not executable:
            self.skipTest("Set KWAK_TEST_NAK or put nak on PATH for local NIP-46 integration")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        signer_relay = f"ws://127.0.0.1:{port}"
        uri = f"bunker://{SIGNING_PUBKEY}?relay=ws%3A%2F%2F127.0.0.1%3A{port}&secret=test-secret"
        # Cap individual calls so a broken handshake fails promptly.
        def run(args, **kwargs):
            kwargs["timeout"] = min(kwargs.get("timeout", 10), 10)
            return subprocess.run(args, **kwargs)
        nak = userd.Nak(executable=executable, run=run)
        processes = []
        try:
            processes.append(subprocess.Popen(
                [executable, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline or processes[0].poll() is not None:
                        self.fail("Local signer relay did not start")
                    time.sleep(.02)
            client_key = "22" * 32
            client_pubkey = nak.public_key(client_key)
            processes.append(subprocess.Popen(
                [executable, "bunker", "--sec", "11" * 32, "-k", client_pubkey,
                 "-s", "test-secret", signer_relay],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            with patch.dict(os.environ, {"NOSTR_CLIENT_KEY": client_key}), patch.object(
                self.nak, "bunker_pubkey", side_effect=nak.bunker_pubkey
            ):
                self._roundtrip(actual_sign=nak.sign, uri=uri)
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

    def _roundtrip(self, actual_sign=None, uri=None):
        from websockets.sync.server import serve

        self.nak.pubkey = SIGNING_PUBKEY
        uri = uri or f"bunker://{SIGNING_PUBKEY}?relay=wss%3A%2F%2Fsigner.example"
        token = self.manager.login_bunker(uri)["token"]
        self.manager.redeem(SIGNING_USER, token)
        self.assertNotIn(SIGNING_USER, self.manager.held)
        signing_calls, auth_events, failures, stored = [], [], [], []

        def remote_sign(template, signer, home=None):
            signing_calls.append((template, signer, home))
            return (actual_sign(template, signer, home) if actual_sign else
                    userd.sign_event("11" * 32, template))

        def handler(connection):
            try:
                message = json.loads(connection.recv(timeout=10))
                challenge = f"challenge-{len(auth_events)}"
                connection.send(json.dumps(["AUTH", challenge]))
                if message[0] == "REQ":
                    connection.send(json.dumps(["CLOSED", message[1], "auth-required: sign in"]))
                else:
                    connection.send(json.dumps(["OK", message[1]["id"], False, "auth-required: sign in"]))
                frame = json.loads(connection.recv(timeout=10))
                self.assertEqual(frame[0], "AUTH")
                auth = frame[1]
                self.assertTrue(userd.valid_signed_event(auth))
                self.assertEqual(auth["pubkey"], SIGNING_PUBKEY)
                self.assertEqual(auth["kind"], 22242)
                self.assertEqual(auth["tags"], [["relay", relay], ["challenge", challenge]])
                auth_events.append(auth)
                connection.send(json.dumps(["OK", auth["id"], True, ""]))
                retried = json.loads(connection.recv(timeout=10))
                self.assertEqual(retried, message)
                if message[0] == "REQ":
                    if stored:
                        connection.send(json.dumps(["EVENT", message[1], stored[0]]))
                    connection.send(json.dumps(["EOSE", message[1]]))
                else:
                    self.assertTrue(userd.valid_signed_event(message[1]))
                    self.assertEqual(message[1]["pubkey"], auth["pubkey"])
                    stored[:] = [message[1]]
                    connection.send(json.dumps(["OK", message[1]["id"], True, ""]))
            except Exception as error:
                failures.append(error)

        with serve(handler, "127.0.0.1", 0, compression=None) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            relay = f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
            self.manager.config["relays"] = [relay]
            address = "35129:" + "a" * 64 + ":remote-game"
            try:
                with patch.object(self.nak, "sign", side_effect=remote_sign), patch.dict(
                    os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
                ):
                    self.assertEqual(self.manager.napplet_library(SIGNING_USER)["addresses"], [])
                    added = self.manager.update_napplet_library(SIGNING_USER, address, "add")
                    self.assertEqual(added["addresses"], [address])
                    # Force a fresh relay read rather than relying on cached state.
                    self.manager.library_events.clear()
                    self.assertEqual(self.manager.napplet_library(SIGNING_USER)["addresses"], [address])
            finally:
                server.shutdown()
                thread.join(timeout=3)
        self.assertEqual(failures, [])
        self.assertEqual(len(auth_events), 4)
        self.assertEqual([call[0]["kind"] for call in signing_calls], [22242, 22242, 30078, 22242, 22242])
        self.assertTrue(all(call[1] == uri for call in signing_calls))
        self.assertTrue(all(call[2] == self.manager.key_dir(SIGNING_USER) / "nak" for call in signing_calls))
        self.assertEqual(stored[0]["tags"], [["d", "kwak-napplets"]])
