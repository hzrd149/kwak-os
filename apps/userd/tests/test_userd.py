"""User manager tests run without root, nak, or the network."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "kwak_userd", Path(__file__).resolve().parents[1] / "userd.py"
)
userd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(userd)

PUBKEY = "3bf0c63fcb93463407af97a5e5ee64fa883d107ef9e558472c4eb9aaaefa459d"
OTHER = "3bf0c63fcbffffffffffffffffffffffffffffffffffffffffffffffffffffff"


class Account:
    def __init__(self, name, uid, home):
        self.pw_name, self.pw_uid, self.pw_gid, self.pw_dir = name, uid, os.getgid(), home


class System:
    """Fake passwd database and command runner that records every command."""

    def __init__(self, root):
        self.root = Path(root)
        self.accounts = {"greeter": Account("greeter", 990, "/var/empty")}
        self.commands = []
        self.processes = set()

    def getpwnam(self, name):
        return self.accounts[name]

    def getpwuid(self, uid):
        for account in self.accounts.values():
            if account.pw_uid == uid:
                return account
        raise KeyError(uid)

    def __call__(self, args, **kwargs):
        self.commands.append(args)
        code = 0
        if args[0] == "useradd":
            home = self.root / "home" / args[-1]
            home.mkdir(parents=True)
            uid = int(args[args.index("--uid") + 1])
            self.accounts[args[-1]] = Account(args[-1], uid, str(home))
        elif args[0] == "userdel":
            self.accounts.pop(args[-1])
        elif args[0] == "pkill":
            self.processes.discard(int(args[-1]))
        elif args[0] == "pgrep":
            code = 0 if int(args[-1]) in self.processes else 1
        return subprocess.CompletedProcess(args, code, "", "")


class FakeNak:
    def __init__(self, pubkey=PUBKEY):
        self.pubkey = pubkey
        self.events = []

    def generate(self):
        return "11" * 32

    def public_key(self, secret):
        return self.pubkey

    def bunker_pubkey(self, uri, home):
        # The environment nak would get, to check which client key it uses.
        self.bunker_env = userd.Nak.signer_env(uri, home)
        return self.pubkey

    def fetch(self, pubkey, relays, kinds=(0, 10002)):
        return self.events

    def publish(self, kind, content, tags, relays, signer, home=None):
        self.published = (kind, content, tags, relays, signer, home)
        return {"kind": kind, "pubkey": self.pubkey, "id": "ab" * 32}

    def sign(self, template, signer, home=None):
        self.signed = (template, signer, home)
        return {**template, "pubkey": self.pubkey, "id": "ab" * 32, "sig": "cd" * 64}


class FakeSessions:
    """Sessions on seat0; starts with the sign-in screen's greeter on screen."""

    def __init__(self):
        self.sessions = []
        self.pids = {}
        self.log = []
        self.active_id = None
        self.add("c1", "greeter", "greeter", pid=100)
        self.active_id = "c1"

    def add(self, session_id, user, kind="user", pid=None):
        self.sessions.append({"id": session_id, "user": user, "class": kind,
                              "state": "online", "locked": False, "seat": "seat0", "vt": 0})
        if pid is not None:
            self.pids[pid] = session_id

    def get(self, session_id):
        return next((s for s in self.sessions if s["id"] == session_id), None)

    def all(self):
        return list(self.sessions)

    def active(self):
        return self.get(self.active_id)

    def of_user(self, username):
        return next((s for s in self.sessions if s["user"] == username and s["class"] == "user"),
                    None)

    def of_pid(self, pid):
        return self.get(self.pids.get(pid))

    def activate(self, session_id):
        self.log.append(("activate", session_id))
        self.active_id = session_id

    def lock(self, session_id):
        self.log.append(("lock", session_id))

    def unlock(self, session_id):
        self.log.append(("unlock", session_id))

    def chvt(self, vt):
        self.log.append(("chvt", vt))
        self.active_id = None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Bech32Tests(unittest.TestCase):
    def test_npub_round_trip(self):
        encoded = userd.npub(PUBKEY)
        self.assertTrue(encoded.startswith("npub1"))
        self.assertEqual(userd.bech32_decode(encoded), ("npub", bytes.fromhex(PUBKEY)))

    def test_nsec_and_hex_secret(self):
        secret = "ab" * 32
        nsec = userd.bech32_encode("nsec", bytes.fromhex(secret))
        self.assertEqual(userd.secret_hex(nsec), secret)
        self.assertEqual(userd.secret_hex(secret.upper()), secret)
        with self.assertRaisesRegex(userd.UserError, "checksum"):
            userd.secret_hex(nsec[:-1] + ("q" if nsec[-1] != "q" else "p"))
        with self.assertRaisesRegex(userd.UserError, "nsec"):
            userd.secret_hex(userd.npub(PUBKEY))

    def test_username(self):
        self.assertEqual(userd.username_for(PUBKEY), "n3bf0c63fcb")
        self.assertRegex(userd.username_for("0" * 64), userd.USERNAME)


class Nip49Tests(unittest.TestCase):
    def test_spec_vector(self):
        ncryptsec = (
            "ncryptsec1qgg9947rlpvqu76pj5ecreduf9jxhselq2nae2kghhvd5g7dgjtcxfqtd67p9m0w57l"
            "spw8gsq6yphnm8623nsl8xn9j4jdzz84zm3frztj3z7s35vpzmqf6ksu8r89qk5z2zxfmu5gv8th8w"
            "clt0h4p"
        )
        self.assertEqual(
            userd.ncryptsec_decrypt(ncryptsec, "nostr"),
            "3501454135014541350145413501453fefb02227e449e57cf4d3a3ce05378683",
        )
        with self.assertRaisesRegex(userd.UserError, "Wrong password"):
            userd.ncryptsec_decrypt(ncryptsec, "nope")

    def test_round_trip(self):
        secret = "cd" * 32
        ncryptsec = userd.ncryptsec_encrypt(secret, "pässwörd", log_n=10)
        self.assertEqual(userd.ncryptsec_decrypt(ncryptsec, "pässwörd"), secret)


