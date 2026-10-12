import json
import os
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from test_userd import ManagerCase, userd, SIGNING_PUBKEY, SIGNING_USER

ADDRESS = "35129:" + "a" * 64 + ":game"
OTHER = "35129:" + "b" * 64 + ":other"


def event(addresses, created_at=None):
    return userd.sign_event("11" * 32, {
        "kind": 1, "created_at": created_at or int(time.time()),
        "tags": [],
        "content": json.dumps({"version": 1, "napplets": addresses})})


class LibraryManagerTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.nak.pubkey = SIGNING_PUBKEY
        token = self.manager.login_nsec("11" * 32)["token"]
        self.manager.redeem(SIGNING_USER, token)

    def test_signature_and_address_validation(self):
        signed = event([ADDRESS])
        self.assertTrue(userd.valid_signed_event(signed))
        self.assertFalse(userd.valid_signed_event({**signed, "content": "tampered"}))
        for value in (["nsec1secret"], [ADDRESS + "\n"], ["1:" + "a" * 64 + ":x"], "wrong"):
            with self.assertRaises(userd.UserError):
                userd.napplet_addresses(value)

    def test_idle_only_pauses_the_callers_visible_session(self):
        self.sessions.add("c2", SIGNING_USER)
        self.sessions.active_id = "c2"
        result = userd.Server(self.manager).dispatch(30000, {"op": "idle_session"})
        self.assertEqual(result, {"opened": True})
        freezes = [call for call in self.system.commands if "freeze" in call]
        self.assertEqual(freezes, [["systemctl", "--user", f"--machine={SIGNING_USER}@.host", "freeze", "kwakore.service"]])
        self.manager._switch_to(self.sessions.get("c2"))
        self.assertIn(["systemctl", "--user", f"--machine={SIGNING_USER}@.host", "thaw", "kwakore.service"], self.system.commands)
        self.sessions.active_id = "c1"
        self.assertEqual(self.manager.idle_session(SIGNING_USER), {"opened": False})

    def test_local_accounts_and_foreign_requests_cannot_pause_games(self):
        before = list(self.system.commands)
        self.manager._game_resources("root", "freeze")
        self.assertEqual(self.system.commands, before)
        with self.assertRaisesRegex(userd.UserError, "Permission denied"):
            userd.Server(self.manager).dispatch(990, {"op": "idle_session"})

    def test_inactive_runtime_is_not_frozen_or_started(self):
        with patch.object(self.manager, "run", return_value=subprocess.CompletedProcess([], 3)) as run:
            self.manager._game_resources(SIGNING_USER, "freeze")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-3:], ["is-active", "--quiet", "kwakore.service"])

    def test_freezer_failures_do_not_strand_account_switching(self):
        self.sessions.add("c2", SIGNING_USER)
        self.sessions.active_id = "c2"
        for error in (subprocess.CompletedProcess([], 1), OSError("bus unavailable"),
                      subprocess.TimeoutExpired("systemctl", 5)):
            with self.subTest(error=type(error).__name__):
                self.manager.switch_started = None
                with (
                    patch.object(self.manager, "run", side_effect=[
                        subprocess.CompletedProcess([], 0), error]),
                    patch.object(self.manager, "_command"),
                    patch.object(userd, "log") as log,
                ):
                    self.assertEqual(self.manager.idle_session(SIGNING_USER), {"opened": True})
                self.assertTrue(log.called)

    def test_thaw_failure_still_restores_visible_session(self):
        self.sessions.add("c2", SIGNING_USER)
        with patch.object(self.manager, "run", side_effect=[
            subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)]):
            self.manager._switch_to(self.sessions.get("c2"))
        self.assertEqual(self.sessions.log[-2:], [("activate", "c2"), ("unlock", "c2")])

    def test_swiping_current_card_thaws_without_freezing_again(self):
        self.sessions.add("c2", SIGNING_USER)
        self.sessions.active_id = "c2"
        self.system.commands.clear()
        self.manager.card_swipe({"format": "SKC1", "secret_key": "11" * 32})
        actions = [command[-2] for command in self.system.commands
                   if command[-1] == "kwakore.service" and command[-2] in ("freeze", "thaw")]
        self.assertEqual(actions, ["thaw"])
        self.assertEqual(self.sessions.active_id, "c2")


class WireTests(unittest.TestCase):
    def connection(self, event=None):
        class Connection:
            def __init__(self):
                self.frames = []
                self.sent = []
                self.closed = False
            def send(self, raw):
                frame = json.loads(raw)
                self.sent.append(frame)
                if frame[0] == "REQ":
                    self.frames.append(["AUTH", "challenge"] if len(self.sent) == 1 else ["EOSE", frame[1]])
                elif frame[0] == "AUTH":
                    self.frames.append(["OK", frame[1]["id"], True, ""])
                elif frame[0] == "EVENT":
                    self.frames.append(["OK", frame[1]["id"], False, "rejected"])
            def recv(self):
                return json.dumps(self.frames.pop(0))
            def settimeout(self, timeout):
                pass
            def close(self):
                self.closed = True
        return Connection()





    def test_rejected_publication_is_not_reported_as_synced(self):
        connection = self.connection()
        with patch("websocket.create_connection", return_value=connection):
            with self.assertRaisesRegex(userd.UserError, "rejected"):
                userd.EventPublisher().exchange("wss://relay.example", SIGNING_PUBKEY, None, event([]))
        self.assertTrue(connection.closed)
