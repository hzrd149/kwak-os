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
        return self.pubkey

    def fetch(self, pubkey, relays, kinds=(0, 10002)):
        return self.events


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
        self.manager = userd.UserManager(
            self.config, run=self.system, nak=self.nak, clock=self.clock, users=self.system,
            executable="/bin/kwak-userd",
        )

    def commands(self, name):
        return [c for c in self.system.commands if c[0] == name]

    def stored_key(self):
        path = self.manager.key_dir(USER) / "key.json"
        return json.loads(path.read_text()) if path.exists() else None


USER = "n3bf0c63fcb"


class ProvisioningTests(ManagerCase):
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
        self.assertEqual(self.manager.known(), [])
        with self.assertRaisesRegex(userd.UserError, "Unknown identity"):
            self.manager.unlock(USER, "anything")

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


class PermissionTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.server = userd.Server(self.manager)
        self.manager.login_nsec("11" * 32, "hunter2")
        self.system.accounts["kwak"] = Account("kwak", 1000, "/home/kwak")

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

    def test_identity_signs_out_only_itself(self):
        self.server.dispatch(30000, {"op": "signout", "username": "kwak"})
        self.assertEqual(self.commands("systemd-run")[-1][-3:], ["/bin/kwak-userd", "remove", USER])


if __name__ == "__main__":
    unittest.main()