class ManagerCase(unittest.TestCase):
    """A manager over a fake system in a temporary state directory."""

    def setUp(self):
        quiet = patch.object(userd, "log")
        self.log = quiet.start()
        self.addCleanup(quiet.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.system = System(root)
        # Fake accounts have UIDs the unprivileged test runner cannot chown to.
        chown = patch.object(userd.os, "chown")
        chown.start()
        self.addCleanup(chown.stop)
        self.clock = Clock()
        self.nak = FakeNak()
        self.config = dict(
            userd.DEFAULTS, state_dir=str(root / "state"), hooks=str(root / "hooks"),
            relays=["ws://relay"],
        )
        self.sessions = FakeSessions()
        self.manager = userd.UserManager(
            self.config, run=self.system, nak=self.nak, clock=self.clock, users=self.system,
            executable="/bin/kwak-userd", sessions=self.sessions,
        )

    def commands(self, name):
        return [c for c in self.system.commands if c[0] == name]

    def stored_key(self):
        path = self.manager.key_dir(USER) / "key.json"
        return json.loads(path.read_text()) if path.exists() else None


USER = "n3bf0c63fcb"


class AccountSettingsTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.manager.login_nsec("11" * 32, "hunter2")
        self.nak.events = [
            {"kind": 0, "pubkey": PUBKEY, "created_at": 1,
             "content": json.dumps({"name": "old", "custom": "preserved"})},
            {"kind": 10002, "pubkey": PUBKEY, "created_at": 1,
             "tags": [["r", "wss://out.example", "write"], ["r", "wss://in.example", "read"]]},
            {"kind": 10063, "pubkey": PUBKEY, "created_at": 1,
             "tags": [["server", "https://media.example"]]},
        ]

    def test_load_and_publish_profile_to_outbox(self):
        result = self.manager.account_settings(USER)
        self.assertEqual(result["media_servers"], ["https://media.example"])
        self.manager.publish_settings(USER, "profile", {"name": "new"}, "hunter2")
        kind, content, tags, relays, signer, home = self.nak.published
        self.assertEqual(kind, 0)
        self.assertEqual(json.loads(content), {"name": "new", "custom": "preserved"})
        self.assertIn("wss://out.example", relays)
        self.assertNotIn("wss://in.example", relays)
        self.assertEqual(signer, "11" * 32)

    def test_relay_edit_reaches_new_write_relay(self):
        self.manager.publish_settings(USER, "relays", [["wss://new.example", "write"]], "hunter2")
        self.assertIn("wss://new.example", self.nak.published[3])
        self.assertEqual(self.nak.published[2], [["r", "wss://new.example", "write"]])

    def test_invalid_edit_never_signs(self):
        with self.assertRaises(userd.UserError):
            self.manager.publish_settings(USER, "media_servers", ["http://unsafe.example"], "hunter2")
        self.assertFalse(hasattr(self.nak, "published"))

    def test_wrong_password_never_signs(self):
        with self.assertRaisesRegex(userd.UserError, "Wrong password"):
            self.manager.publish_settings(USER, "profile", {"name": "new"}, "bad")
        self.assertFalse(hasattr(self.nak, "published"))

    def test_temporary_nsec_can_publish_with_key(self):
        self.nak.pubkey = "44" * 32
        self.manager.login_nsec("22" * 32)
        name = userd.username_for("44" * 32)
        # The fake signer derives the same pubkey for every test key.
        self.manager.publish_settings(name, "media_servers", ["https://media.example"], "11" * 32)
        self.assertEqual(self.nak.published[4], "11" * 32)


class ProvisioningTests(ManagerCase):
    def test_refresh_updates_saved_name_and_login_grant(self):
        self.manager.login_nsec("11" * 32)
        self.nak.events = [{"kind": 0, "pubkey": PUBKEY, "created_at": 2,
                            "content": json.dumps({"display_name": "Alice"})}]
        self.assertEqual(self.manager.refresh_profiles(), [USER])
        self.assertEqual(self.manager.known()[0]["name"], "Alice")
        self.assertEqual(self.manager.login_nsec("11" * 32)["name"], "Alice")

    def test_refresh_ignores_unrelated_events(self):
        self.manager.login_nsec("11" * 32)
        self.nak.events = [{"kind": 0, "pubkey": "ab" * 32, "created_at": 2,
                            "content": json.dumps({"name": "Impostor"})}]
        self.assertEqual(self.manager.refresh_profiles(), [])
        self.assertEqual(self.manager.known()[0]["name"], "")

    def test_first_login_provisions_user(self):
        self.nak.events = [
            {"kind": 0, "pubkey": PUBKEY, "created_at": 1,
             "content": json.dumps({"display_name": "Fiat:Jaf\n"})},
            {"kind": 10002, "pubkey": PUBKEY, "created_at": 1,
             "tags": [["r", "wss://relay.example"], ["r", "http://bad"]]},
        ]
        result = self.manager.login_nsec("11" * 32, "hunter2")
        self.assertEqual(result["username"], USER)
        useradd = self.system.commands[0]
        self.assertEqual(useradd[0], "useradd")
        self.assertEqual(useradd[useradd.index("--uid") + 1], "30000")
        self.assertEqual(useradd[useradd.index("--comment") + 1], "FiatJaf")
        home = Path(self.system.accounts[USER].pw_dir)
        identity = json.loads((home / ".config/kwak/identity.json").read_text())
        self.assertEqual(identity["pubkey"], PUBKEY)
        self.assertEqual(
            json.loads((home / ".config/kwak/relays.json").read_text()), ["wss://relay.example"]
        )

    def test_second_login_reuses_user(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.manager.login_nsec("11" * 32, "hunter2")
        self.assertEqual(len(self.commands("useradd")), 1)

    def test_prefix_collision_is_refused(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.nak.pubkey = OTHER
        with self.assertRaisesRegex(userd.UserError, "different key"):
            self.manager.login_nsec("22" * 32, "hunter2")

    def test_existing_system_account_is_refused(self):
        self.system.accounts[USER] = Account(USER, 1000, "/home/x")
        with self.assertRaisesRegex(userd.UserError, "already exists"):
            self.manager.login_nsec("11" * 32, "hunter2")

    def test_failed_setup_removes_the_new_account(self):
        hooks = Path(self.config["hooks"])
        hooks.mkdir()
        (hooks / "10-test").write_text("#!/bin/sh\n")
        (hooks / "10-test").chmod(0o755)
        with patch.object(self.manager, "_run_hooks", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.manager.login_nsec("11" * 32, "hunter2")
        self.assertEqual(len(self.commands("userdel")), 1)
        self.assertNotIn(USER, self.system.accounts)
        self.assertIsNone(self.manager.entry(USER))

    def test_hooks_run_with_identity(self):
        hooks = Path(self.config["hooks"])
        hooks.mkdir()
        hook = hooks / "10-test"
        hook.write_text("#!/bin/sh\n")
        hook.chmod(0o755)
        self.manager.login_nsec("11" * 32, "hunter2")
        self.assertIn([str(hook)], self.system.commands)


class NsecTests(ManagerCase):
    def test_password_saves_identity_as_ncryptsec(self):
        result = self.manager.login_nsec("11" * 32, "hunter2")
        self.assertFalse(result["temporary"])
        entry = self.manager.entry(USER)
        self.assertEqual((entry["method"], entry["temporary"]), ("ncryptsec", False))
        stored = self.stored_key()
        self.assertNotIn("11" * 32, json.dumps(stored))
        self.assertEqual(userd.ncryptsec_decrypt(stored["ncryptsec"], "hunter2"), "11" * 32)
        self.assertEqual([p["username"] for p in self.manager.known()], [USER])

    def test_no_password_is_temporary_and_saves_nothing(self):
        result = self.manager.login_nsec("11" * 32)
        self.assertTrue(result["temporary"])
        self.assertTrue(self.manager.entry(USER)["temporary"])
        self.assertIsNone(self.stored_key())
        listed = self.manager.known()
        self.assertEqual([(p["username"], p["temporary"]) for p in listed], [(USER, True)])
        # Still on the computer, so it opens again without a password.
        self.assertTrue(self.manager.unlock(USER)["temporary"])
        with self.assertRaisesRegex(userd.UserError, "Unknown identity"):
            self.manager.unlock("n0000000000")

    def test_short_password_is_refused_before_provisioning(self):
        with self.assertRaisesRegex(userd.UserError, "at least 4"):
            self.manager.login_nsec("11" * 32, "abc")
        self.assertEqual(self.commands("useradd"), [])

    def test_adding_a_password_keeps_a_temporary_identity(self):
        self.manager.login_nsec("11" * 32)
        result = self.manager.login_nsec("11" * 32, "hunter2")
        self.assertFalse(result["temporary"])
        self.assertFalse(self.manager.entry(USER)["temporary"])
        self.assertIsNotNone(self.stored_key())
        self.assertEqual(len(self.commands("useradd")), 1)

    def test_saved_identity_stays_saved_without_password(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        before = self.stored_key()
        result = self.manager.login_nsec("11" * 32)
        self.assertFalse(result["temporary"])
        self.assertEqual(self.stored_key(), before)

    def test_new_password_replaces_the_old_one(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.manager.login_nsec("11" * 32, "correct horse")
        with self.assertRaisesRegex(userd.UserError, "Wrong password"):
            self.manager.unlock(USER, "hunter2")
        self.assertIn("token", self.manager.unlock(USER, "correct horse"))


class NcryptsecTests(ManagerCase):
    def test_ncryptsec_is_saved_as_given(self):
        ncryptsec = userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10)
        result = self.manager.login_ncryptsec(ncryptsec, "hunter2")
        self.assertFalse(result["temporary"])
        self.assertEqual(self.stored_key(), {"ncryptsec": ncryptsec})
        self.assertEqual(self.manager.entry(USER)["method"], "ncryptsec")
        with self.assertRaisesRegex(userd.UserError, "Wrong password"):
            self.manager.unlock(USER, "wrong")
        self.assertIn("token", self.manager.unlock(USER, "hunter2"))

    def test_wrong_password_creates_nothing(self):
        ncryptsec = userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10)
        with self.assertRaisesRegex(userd.UserError, "Wrong password"):
            self.manager.login_ncryptsec(ncryptsec, "nope")
        self.assertEqual(self.commands("useradd"), [])

    def test_excessive_scrypt_cost_is_refused(self):
        data = bytearray(userd.bech32_decode(
            userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10))[1])
        data[1] = 21
        with self.assertRaisesRegex(userd.UserError, "too much memory"):
            self.manager.login_ncryptsec(userd.bech32_encode("ncryptsec", bytes(data)), "x")


class SignerEnvTests(unittest.TestCase):
    def test_client_key_comes_from_nak_home(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(userd.Nak.signer_env("bunker://x", home),
                             {"NOSTR_SECRET_KEY": "bunker://x", "HOME": home})
            Path(home, "client-key").write_text("22" * 32 + "\n")
            self.assertEqual(userd.Nak.signer_env("bunker://x", home)["NOSTR_CLIENT_KEY"],
                             "22" * 32)
        self.assertEqual(userd.Nak.signer_env("ab" * 32), {"NOSTR_SECRET_KEY": "ab" * 32})


class BunkerTests(ManagerCase):
    URI = "bunker://abc?relay=wss%3A%2F%2Fr&secret=x"

    def test_confirmed_bunker_is_saved(self):
        result = self.manager.login_bunker(self.URI)
        self.assertEqual(result["username"], USER)
        self.assertFalse(result["temporary"])
        self.assertEqual(self.stored_key(), {"bunker": self.URI})
        self.assertEqual(self.manager.entry(USER)["method"], "bunker")
        self.assertTrue((self.manager.key_dir(USER) / "nak").is_dir())
        self.assertEqual(list((Path(self.config["state_dir"]) / "bunker-clients").iterdir()), [])
        self.assertIn("token", self.manager.unlock(USER))

    def test_unconfirmed_bunker_creates_nothing(self):
        with patch.object(self.nak, "bunker_pubkey", side_effect=userd.UserError("timeout")):
            with self.assertRaises(userd.UserError):
                self.manager.login_bunker(self.URI)
        self.assertEqual(self.commands("useradd"), [])

    def test_unlock_refuses_a_different_signer(self):
        self.manager.login_bunker(self.URI)
        self.nak.pubkey = OTHER.replace("3bf0c63fcb", "aaaaaaaaaa")
        with self.assertRaisesRegex(userd.UserError, "different identity"):
            self.manager.unlock(USER)


class CreateTests(ManagerCase):
    def test_create_with_password_is_saved(self):
        result = self.manager.create_identity("hunter2")
        self.assertFalse(result["temporary"])
        self.assertTrue(result["nsec"].startswith("nsec1"))
        self.assertEqual(userd.ncryptsec_decrypt(result["ncryptsec"], "hunter2"), "11" * 32)

    def test_create_without_password_is_a_guest(self):
        result = self.manager.create_identity()
        self.assertTrue(result["temporary"])
        self.assertIsNone(result["ncryptsec"])
        self.assertIsNone(self.stored_key())

    def test_create_refuses_an_existing_key(self):
        self.manager.create_identity()
        with self.assertRaisesRegex(userd.UserError, "already exists"):
            self.manager.create_identity()


class SessionTests(ManagerCase):
    def test_token_is_single_use_bound_and_expires(self):
        token = self.manager.login_nsec("11" * 32, "hunter2")["token"]
        with self.assertRaises(userd.UserError):
            self.manager.redeem("greeter", token)
        token = self.manager.unlock(USER, "hunter2")["token"]
        self.manager.redeem(USER, token)
        with self.assertRaises(userd.UserError):
            self.manager.redeem(USER, token)
        token = self.manager.unlock(USER, "hunter2")["token"]
        self.clock.now += 61
        with self.assertRaises(userd.UserError):
            self.manager.redeem(USER, token)

    def test_logout_removes_temporary_identity(self):
        self.manager.login_nsec("11" * 32)
        self.assertEqual(self.manager.close_session(USER), {"scheduled": True})
        self.assertEqual(self.commands("systemd-run")[-1][-3:], ["/bin/kwak-userd", "remove", USER])

    def test_logout_keeps_saved_identity(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.assertEqual(self.manager.close_session(USER), {"scheduled": False})
        self.assertEqual(self.manager.close_session("kwak"), {"scheduled": False})
        self.assertEqual(self.commands("systemd-run"), [])


class RemovalTests(ManagerCase):
    def test_signout_survives_interrupted_removal(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.manager.request_signout(USER)
        self.assertTrue(self.manager.entry(USER)["pending_removal"])
        self.assertEqual(self.manager.known(), [])
        with self.assertRaises(userd.UserError):
            self.manager.unlock(USER, "hunter2")
        with self.assertRaises(userd.UserError):
            self.manager.login_nsec("11" * 32, "hunter2")
        # A new daemon instance at boot must finish the deletion.
        restarted = userd.UserManager(
            self.config, run=self.system, nak=self.nak, clock=self.clock,
            users=self.system, executable="/bin/kwak-userd", sessions=self.sessions,
        )
        self.assertEqual(restarted.cleanup_temporary(wait=lambda _: None), [USER])
        self.assertIsNone(restarted.entry(USER))
        self.assertNotIn(USER, self.system.accounts)

    def test_remove_cleans_up_in_order(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        home = Path(self.system.accounts[USER].pw_dir)
        self.system.processes.add(30000)
        self.system.commands.clear()
        self.manager.remove(USER, wait=lambda _: None)
        self.assertEqual(
            [c[0] for c in self.system.commands],
            ["loginctl", "systemctl", "pkill", "pgrep", "userdel", "groupdel", "find"],
        )
        self.assertNotIn(USER, self.system.accounts)
        self.assertIsNone(self.manager.entry(USER))
        self.assertFalse(self.manager.key_dir(USER).exists())
        self.assertTrue(home.exists(), "the fake userdel leaves the home alone")

    def test_remove_refuses_unmanaged_accounts(self):
        self.system.accounts["kwak"] = Account("kwak", 1000, "/home/kwak")
        for name in ("kwak", "greeter", "n0000000000", "../etc"):
            with self.assertRaises(userd.UserError):
                self.manager.remove(name)
        self.manager.login_nsec("11" * 32, "hunter2")
        self.system.accounts[USER].pw_uid = 1000
        with self.assertRaises(userd.UserError):
            self.manager.remove(USER)
        self.assertEqual(self.commands("userdel"), [])

    def test_cleanup_removes_only_temporary_identities(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.nak.pubkey = "ab" * 32
        self.manager.login_nsec("22" * 32)
        removed = self.manager.cleanup_temporary(wait=lambda _: None)
        self.assertEqual(removed, ["nabababab" + "ab"])
        self.assertIn(USER, self.system.accounts)
        self.assertEqual([p["username"] for p in self.manager.known()], [USER])


class CardTests(ManagerCase):
    def setUp(self):
        super().setUp()
        hidraw = Path(self.directory.name) / "hidraw"
        hidraw.mkdir()
        self.config["hidraw"] = str(hidraw)

    def plug_in_reader(self):
        usb = Path(self.directory.name) / "usb" / "1-1"
        (usb / "1-1:1.0" / "0003:C216:0180.0001").mkdir(parents=True)
        (usb / "idVendor").write_text("c216\n")
        (usb / "idProduct").write_text("0180\n")
        node = Path(self.config["hidraw"]) / "hidraw0"
        node.mkdir()
        (node / "device").symlink_to(usb / "1-1:1.0" / "0003:C216:0180.0001")

    def swipe(self, **event):
        """Swipe at the sign-in screen; its greeter (pid 100) picks it up."""
        self.assertEqual(self.manager.card_swipe(event), {"delivered": True})
        return self.manager.wait_card(after=0, wait=0, pid=100)["card"]

    def test_swipe_without_a_session_on_screen_is_dropped(self):
        self.sessions.active_id = None
        self.assertEqual(self.manager.card_swipe({"format": "SKC1", "secret_key": "11" * 32}),
                         {"delivered": False})
        self.assertIsNone(self.manager.wait_card(after=0, wait=0)["card"])

    def test_only_the_greeter_on_screen_gets_the_swipe(self):
        self.sessions.add("c2", "greeter", "greeter", pid=200)
        self.manager.card_swipe({"format": "SKC1", "secret_key": "11" * 32})
        self.assertIsNone(self.manager.wait_card(after=0, wait=0, pid=200)["card"])
        self.assertIsNotNone(self.manager.wait_card(after=0, wait=0, pid=100)["card"])

    def test_used_swipe_is_not_handed_out_again(self):
        card = self.swipe(format="SKC1", secret_key="11" * 32)
        self.manager.card_login(card["id"])
        self.assertIsNone(self.manager.wait_card(after=0, wait=0, pid=100)["card"])

    def test_reader_detection(self):
        self.assertFalse(self.manager.wait_card(wait=0)["reader"])
        self.plug_in_reader()
        self.assertTrue(self.manager.wait_card(wait=0)["reader"])

    def test_summary_holds_no_secret(self):
        card = self.swipe(format="SKC1", secret_key="11" * 32)
        self.assertEqual(
            {k: card[k] for k in ("format", "username", "known", "password")},
            {"format": "SKC1", "username": USER, "known": False, "password": "optional"})
        self.assertNotIn("11" * 32, json.dumps(card))
        ncryptsec = userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10)
        card = self.swipe(format="SKC2", ncryptsec=ncryptsec)
        self.assertEqual(card["password"], "required")
        self.assertNotIn(ncryptsec, json.dumps(card))

    def test_unknown_skc1_is_a_guest_without_password(self):
        card = self.swipe(format="SKC1", secret_key="11" * 32)
        result = self.manager.card_login(card["id"])
        self.assertTrue(result["temporary"])
        self.assertIsNone(self.stored_key())

    def test_unknown_skc1_with_password_is_saved(self):
        card = self.swipe(format="SKC1", secret_key="11" * 32)
        self.assertFalse(self.manager.card_login(card["id"], "hunter2")["temporary"])
        self.assertIn("ncryptsec", self.stored_key())

    def test_known_skc1_signs_in_and_keeps_stored_key(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        stored = self.stored_key()
        card = self.swipe(format="SKC1", secret_key="11" * 32)
        self.assertEqual((card["known"], card["password"]), (True, "none"))
        self.assertIn("token", self.manager.card_login(card["id"]))
        self.assertEqual(self.stored_key(), stored)

    def test_skc2_wrong_password_keeps_the_swipe(self):
        ncryptsec = userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10)
        card = self.swipe(format="SKC2", ncryptsec=ncryptsec)
        with self.assertRaisesRegex(userd.UserError, "Wrong password"):
            self.manager.card_login(card["id"], "nope")
        self.assertFalse(self.manager.card_login(card["id"], "hunter2")["temporary"])
        self.assertEqual(self.stored_key(), {"ncryptsec": ncryptsec})
        with self.assertRaisesRegex(userd.UserError, "expired"):
            self.manager.card_login(card["id"], "hunter2")

    def test_swipe_expires(self):
        card = self.swipe(format="SKC1", secret_key="11" * 32)
        self.clock.now += self.config["card_ttl"]
        with self.assertRaisesRegex(userd.UserError, "expired"):
            self.manager.card_login(card["id"])
        self.assertEqual(self.commands("useradd"), [])

    def test_new_swipe_replaces_the_old_one(self):
        first = self.swipe(format="SKC1", secret_key="11" * 32)
        self.swipe(format="SKC1", secret_key="11" * 32)
        with self.assertRaisesRegex(userd.UserError, "expired"):
            self.manager.card_login(first["id"])

    def test_skc3_card_signs_in_through_its_bunker(self):
        uri = "bunker://" + "ab" * 32 + "?relay=wss%3A%2F%2Fr"
        card = self.swipe(format="SKC3", bunker=uri, client_key="22" * 32)
        self.assertEqual((card["format"], card["password"], card["signer"]),
                         ("SKC3", "none", True))
        self.assertNotIn("22" * 32, json.dumps(card))
        result = self.manager.card_login(card["id"])
        self.assertEqual((result["username"], result["temporary"]), (USER, False))
        self.assertEqual(self.nak.bunker_env["NOSTR_CLIENT_KEY"], "22" * 32)
        # The paired client key stays with nak's state for later sign-ins.
        self.assertEqual(self.stored_key(), {"bunker": uri})
        self.assertEqual((self.manager.key_dir(USER) / "nak" / "client-key").read_text(),
                         "22" * 32 + "\n")
        self.nak.bunker_env = None
        self.manager.unlock(USER)
        self.assertEqual(self.nak.bunker_env["NOSTR_CLIENT_KEY"], "22" * 32)

    def test_skc3_card_with_unanswered_signer_creates_nothing(self):
        card = self.swipe(format="SKC3", bunker="bunker://" + "ab" * 32, client_key="22" * 32)
        with patch.object(self.nak, "bunker_pubkey", side_effect=userd.UserError("timeout")):
            with self.assertRaises(userd.UserError):
                self.manager.card_login(card["id"])
        self.assertEqual(self.commands("useradd"), [])
        self.assertEqual(list((Path(self.config["state_dir"]) / "bunker-clients").iterdir()), [])

    def test_unreadable_card(self):
        self.assertEqual(self.swipe(error="card_format")["error"], "card_format")
        self.assertEqual(self.swipe(format="SKC2", ncryptsec="nope")["error"], "card_format")
        self.assertEqual(self.swipe(format="SKC3", bunker="https://x", client_key="22" * 32)
                         ["error"], "card_format")
        self.assertEqual(self.swipe(format="SKC9")["error"], "card_format")


class SessionsTests(unittest.TestCase):
    """Parsing loginctl output and cgroups."""

    OUTPUT = {
        ("list-sessions", "--no-legend"): " 3 1000 kwak seat0 1234 user tty2 no -\n"
                                          "c1  990 greeter seat0 99 greeter tty1 no -\n"
                                          " 7    0 root - 55 manager - no -\n",
        ("show-seat", "seat0", "--property=ActiveSession", "--value"): "3\n",
    }
    SHOW = {
        "3": "Id=3\nName=kwak\nClass=user\nState=active\nLockedHint=no\nVTNr=2\nSeat=seat0\n",
        "c1": "Id=c1\nName=greeter\nClass=greeter\nState=online\nLockedHint=no\nVTNr=1\n"
              "Seat=seat0\n",
        "7": "Id=7\nName=root\nClass=manager\nState=active\nVTNr=0\nSeat=\n",
    }

    def loginctl(self, args, **kwargs):
        self.calls.append(args)
        rest = tuple(args[2:])
        if rest[:1] == ("show-session",):
            out = self.SHOW.get(rest[1], "")
        else:
            out = self.OUTPUT.get(rest, "")
        return subprocess.CompletedProcess(args, 0, out, "")

    def setUp(self):
        self.calls = []
        self.proc = tempfile.TemporaryDirectory()
        self.addCleanup(self.proc.cleanup)
        self.sessions = userd.Sessions(self.loginctl, proc=self.proc.name)

    def test_sessions_on_seat0(self):
        self.assertEqual([s["id"] for s in self.sessions.all()], ["3", "c1"])
        self.assertEqual(self.sessions.active(),
                         {"id": "3", "user": "kwak", "class": "user", "state": "active",
                          "locked": False, "seat": "seat0", "vt": 2})
        self.assertEqual(self.sessions.of_user("kwak")["id"], "3")
        self.assertIsNone(self.sessions.of_user("greeter"))

    def process(self, pid, parent, cgroup):
        Path(self.proc.name, str(pid)).mkdir()
        Path(self.proc.name, str(pid), "cgroup").write_text(f"0::{cgroup}\n")
        Path(self.proc.name, str(pid), "status").write_text(f"Name:\tx\nPPid:\t{parent}\n")

    def test_session_of_a_process(self):
        self.process(42, 1, "/user.slice/user-990.slice/session-c1.scope")
        self.assertEqual(self.sessions.of_pid(42)["id"], "c1")
        self.assertIsNone(self.sessions.of_pid(43))

    def test_session_of_a_program_kitty_moved_to_its_own_scope(self):
        # cage and kitty stay in the greeter's session; kitty's child does not.
        self.process(40, 1, "/user.slice/user-990.slice/session-c1.scope")
        self.process(41, 40, "/user.slice/user-990.slice/session-c1.scope")
        self.process(42, 41, "/user.slice/user-990.slice/user@990.service/app.slice/"
                             "kitty-41-0.scope")
        self.assertEqual(self.sessions.of_pid(42)["id"], "c1")

    def test_process_outside_any_session(self):
        self.process(50, 1, "/system.slice/kwak-userd.service")
        self.assertIsNone(self.sessions.of_pid(50))

    def test_commands(self):
        self.sessions.lock("3")
        self.sessions.activate("3")
        self.assertEqual(self.calls, [["loginctl", "--no-pager", "lock-session", "3"],
                                      ["loginctl", "--no-pager", "activate", "3"]])


class SwitchTests(ManagerCase):
    """Swipes during a session, and signing in to an account that is already open."""

    SKC2 = {"format": "SKC2", "ncryptsec": "ncryptsec1placeholder"}

    def setUp(self):
        super().setUp()
        self.sessions.add("c3", "kwak")
        self.sessions.active_id = "c3"

    def started(self):
        return [c for c in self.system.commands if c[:2] == ["systemctl", "start"]]

    def test_swipe_locks_the_session_and_opens_a_switch_greeter(self):
        self.assertEqual(self.manager.card_swipe(self.SKC2), {"delivered": True})
        self.assertIn(("lock", "c3"), self.sessions.log)
        [command] = self.started()
        self.assertRegex(command[-1], r"^kwak-greeter-switch@[0-9a-f]{16}\.service$")
        # The new greeter comes on screen and picks up the swipe that opened it.
        self.sessions.add("c9", "greeter", "greeter", pid=500)
        self.sessions.active_id = "c9"
        card = self.manager.wait_card(after=0, wait=0, pid=500)["card"]
        self.assertEqual(card["format"], "SKC2")

    def test_one_switch_greeter_at_a_time(self):
        self.manager.card_swipe(self.SKC2)
        self.manager.card_swipe(self.SKC2)
        self.assertEqual(len(self.started()), 1)
        self.clock.now += 20
        self.manager.card_swipe(self.SKC2)
        self.assertEqual(len(self.started()), 2)

    def test_swipe_decisions_are_logged_without_secrets(self):
        self.manager.card_swipe({"format": "SKC1", "secret_key": "11" * 32})
        lines = " ".join(call.args[0] for call in self.log.call_args_list)
        self.assertIn("opening a switch greeter", lines)
        self.assertNotIn("11" * 32, lines)

    def test_card_of_a_signed_in_account_switches_back(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.sessions.add("c5", USER)
        self.manager.card_swipe({"format": "SKC1", "secret_key": "11" * 32})
        self.assertEqual(self.sessions.log, [("lock", "c3"), ("activate", "c5"),
                                             ("unlock", "c5")])
        self.assertEqual(self.started(), [])

    def test_skc2_card_of_a_signed_in_account_switches_back(self):
        ncryptsec = userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10)
        self.manager.login_ncryptsec(ncryptsec, "hunter2")
        self.sessions.add("c5", USER)
        # The same card, as kwak-cards encodes it (lowercase bech32 either way).
        self.manager.card_swipe({"format": "SKC2", "ncryptsec": ncryptsec.upper()})
        self.assertEqual(self.sessions.log, [("lock", "c3"), ("activate", "c5"),
                                             ("unlock", "c5")])
        self.assertEqual(self.started(), [])

    def test_other_skc2_card_opens_the_switch_greeter(self):
        self.manager.login_ncryptsec(userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10),
                                     "hunter2")
        self.sessions.add("c5", USER)
        # Same key, but a different encryption: not the card that signed in.
        other = userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10)
        self.manager.card_swipe({"format": "SKC2", "ncryptsec": other})
        self.assertEqual(len(self.started()), 1)
        self.assertNotIn(("activate", "c5"), self.sessions.log)

    def test_skc3_card_of_a_signed_in_account_switches_back(self):
        uri = "bunker://" + "ab" * 32 + "?relay=wss%3A%2F%2Fr"
        self.manager.login_bunker(uri, client_key="22" * 32)
        self.sessions.add("c5", USER)
        self.manager.card_swipe({"format": "SKC3", "bunker": uri, "client_key": "33" * 32})
        self.assertEqual(len(self.started()), 1, "a different client key is another card")
        self.clock.now += 20
        self.manager.card_swipe({"format": "SKC3", "bunker": "bunker://" + "AB" * 32,
                                 "client_key": "22" * 32})
        self.assertEqual(self.sessions.log[-2:], [("activate", "c5"), ("unlock", "c5")])
        self.assertEqual(len(self.started()), 1)

    def test_swipe_at_a_sign_in_screen_switches_to_an_open_account(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.sessions.add("c5", USER)
        self.sessions.active_id = "c1"
        self.manager.card_swipe({"format": "SKC1", "secret_key": "11" * 32})
        self.assertEqual(self.sessions.log, [("activate", "c5"), ("unlock", "c5")])
        self.assertIsNone(self.manager.wait_card(after=0, wait=0, pid=100)["card"])

    def test_greeters_are_told_whether_they_are_on_screen(self):
        self.sessions.add("c9", "greeter", "greeter", pid=500)
        self.assertFalse(self.manager.wait_card(wait=0, pid=500)["on_screen"])
        self.sessions.active_id = "c9"
        self.assertTrue(self.manager.wait_card(wait=0, pid=500)["on_screen"])

    def test_guest_session_is_not_locked(self):
        self.manager.login_nsec("11" * 32)
        self.sessions.add("c5", USER)
        self.sessions.active_id = "c5"
        self.manager.card_swipe(self.SKC2)
        self.assertNotIn(("lock", "c5"), self.sessions.log)
        self.assertEqual(len(self.started()), 1)

    def test_unreadable_card_in_a_session_does_nothing(self):
        self.assertEqual(self.manager.card_swipe({"error": "card_format"}),
                         {"delivered": False})
        self.assertEqual((self.sessions.log, self.started()), ([], []))

    def test_signing_in_to_an_open_account_switches_to_it(self):
        self.manager.login_nsec("11" * 32, "hunter2")
        self.sessions.add("c5", USER)
        grant = self.manager.login_nsec("11" * 32)
        self.assertTrue(grant["switched"])
        self.assertNotIn("token", grant)
        self.assertEqual(self.sessions.log[-2:], [("activate", "c5"), ("unlock", "c5")])

    def switch_greeter(self):
        self.manager.card_swipe(self.SKC2)
        instance = self.started()[0][-1].split("@")[1].split(".")[0]
        self.sessions.add("c9", "greeter", "greeter", pid=500)
        self.sessions.active_id = "c9"
        return instance

    def test_closing_the_switch_greeter_returns_to_the_locked_session(self):
        instance = self.switch_greeter()
        self.assertEqual(self.manager.switch_done(instance, pid=500), {"closed": True})
        self.assertEqual(self.sessions.active_id, "c3")
        self.assertEqual(self.system.commands[-1],
                         ["systemctl", "stop", "--no-block",
                          f"kwak-greeter-switch@{instance}.service"])

    def test_closing_with_no_sessions_left_shows_the_sign_in_screen(self):
        instance = self.switch_greeter()
        self.sessions.sessions = [s for s in self.sessions.sessions if s["class"] == "greeter"
                                  and s["id"] != "c1"]
        self.manager.switch_done(instance, pid=500)
        self.assertIn(("chvt", 1), self.sessions.log)

    def test_closing_after_switching_leaves_the_screen_alone(self):
        instance = self.switch_greeter()
        self.sessions.active_id = "c3"
        self.sessions.log.clear()
        self.manager.switch_done(instance, pid=500)
        self.assertEqual(self.sessions.log, [])

    def test_switch_done_checks_the_instance(self):
        with self.assertRaises(userd.UserError):
            self.manager.switch_done("../../etc", pid=500)


class UnlockTests(ManagerCase):
    def test_ncryptsec_password_unlocks(self):
        self.manager.login_ncryptsec(userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10),
                                     "hunter2")
        self.assertEqual(self.manager.check_unlock(USER, "hunter2"), {"ok": True})
        with self.assertRaisesRegex(userd.UserError, "Wrong password"):
            self.manager.check_unlock(USER, "nope")

    def test_bunker_unlocks_when_the_signer_answers(self):
        self.manager.login_bunker("bunker://abc?relay=wss%3A%2F%2Fr")
        self.assertEqual(self.manager.check_unlock(USER), {"ok": True})
        self.nak.pubkey = OTHER
        with self.assertRaisesRegex(userd.UserError, "different identity"):
            self.manager.check_unlock(USER)

    def test_guest_unlocks_without_password(self):
        self.manager.login_nsec("11" * 32)
        self.assertEqual(self.manager.check_unlock(USER), {"ok": True})

    def test_other_users_are_refused(self):
        with self.assertRaises(userd.UserError):
            self.manager.check_unlock("kwak", "x")


class PermissionTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.server = userd.Server(self.manager)
        self.manager.login_nsec("11" * 32, "hunter2")
        self.system.accounts["kwak"] = Account("kwak", 1000, "/home/kwak")
        self.system.accounts["kwak-cards"] = Account("kwak-cards", 991, "/var/empty")

    def allowed(self, uid, op, **fields):
        try:
            self.server.dispatch(uid, {"op": op, **fields})
        except userd.UserError as error:
            return "Permission denied" not in str(error)
        return True

    def test_matrix(self):
        self.assertTrue(self.allowed(990, "list_known"))
        self.assertTrue(self.allowed(990, "login_nsec", nsec="11" * 32))
        self.assertFalse(self.allowed(990, "redeem", username=USER, token="x"))
        self.assertFalse(self.allowed(990, "remove", username=USER))
        self.assertFalse(self.allowed(990, "close_session", username=USER))
        self.assertFalse(self.allowed(1000, "list_known"))
        self.assertFalse(self.allowed(1000, "signout"))
        self.assertFalse(self.allowed(30000, "login_nsec", nsec="x", password="y"))
        self.assertFalse(self.allowed(12345, "list_known"))
        self.assertTrue(self.allowed(0, "list"))

    def test_card_matrix(self):
        self.assertTrue(self.allowed(991, "card_swipe", error="card_format"))
        self.assertFalse(self.allowed(991, "wait_card", wait=0))
        self.assertFalse(self.allowed(991, "list_known"))
        self.assertFalse(self.allowed(990, "card_swipe", error="card_format"))
        self.assertFalse(self.allowed(30000, "card_swipe", error="card_format"))
        self.assertTrue(self.allowed(990, "wait_card", wait=0))
        self.assertTrue(self.allowed(990, "card_login", card="x"))

    def test_switch_and_unlock_matrix(self):
        self.assertTrue(self.allowed(990, "switch_done", instance="0" * 16))
        self.assertFalse(self.allowed(30000, "switch_done", instance="0" * 16))
        self.assertTrue(self.allowed(30000, "check_unlock", password="hunter2"))
        self.assertFalse(self.allowed(990, "check_unlock", password="hunter2"))
        self.assertFalse(self.allowed(1000, "check_unlock", password="x"))

    def test_identity_signs_out_only_itself(self):
        self.server.dispatch(30000, {"op": "signout", "username": "kwak"})
        self.assertEqual(self.commands("systemd-run")[-1][-3:], ["/bin/kwak-userd", "remove", USER])


# The real public key of "11" * 32, for tests that sign.
SIGNING_PUBKEY = "4f355bdcb7cc0af728ef3cceb9615d90684bb5b2ca5f859ab0f0b704075871aa"
SIGNING_USER = "n4f355bdcb7"
KWAKORE = "/nix/store/kwakore/bin/.kwakore-daemon-wrapped"


class CryptoTests(unittest.TestCase):
    def test_nip44_spec_vector(self):
        payload = userd.nip44_encrypt("00" * 31 + "01", self.public("00" * 31 + "02"), "a",
                                      nonce=bytes.fromhex("00" * 31 + "01"))
        self.assertEqual(payload, "AgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABee0G5VSK0/9YypIObAtDKfYE"
                                  "AjD35uVkHyB0F4DwrcNaCXlCWZKaArsGrY6M9wnuTMxWfp1RTN9Xga8no+kF5Vsb")
        self.assertEqual(
            userd.nip44_conversation_key("00" * 31 + "01", self.public("00" * 31 + "02")).hex(),
            "c41c775356fd92eadc63ff5a0dc1da211b268cbea22316767095b2871ea1412d")

    @staticmethod
    def public(secret):
        from coincurve import PublicKeyXOnly

        return PublicKeyXOnly.from_secret(bytes.fromhex(secret)).format().hex()

    def test_round_trips_between_two_keys(self):
        alice, bob = "11" * 32, "22" * 32
        for encrypt, decrypt in ((userd.nip44_encrypt, userd.nip44_decrypt),
                                 (userd.nip04_encrypt, userd.nip04_decrypt)):
            for text in ("hi", "ü" * 40, "x" * 5000):
                sealed = encrypt(alice, self.public(bob), text)
                self.assertEqual(decrypt(bob, self.public(alice), sealed), text)
        with self.assertRaises(userd.UserError):
            userd.nip44_decrypt(bob, self.public(alice), "AgAA")
        sealed = userd.nip44_encrypt(alice, self.public(bob), "hi")
        with self.assertRaises(userd.UserError):
            userd.nip44_decrypt("33" * 32, self.public(alice), sealed)

    def test_signed_event_verifies(self):
        from coincurve import PublicKeyXOnly

        template = {"kind": 1, "created_at": 1700000000, "tags": [["t", "kwak"]],
                    "content": "héllo\n\"quoted\""}
        event = userd.sign_event("11" * 32, template)
        self.assertEqual(event["pubkey"], SIGNING_PUBKEY)
        self.assertEqual(event["id"], userd.event_id(SIGNING_PUBKEY, template))
        self.assertTrue(PublicKeyXOnly(bytes.fromhex(event["pubkey"])).verify(
            bytes.fromhex(event["sig"]), bytes.fromhex(event["id"])))

    def test_event_template_is_checked(self):
        for event in ({"kind": True, "created_at": 1, "content": ""},
                      {"kind": 70000, "created_at": 1, "content": ""},
                      {"kind": 1, "created_at": "1", "content": ""},
                      {"kind": 1, "created_at": 1, "content": 5},
                      {"kind": 1, "created_at": 1, "content": "", "tags": [["e", 1]]},
                      "event"):
            with self.assertRaises(userd.UserError):
                userd.event_template(event)
        self.assertEqual(userd.event_template({"kind": 1, "created_at": 1, "content": "",
                                               "id": "x", "sig": "y"}),
                         {"kind": 1, "created_at": 1, "tags": [], "content": ""})


class SignerTests(ManagerCase):
    """Signing for kwakore, as the signed-in user."""

    def setUp(self):
        super().setUp()
        self.nak.pubkey = SIGNING_PUBKEY
        proc = Path(self.directory.name) / "proc"
        for pid, exe in (("4242", KWAKORE), ("4243", "/run/current-system/sw/bin/python3")):
            (proc / pid).mkdir(parents=True)
            (proc / pid / "exe").symlink_to(exe)
        self.config.update(proc=str(proc), signer_clients=[KWAKORE])
        self.server = userd.Server(self.manager)

    def sign(self, op="signer.get_public_key", pid=4242, uid=30000, **fields):
        return self.server.dispatch(uid, {"op": op, **fields}, pid)

    def log_in(self):
        token = self.manager.login_ncryptsec(
            userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10), "hunter2")["token"]
        self.manager.redeem(SIGNING_USER, token)

    def test_key_is_held_only_once_the_token_is_redeemed(self):
        token = self.manager.login_ncryptsec(
            userd.ncryptsec_encrypt("11" * 32, "hunter2", log_n=10), "hunter2")["token"]
        with self.assertRaisesRegex(userd.UserError, "No key is held"):
            self.sign()
        self.manager.redeem(SIGNING_USER, token)
        self.assertEqual(self.sign(), SIGNING_PUBKEY)

    def test_signs_and_encrypts_as_the_user(self):
        self.log_in()
        event = self.sign("signer.sign_event",
                          event={"kind": 1, "created_at": 1700000000, "tags": [], "content": "hi"})
        self.assertEqual(event, userd.sign_event("11" * 32, userd.event_template(event)) | {
            "sig": event["sig"]})
        peer = CryptoTests.public("22" * 32)
        sealed = self.sign("signer.nip44_encrypt", pubkey=peer, plaintext="note")
        self.assertEqual(userd.nip44_decrypt("22" * 32, SIGNING_PUBKEY, sealed), "note")
        self.assertEqual(self.sign("signer.nip44_decrypt", pubkey=peer, ciphertext=sealed), "note")
        sealed = self.sign("signer.nip04_encrypt", pubkey=peer, plaintext="old note")
        self.assertEqual(self.sign("signer.nip04_decrypt", pubkey=peer, ciphertext=sealed),
                         "old note")

    def test_only_kwakore_may_sign(self):
        self.log_in()
        for pid in (4243, 4244, None):
            with self.assertRaisesRegex(userd.UserError, "may not sign"):
                self.sign(pid=pid)

    def test_other_callers_are_refused(self):
        self.log_in()
        self.system.accounts["kwak"] = Account("kwak", 1000, "/home/kwak")
        for uid in (0, 990, 1000):
            with self.assertRaisesRegex(userd.UserError, "Permission denied"):
                self.sign(uid=uid)

    def test_logout_and_signout_forget_the_key(self):
        self.log_in()
        self.manager.close_session(SIGNING_USER)
        with self.assertRaisesRegex(userd.UserError, "No key is held"):
            self.sign()
        self.manager.check_unlock(SIGNING_USER, "hunter2")
        self.assertEqual(self.sign(), SIGNING_PUBKEY)
        self.manager.request_signout(SIGNING_USER)
        with self.assertRaisesRegex(userd.UserError, "No key is held"):
            self.sign()

    def test_guest_key_is_held_for_its_session(self):
        token = self.manager.login_nsec("11" * 32)["token"]
        self.manager.redeem(SIGNING_USER, token)
        self.assertEqual(self.sign(), SIGNING_PUBKEY)

    def test_switching_to_a_signed_in_account_holds_its_key(self):
        self.log_in()
        self.manager.close_session(SIGNING_USER)
        self.sessions.add("7", SIGNING_USER)
        self.manager.unlock(SIGNING_USER, "hunter2")
        self.assertEqual(self.sign(), SIGNING_PUBKEY)

    def test_bunker_signs_through_nak_but_does_not_encrypt(self):
        token = self.manager.login_bunker("bunker://abc?relay=wss%3A%2F%2Fr")["token"]
        self.manager.redeem(SIGNING_USER, token)
        template = {"kind": 1, "created_at": 1, "tags": [], "content": "hi"}
        self.assertEqual(self.sign("signer.sign_event", event=template)["pubkey"], SIGNING_PUBKEY)
        self.assertEqual(self.nak.signed[0], template)
        self.assertEqual(self.nak.signed[1], "bunker://abc?relay=wss%3A%2F%2Fr")
        with self.assertRaisesRegex(userd.UserError, "not supported"):
            self.sign("signer.nip44_encrypt", pubkey=CryptoTests.public("22" * 32), plaintext="x")
        self.nak.pubkey = OTHER
        with self.assertRaisesRegex(userd.UserError, "different identity"):
            self.sign("signer.sign_event", event=template)


if __name__ == "__main__":
    unittest.main()
