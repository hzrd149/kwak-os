#!/usr/bin/env python3
"""Nostr identities as Unix users: the root daemon and its PAM helpers.

Each Unix user is named ``n`` + the first 10 hex characters of a Nostr public
key and is created on first sign-in:

- nsec with a password: saved, with the key kept as an ncryptsec.
- nsec without a password: temporary, deleted when the session ends.
- ncryptsec: saved, with the ncryptsec kept as given.
- bunker: saved once the bunker confirms the pubkey by signing a challenge.

Saved users are removed, with all their data, when they sign out of the computer.
"""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request


SOCKET_PATH = "/run/kwak-userd.sock"
CONFIG_PATH = "/etc/kwak/userd.json"
DEFAULTS = {
    "state_dir": "/var/lib/kwak-userd",
    "skel": "/etc/kwak/skel",
    "hooks": "/etc/kwak/user-setup.d",
    "shell": "/run/current-system/sw/bin/bash",
    "groups": ["nostr"],
    "uid_min": 30000,
    "uid_max": 39999,
    "greeter_user": "greeter",
    "relays": ["wss://purplepag.es", "wss://relay.damus.io", "wss://nos.lol"],
    "token_ttl": 60,
}
USERNAME = re.compile(r"^n[0-9a-f]{10}$")
HEX_KEY = re.compile(r"^[0-9a-f]{64}$")
AVATAR_LIMIT = 2 * 1024 * 1024


class UserError(Exception):
    """A failure that can be shown to the person signing in."""


# --- NIP-19 bech32 -----------------------------------------------------------

BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for bit, generator in enumerate(
            (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
        ):
            checksum ^= generator if (top >> bit) & 1 else 0
    return checksum


def _expand(prefix):
    return [ord(c) >> 5 for c in prefix] + [0] + [ord(c) & 31 for c in prefix]


def _convert(data, source, target, pad):
    acc = bits = 0
    out = []
    for value in data:
        acc = (acc << source) | value
        bits += source
        while bits >= target:
            bits -= target
            out.append((acc >> bits) & ((1 << target) - 1))
    if pad and bits:
        out.append((acc << (target - bits)) & ((1 << target) - 1))
    elif not pad and (bits >= source or (acc << (target - bits)) & ((1 << target) - 1)):
        raise UserError("Invalid key encoding.")
    return out


def bech32_encode(prefix, data):
    words = _convert(data, 8, 5, True)
    checksum = _polymod(_expand(prefix) + words + [0] * 6) ^ 1
    words += [(checksum >> 5 * (5 - i)) & 31 for i in range(6)]
    return prefix + "1" + "".join(BECH32[w] for w in words)


def bech32_decode(text):
    text = text.strip().lower()
    prefix, _, body = text.rpartition("1")
    if not prefix or len(body) < 6 or any(c not in BECH32 for c in body):
        raise UserError("Invalid key encoding.")
    words = [BECH32.index(c) for c in body]
    if _polymod(_expand(prefix) + words) != 1:
        raise UserError("The key has a typo (checksum mismatch).")
    return prefix, bytes(_convert(words[:-6], 5, 8, False))


def npub(pubkey):
    return bech32_encode("npub", bytes.fromhex(pubkey))


def secret_hex(text):
    """Accept an nsec or 64-character hex secret key."""
    text = text.strip()
    if HEX_KEY.match(text.lower()):
        return text.lower()
    prefix, data = bech32_decode(text)
    if prefix != "nsec" or len(data) != 32:
        raise UserError("Expected an nsec secret key.")
    return data.hex()


# --- NIP-49 ncryptsec --------------------------------------------------------


def _nip49_key(password, salt, log_n):
    password = unicodedata.normalize("NFKC", password).encode()
    n = 1 << log_n
    return hashlib.scrypt(
        password, salt=salt, n=n, r=8, p=1, dklen=32, maxmem=128 * 8 * n + (1 << 20)
    )


def ncryptsec_encrypt(secret, password, log_n=16, key_security=2):
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt

    salt, nonce = os.urandom(16), os.urandom(24)
    associated = bytes([key_security])
    ciphertext = crypto_aead_xchacha20poly1305_ietf_encrypt(
        bytes.fromhex(secret), associated, nonce, _nip49_key(password, salt, log_n)
    )
    return bech32_encode(
        "ncryptsec", bytes([2, log_n]) + salt + nonce + associated + ciphertext
    )


def ncryptsec_decrypt(text, password):
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
    from nacl.exceptions import CryptoError

    prefix, data = bech32_decode(text)
    if prefix != "ncryptsec" or len(data) != 91 or data[0] != 2:
        raise UserError("Expected a version 2 ncryptsec.")
    log_n, salt, nonce, associated = data[1], data[2:18], data[18:42], data[42:43]
    # 2^20 needs 1 GiB; a pasted ncryptsec must not exhaust the machine's memory.
    if log_n > 20:
        raise UserError("The ncryptsec asks for too much memory to decrypt.")
    try:
        secret = crypto_aead_xchacha20poly1305_ietf_decrypt(
            data[43:], associated, nonce, _nip49_key(password, salt, log_n)
        )
    except CryptoError:
        raise UserError("Wrong password.") from None
    return secret.hex()


# --- Shared helpers ----------------------------------------------------------


def username_for(pubkey):
    return "n" + pubkey[:10]


def load_config(path=CONFIG_PATH):
    config = dict(DEFAULTS)
    with contextlib.suppress(FileNotFoundError):
        config.update(json.loads(Path(path).read_text()))
    return config


def write_private(path, text, mode=0o600, owner=None):
    """Atomically replace ``path`` without ever exposing it with wider permissions."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    data = text if isinstance(text, bytes) else text.encode()
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if owner is not None:
            os.chown(temporary, *owner)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_events(output):
    events = []
    for line in output.splitlines():
        with contextlib.suppress(ValueError):
            event = json.loads(line)
            if isinstance(event, dict) and "kind" in event:
                events.append(event)
    return events


def latest(events, kind, pubkey):
    matching = [e for e in events if e.get("kind") == kind and e.get("pubkey") == pubkey]
    return max(matching, key=lambda e: e.get("created_at", 0), default=None)


def relay_list(event):
    return [
        tag[1] for tag in (event or {}).get("tags", [])
        if len(tag) >= 2 and tag[0] == "r" and tag[1].startswith(("wss://", "ws://"))
    ]


def gecos(name):
    return re.sub(r"[:,=\\\x00-\x1f\x7f]", "", name or "").strip()[:64]


# --- Nostr operations through nak --------------------------------------------


class Nak:
    """Runs nak without ever putting a secret on the command line."""

    def __init__(self, run=subprocess.run, executable="nak"):
        self.run = run
        self.executable = executable

    def _call(self, args, stdin=None, env=None, timeout=20):
        try:
            result = self.run(
                [self.executable, *args], input=stdin, capture_output=True, text=True,
                timeout=timeout, check=False, env={**os.environ, **(env or {})},
            )
        except subprocess.TimeoutExpired:
            raise UserError("Timed out waiting for the Nostr network.") from None
        except OSError as error:
            raise UserError(f"Cannot run nak: {error}") from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise UserError(f"nak failed: {detail[-1] if detail else 'unknown error'}")
        return result.stdout

    def generate(self):
        return self._call(["key", "generate"]).strip()

    def public_key(self, secret):
        pubkey = self._call(["key", "public"], stdin=secret + "\n").strip()
        if not HEX_KEY.match(pubkey):
            raise UserError("Could not derive the public key.")
        return pubkey

    def bunker_pubkey(self, uri, home):
        """Sign a fresh challenge through the bunker and return the signing pubkey."""
        if not uri.startswith("bunker://"):
            raise UserError("Expected a bunker:// URI.")
        challenge = f"kwakos-login:{socket.gethostname()}:{secrets.token_hex(16)}"
        output = self._call(
            ["event", "-k", "22242", "-c", challenge],
            env={"NOSTR_SECRET_KEY": uri, "HOME": str(home)}, timeout=120,
        )
        events = parse_events(output)
        if len(events) != 1 or events[0].get("content") != challenge:
            raise UserError("The signer returned an unexpected event.")
        self._call(["verify"], stdin=json.dumps(events[0]))
        pubkey = events[0].get("pubkey", "")
        if not HEX_KEY.match(pubkey):
            raise UserError("The signer returned an invalid public key.")
        return pubkey

    def fetch(self, pubkey, relays, kinds=(0, 10002)):
        args = ["req", "-a", pubkey, "--limit", "10"]
        for kind in kinds:
            args += ["-k", str(kind)]
        try:
            return parse_events(self._call(args + list(relays), timeout=15))
        except UserError:
            return []


# --- The user manager --------------------------------------------------------


class UserManager:
    def __init__(self, config=None, run=subprocess.run, nak=None, clock=time.monotonic,
                 users=pwd, executable=None):
        self.config = config or load_config()
        self.run = run
        self.nak = nak or Nak(run)
        self.clock = clock
        self.users = users
        self.executable = (
            executable or os.environ.get("KWAK_USERD") or os.path.abspath(sys.argv[0])
        )
        self.state = Path(self.config["state_dir"])
        self.tokens = {}
        self.lock = threading.RLock()

    # Registry ---------------------------------------------------------------

    @contextlib.contextmanager
    def registry(self):
        """Yield the registry under an exclusive lock and save it afterwards."""
        # Traversable so the greeter can read avatars; keys stay private.
        self.state.mkdir(mode=0o711, parents=True, exist_ok=True)
        with self.lock, open(self.state / "users.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.state / "users.json"
            try:
                data = json.loads(path.read_text())
            except FileNotFoundError:
                data = {}
            before = json.dumps(data, sort_keys=True)
            yield data
            if json.dumps(data, sort_keys=True) != before:
                write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    def entry(self, username):
        with self.registry() as data:
            return data.get(username)

    def key_dir(self, username):
        return self.state / "keys" / username

    # Sign-in ------------------------------------------------------------------

    def login_nsec(self, nsec, password=None):
        """With a password the identity is kept as an ncryptsec; without one it is temporary."""
        secret = secret_hex(nsec)
        pubkey = self.nak.public_key(secret)
        if not password:
            return self._login(pubkey, "nsec", None)
        self._check_password(password)
        return self._login(pubkey, "ncryptsec", {"ncryptsec": ncryptsec_encrypt(secret, password)})

    def login_ncryptsec(self, ncryptsec, password):
        secret = ncryptsec_decrypt(ncryptsec, password)
        pubkey = self.nak.public_key(secret)
        return self._login(pubkey, "ncryptsec", {"ncryptsec": ncryptsec.strip()})

    def login_bunker(self, uri, username=None):
        """Confirm the bunker's pubkey; nak's client state is kept so the signer remembers us."""
        uri = uri.strip()
        if username:
            home = self.key_dir(username) / "nak"
        else:
            home = self.state / "bunker-clients" / secrets.token_hex(8)
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            pubkey = self.nak.bunker_pubkey(uri, home)
            if username and username_for(pubkey) != username:
                raise UserError("The signer answered for a different identity.")
            result = self._login(pubkey, "bunker", {"bunker": uri})
            if not username:
                client = self.key_dir(result["username"]) / "nak"
                shutil.rmtree(client, ignore_errors=True)
                shutil.move(home, client)
            return result
        finally:
            if not username:
                shutil.rmtree(home, ignore_errors=True)

    def create_identity(self, password=None):
        secret = self.nak.generate()
        pubkey = self.nak.public_key(secret)
        ncryptsec = None
        if password:
            self._check_password(password)
            ncryptsec = ncryptsec_encrypt(secret, password)
            result = self._login(pubkey, "ncryptsec", {"ncryptsec": ncryptsec}, new=True)
        else:
            result = self._login(pubkey, "nsec", None, new=True)
        result.update(nsec=bech32_encode("nsec", bytes.fromhex(secret)), ncryptsec=ncryptsec)
        return result

    def unlock(self, username, password=None):
        """Sign in to a saved identity: its ncryptsec password, or bunker approval."""
        entry = self.entry(username)
        if entry is None or entry.get("temporary"):
            raise UserError("Unknown identity.")
        material = json.loads((self.key_dir(username) / "key.json").read_text())
        if entry["method"] == "bunker":
            return self.login_bunker(material["bunker"], username)
        return self.login_ncryptsec(material["ncryptsec"], password or "")

    @staticmethod
    def _check_password(password):
        if len(password or "") < 4:
            raise UserError("Choose a password of at least 4 characters.")

    def _login(self, pubkey, method, material, new=False):
        """Create or reuse the user for ``pubkey`` and issue a login token.

        ``material`` is the key to keep (an ncryptsec or bunker URI). None signs
        in without saving anything: a new user is then temporary, and an
        existing saved user keeps its stored key.
        """
        username = username_for(pubkey)
        # Network lookups happen before taking the registry lock.
        events = self.nak.fetch(pubkey, self.config["relays"]) if not self.entry(username) else []
        with self.registry() as data:
            entry = data.get(username)
            if entry and entry["pubkey"] != pubkey:
                raise UserError(
                    f"{username} already belongs to a different key with the same prefix."
                )
            if new and entry:
                raise UserError("Generated a key that already exists; try again.")
            if entry is None:
                taken = {other["uid"] for other in data.values()}
                entry = self.provision(username, pubkey, method, taken, events)
                entry["temporary"] = material is None
                data[username] = entry
            if material is not None:
                # Saving a key makes a temporary identity permanent.
                keys = self.key_dir(username)
                keys.parent.mkdir(mode=0o700, exist_ok=True)
                keys.mkdir(mode=0o700, exist_ok=True)
                write_private(keys / "key.json", json.dumps(material) + "\n")
                entry.update(method=method, temporary=False)
            temporary = entry["temporary"]
        return {"username": username, "token": self.issue(username), "temporary": temporary}

    # Provisioning ------------------------------------------------------------

    def _exists(self, username):
        try:
            return self.users.getpwnam(username)
        except KeyError:
            return None

    def _free_uid(self, taken):
        for uid in range(self.config["uid_min"], self.config["uid_max"] + 1):
            if uid in taken:
                continue
            try:
                self.users.getpwuid(uid)
            except KeyError:
                return uid
        raise UserError("No free user IDs are left for new identities.")

    def _command(self, args, check=True):
        result = self.run(args, capture_output=True, text=True, check=False)
        if check and result.returncode:
            raise UserError(f"{args[0]} failed: {(result.stderr or result.stdout).strip()}")
        return result

    def provision(self, username, pubkey, method, taken, events):
        """Create the Unix account; the caller holds the registry lock."""
        if not USERNAME.match(username):
            raise UserError("Invalid identity name.")
        if self._exists(username):
            raise UserError(f"A system account named {username} already exists.")
        profile = self._profile(latest(events, 0, pubkey))
        uid = self._free_uid(taken)
        self._command([
            "useradd", "--create-home", "--skel", self.config["skel"],
            "--uid", str(uid), "--user-group", "--groups", ",".join(self.config["groups"]),
            "--shell", self.config["shell"], "--comment", gecos(profile.get("name")),
            username,
        ])
        try:
            account = self.users.getpwnam(username)
            home = Path(account.pw_dir)
            owner = (account.pw_uid, account.pw_gid)
            config = home / ".config" / "kwak"
            for directory in (home / ".config", config):
                directory.mkdir(mode=0o700, exist_ok=True)
                os.chown(directory, *owner)
            identity = {"pubkey": pubkey, "npub": npub(pubkey)}
            write_private(
                config / "identity.json", json.dumps(identity, indent=2) + "\n", owner=owner
            )
            relays = relay_list(latest(events, 10002, pubkey))
            if relays:
                write_private(config / "relays.json", json.dumps(relays) + "\n", owner=owner)
            self._save_avatar(username, profile.get("picture"))
            self._run_hooks(username, pubkey, home)
        except BaseException:
            # The registry is not saved on failure, so don't leave an orphan account.
            self._command(["userdel", "--remove", username], check=False)
            self._command(["groupdel", username], check=False)
            raise
        return {
            "pubkey": pubkey, "uid": uid, "method": method,
            "name": profile.get("name") or "", "created": int(time.time()),
        }

    @staticmethod
    def _profile(event):
        try:
            content = json.loads((event or {}).get("content") or "{}")
        except ValueError:
            content = {}
        if not isinstance(content, dict):
            content = {}
        name = content.get("display_name") or content.get("name") or ""
        picture = content.get("picture") or ""
        return {
            "name": name if isinstance(name, str) else "",
            "picture": picture if isinstance(picture, str) else "",
        }

    def _save_avatar(self, username, url):
        if not url or not url.startswith("https://"):
            return
        avatars = self.state / "avatars"
        avatars.mkdir(mode=0o755, exist_ok=True)
        os.chmod(avatars, 0o755)
        with contextlib.suppress(OSError, ValueError):
            request = urllib.request.Request(url, headers={"User-Agent": "kwak-userd"})
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read(AVATAR_LIMIT + 1)
            if len(data) <= AVATAR_LIMIT:
                write_private(avatars / username, data, mode=0o644)

    def _run_hooks(self, username, pubkey, home):
        hooks = Path(self.config["hooks"])
        if not hooks.is_dir():
            return
        env = {**os.environ, "KWAK_USER": username, "KWAK_PUBKEY": pubkey, "KWAK_HOME": str(home)}
        for hook in sorted(hooks.iterdir()):
            if hook.is_file() and os.access(hook, os.X_OK):
                result = self.run([str(hook)], env=env, capture_output=True, text=True, check=False)
                if result.returncode:
                    print(f"kwak-userd: hook {hook.name} failed for {username}: "
                          f"{result.stderr.strip()}", file=sys.stderr)

    # Tokens and sessions -----------------------------------------------------

    def issue(self, username):
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.tokens = {t: v for t, v in self.tokens.items() if v["expires"] > now}
            self.tokens[token] = {"username": username, "expires": now + self.config["token_ttl"]}
        return token

    def redeem(self, username, token):
        with self.lock:
            grant = self.tokens.pop(token, None)
        if grant is None or grant["username"] != username or grant["expires"] <= self.clock():
            raise UserError("Invalid or expired login token.")
        return {"username": username}

    def close_session(self, username):
        """Logging out of a temporary identity deletes it."""
        entry = self.managed(username)
        if entry is not None and entry.get("temporary"):
            self.schedule_removal(username)
            return {"scheduled": True}
        return {"scheduled": False}

    def known(self):
        """Saved identities for the greeter; temporary ones are never listed."""
        with self.registry() as data:
            items = sorted(
                (item for item in data.items() if not item[1].get("temporary")),
                key=lambda item: item[1].get("created", 0),
            )
        avatars = self.state / "avatars"
        return [
            {
                "username": username, "npub": npub(entry["pubkey"]),
                "name": entry.get("name") or "", "method": entry["method"],
                "avatar": str(avatars / username) if (avatars / username).exists() else "",
            }
            for username, entry in items
        ]

    # Removal -----------------------------------------------------------------

    def managed(self, username):
        """Return the registry entry only for a user this daemon may delete."""
        if not USERNAME.match(username or ""):
            return None
        entry = self.entry(username)
        if entry is None or not self.config["uid_min"] <= entry["uid"] <= self.config["uid_max"]:
            return None
        account = self._exists(username)
        if account is not None and account.pw_uid != entry["uid"]:
            return None
        return entry

    def schedule_removal(self, username):
        """Remove the user from a transient unit that outlives their session."""
        self._command([
            "systemd-run", "--no-block", "--collect", "--quiet",
            f"--unit=kwak-userd-remove-{username}", self.executable, "remove", username,
        ])

    def request_signout(self, username):
        if self.managed(username) is None:
            raise UserError("Only Nostr identities can sign out of this computer.")
        self.schedule_removal(username)
        return {"scheduled": True}

    def remove(self, username, wait=time.sleep):
        entry = self.managed(username)
        if entry is None:
            raise UserError(f"{username} is not a removable Nostr identity.")
        uid = str(entry["uid"])
        if self._exists(username):
            self._command(["loginctl", "terminate-user", username], check=False)
            self._command(["systemctl", "stop", f"user@{uid}.service"], check=False)
            for _ in range(20):
                self._command(["pkill", "-KILL", "-U", uid], check=False)
                if self._command(["pgrep", "-U", uid], check=False).returncode == 1:
                    break
                wait(0.5)
            else:
                raise UserError(f"Processes of {username} are still running.")
            self._command(["userdel", "--remove", username], check=False)
            if self._exists(username):
                raise UserError(f"userdel could not remove {username}.")
        self._command(["groupdel", username], check=False)
        self._command(
            ["find", "/tmp", "/var/tmp", "/dev/shm", "-xdev", "-uid", uid, "-delete"], check=False
        )
        doomed = [
            self.key_dir(username), self.state / "avatars" / username,
            Path("/var/lib/systemd/linger") / username,
            Path("/nix/var/nix/profiles/per-user") / username,
            Path("/nix/var/nix/gcroots/per-user") / username,
            Path("/var/spool/cron") / username, Path("/var/spool/mail") / username,
        ]
        doomed += Path("/var/log/journal").glob(f"*/user-{uid}*.journal*")
        for path in doomed:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
        with self.registry() as data:
            data.pop(username, None)
        return {"removed": username}

    def cleanup_temporary(self, wait=time.sleep):
        """Remove temporary identities left over from a crash or power loss."""
        with self.registry() as data:
            leftovers = [name for name, entry in data.items() if entry.get("temporary")]
        removed = []
        for username in leftovers:
            try:
                self.remove(username, wait=wait)
                removed.append(username)
            except UserError as error:
                print(f"kwak-userd: {error}", file=sys.stderr)
        return removed


# --- Socket server -----------------------------------------------------------

GREETER_OPS = {"list_known", "login_nsec", "login_ncryptsec", "login_bunker",
               "create_identity", "unlock"}
ROOT_OPS = GREETER_OPS | {"redeem", "close_session", "remove", "list"}


def peer_uid(connection):
    credentials = connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    return struct.unpack("3i", credentials)[1]


class Server:
    def __init__(self, manager):
        self.manager = manager

    def role(self, uid):
        if uid == 0:
            return "root", None
        try:
            name = self.manager.users.getpwuid(uid).pw_name
        except KeyError:
            return "unknown", None
        if name == self.manager.config["greeter_user"]:
            return "greeter", name
        if self.manager.managed(name) is not None:
            return "identity", name
        return "unknown", name

    def dispatch(self, uid, request):
        op = request.get("op")
        role, name = self.role(uid)
        allowed = (
            (role == "root" and op in ROOT_OPS)
            or (role == "greeter" and op in GREETER_OPS)
            or (role == "identity" and op == "signout")
        )
        if not allowed:
            raise UserError("Permission denied.")
        m, r = self.manager, request
        handlers = {
            "list_known": lambda: m.known(),
            "list": lambda: m.known(),
            "login_nsec": lambda: m.login_nsec(r["nsec"], r.get("password")),
            "login_ncryptsec": lambda: m.login_ncryptsec(r["ncryptsec"], r["password"]),
            "login_bunker": lambda: m.login_bunker(r["uri"]),
            "create_identity": lambda: m.create_identity(r.get("password")),
            "unlock": lambda: m.unlock(r["username"], r.get("password")),
            "redeem": lambda: m.redeem(r["username"], r["token"]),
            "close_session": lambda: m.close_session(r["username"]),
            "remove": lambda: m.remove(r["username"]),
            "signout": lambda: m.request_signout(name),
        }
        return handlers[op]()

    def handle(self, connection):
        with connection:
            try:
                uid = peer_uid(connection)
                stream = connection.makefile("rwb")
                line = stream.readline(1 << 16)
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError
                response = {"ok": True, "result": self.dispatch(uid, request)}
            except UserError as error:
                response = {"ok": False, "error": str(error)}
            except (KeyError, ValueError, TypeError):
                response = {"ok": False, "error": "Malformed request."}
            except Exception as error:  # Keep serving other clients.
                print(f"kwak-userd: {type(error).__name__}: {error}", file=sys.stderr)
                response = {"ok": False, "error": "Internal error; see the system journal."}
            with contextlib.suppress(OSError):
                stream.write(json.dumps(response).encode() + b"\n")
                stream.flush()

    def serve(self, listener):
        while True:
            connection, _ = listener.accept()
            connection.settimeout(300)
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()


def listener():
    if os.environ.get("LISTEN_PID") == str(os.getpid()) and os.environ.get("LISTEN_FDS") == "1":
        return socket.socket(fileno=3)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SOCKET_PATH)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)
    sock.listen(16)
    return sock


def request(op, path=SOCKET_PATH, timeout=180, **fields):
    """Send one request to the daemon and return its result or raise UserError."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(path)
        except OSError as error:
            raise UserError(f"The user service is unavailable: {error}") from error
        stream = sock.makefile("rwb")
        stream.write(json.dumps({"op": op, **fields}).encode() + b"\n")
        stream.flush()
        response = json.loads(stream.readline() or b"{}")
    if not response.get("ok"):
        raise UserError(response.get("error") or "No response from the user service.")
    return response["result"]


# --- Command line ------------------------------------------------------------


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else "help"
    try:
        if command == "serve":
            Server(UserManager()).serve(listener())
        elif command == "pam-redeem":
            # pam_exec expose_authtok writes the token, NUL-terminated, to stdin.
            token = sys.stdin.buffer.read(4096).split(b"\0")[0].decode().strip()
            if not token or not USERNAME.match(os.environ.get("PAM_USER", "")):
                return 1
            request("redeem", username=os.environ["PAM_USER"], token=token, timeout=10)
        elif command == "pam-close":
            if USERNAME.match(os.environ.get("PAM_USER", "")):
                request("close_session", username=os.environ["PAM_USER"], timeout=10)
        elif command == "signout":
            request("signout")
            print("Signing out; this account and all its data are being deleted.")
        elif command == "list":
            for user in request("list"):
                print(f"{user['username']}  {user['method']:9}  {user['npub']}  {user['name']}")
        elif command == "remove" and len(argv) == 2:
            print(json.dumps(UserManager().remove(argv[1])))
        elif command == "cleanup":
            print(json.dumps(UserManager().cleanup_temporary()))
        else:
            print("usage: kwak-userd serve|list|remove USER|cleanup|signout", file=sys.stderr)
            return 2
    except UserError as error:
        print(f"kwak-userd: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
