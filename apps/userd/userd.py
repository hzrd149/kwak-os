#!/usr/bin/env python3
"""Nostr identities as Unix users: the root daemon and its PAM helpers.

Each Unix user is named ``n`` + the first 10 hex characters of a Nostr public
key and is created on first sign-in:

- nsec with a password: saved, with the key kept as an ncryptsec.
- nsec without a password: temporary, deleted when the session ends.
- ncryptsec: saved, with the ncryptsec kept as given.
- bunker: saved once the bunker confirms the pubkey by signing a challenge.
- swipe card: an SKC1 card signs in like an nsec, an SKC2 card like an
  ncryptsec, and an SKC3 card like a bunker paired with the card's client key.
  kwak-cards reads the card and the greeter asks for any password.

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
import urllib.parse
import urllib.request
from urllib.parse import urlparse


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
    "card_user": "kwak-cards",
    "hidraw": "/sys/class/hidraw",
    "card_ttl": 120,
    # A greetd instance on the next free VT, for switching accounts mid-session.
    "switch_unit": "kwak-greeter-switch",
    "relays": ["wss://purplepag.es", "wss://relay.damus.io", "wss://nos.lol"],
    "token_ttl": 60,
    # Programs that may sign as the user they run as: kwakore's daemon, by the
    # path /proc/PID/exe shows.
    "signer_clients": [],
    "proc": "/proc",
}
USERNAME = re.compile(r"^n[0-9a-f]{10}$")
HEX_KEY = re.compile(r"^[0-9a-f]{64}$")
AVATAR_LIMIT = 2 * 1024 * 1024
# The MSR90 magnetic card reader, read by kwak-cards.
CARD_READER = (0xC216, 0x0180)


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


# --- Signing for the session --------------------------------------------------
#
# kwakore's "system" signer mode asks kwak-userd to sign for the user it runs as
# (kwakore's docs/system-signer.md). Keys held in memory sign here, so nothing
# secret reaches a command line; bunker identities sign through nak.


def event_template(event):
    """The unsigned fields of an event to sign, checked."""
    if not isinstance(event, dict):
        raise UserError("Expected an event.")
    kind, created_at = event.get("kind"), event.get("created_at")
    tags, content = event.get("tags", []), event.get("content")
    if (not isinstance(kind, int) or isinstance(kind, bool) or not 0 <= kind <= 65535
            or not isinstance(created_at, int) or isinstance(created_at, bool)
            or not 0 <= created_at < 1 << 40 or not isinstance(content, str)
            or not isinstance(tags, list)
            or any(not isinstance(tag, list) or any(not isinstance(item, str) for item in tag)
                   for tag in tags)):
        raise UserError("Invalid event.")
    return {"kind": kind, "created_at": created_at, "tags": tags, "content": content}


def event_id(pubkey, template):
    serialized = json.dumps(
        [0, pubkey, template["created_at"], template["kind"], template["tags"],
         template["content"]],
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def sign_event(secret, template):
    from coincurve import PrivateKey, PublicKeyXOnly

    key = bytes.fromhex(secret)
    pubkey = PublicKeyXOnly.from_secret(key).format().hex()
    event_hash = event_id(pubkey, template)
    signature = PrivateKey(key).sign_schnorr(bytes.fromhex(event_hash), os.urandom(32))
    return {"id": event_hash, "pubkey": pubkey, **template, "sig": signature.hex()}


def shared_x(secret, pubkey):
    """The x coordinate of the ECDH point, as NIP-04 and NIP-44 use it."""
    from coincurve import PublicKey

    if not HEX_KEY.match(pubkey or ""):
        raise UserError("Invalid public key.")
    try:
        point = PublicKey(b"\x02" + bytes.fromhex(pubkey)).multiply(bytes.fromhex(secret))
    except ValueError:
        raise UserError("Invalid public key.") from None
    return point.format(compressed=True)[1:]


def _nip44_keys(conversation, nonce):
    import hmac

    okm, block = b"", b""
    for counter in range(1, 4):
        block = hmac.new(conversation, block + nonce + bytes([counter]), "sha256").digest()
        okm += block
    return okm[:32], okm[32:44], okm[44:76]


def _nip44_padded_length(length):
    if length <= 32:
        return 32
    next_power = 1 << (length - 1).bit_length()
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * ((length - 1) // chunk + 1)


def _chacha20(key, nonce, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

    return Cipher(algorithms.ChaCha20(key, b"\0" * 4 + nonce), mode=None).encryptor().update(data)


def nip44_conversation_key(secret, pubkey):
    import hmac

    return hmac.new(b"nip44-v2", shared_x(secret, pubkey), "sha256").digest()


def nip44_encrypt(secret, pubkey, plaintext, nonce=None):
    import base64
    import hmac

    data = plaintext.encode() if isinstance(plaintext, str) else b""
    if not 1 <= len(data) <= 65535:
        raise UserError("The message must be 1 to 65535 bytes.")
    nonce = nonce or os.urandom(32)
    key, chacha_nonce, mac_key = _nip44_keys(nip44_conversation_key(secret, pubkey), nonce)
    padded = len(data).to_bytes(2, "big") + data
    padded += b"\0" * (2 + _nip44_padded_length(len(data)) - len(padded))
    ciphertext = _chacha20(key, chacha_nonce, padded)
    mac = hmac.new(mac_key, nonce + ciphertext, "sha256").digest()
    return base64.b64encode(b"\x02" + nonce + ciphertext + mac).decode()


def nip44_decrypt(secret, pubkey, payload):
    import base64
    import binascii
    import hmac

    try:
        data = base64.b64decode(payload, validate=True) if isinstance(payload, str) else b""
    except (binascii.Error, ValueError):
        data = b""
    if not 99 <= len(data) <= 65603 or data[0] != 2:
        raise UserError("Not a NIP-44 version 2 payload.")
    nonce, ciphertext, mac = data[1:33], data[33:-32], data[-32:]
    key, chacha_nonce, mac_key = _nip44_keys(nip44_conversation_key(secret, pubkey), nonce)
    if not hmac.compare_digest(hmac.new(mac_key, nonce + ciphertext, "sha256").digest(), mac):
        raise UserError("The message could not be decrypted.")
    padded = _chacha20(key, chacha_nonce, ciphertext)
    length = int.from_bytes(padded[:2], "big")
    if not 1 <= length or len(padded) != 2 + _nip44_padded_length(length):
        raise UserError("The message could not be decrypted.")
    try:
        return padded[2:2 + length].decode()
    except UnicodeDecodeError:
        raise UserError("The message could not be decrypted.") from None


def nip04_encrypt(secret, pubkey, plaintext):
    import base64
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not isinstance(plaintext, str):
        raise UserError("Expected text.")
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(shared_x(secret, pubkey)), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return f"{base64.b64encode(ciphertext).decode()}?iv={base64.b64encode(iv).decode()}"


def nip04_decrypt(secret, pubkey, payload):
    import base64
    import binascii
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    try:
        body, _, iv = (payload if isinstance(payload, str) else "").partition("?iv=")
        ciphertext, iv = base64.b64decode(body, validate=True), base64.b64decode(iv, validate=True)
        decryptor = Cipher(algorithms.AES(shared_x(secret, pubkey)), modes.CBC(iv)).decryptor()
        unpadder = padding.PKCS7(128).unpadder()
        data = decryptor.update(ciphertext) + decryptor.finalize()
        return (unpadder.update(data) + unpadder.finalize()).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise UserError("The message could not be decrypted.") from None


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


def valid_url(value, schemes):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in schemes and bool(parsed.hostname) and not parsed.username and not parsed.password


def outbox_relays(event):
    return [tag[1] for tag in (event or {}).get("tags", [])
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "r"
            and (len(tag) < 3 or tag[2] != "read") and valid_url(tag[1], ("wss", "ws"))]


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

    @staticmethod
    def signer_env(signer, home=None):
        """nak's environment for a key or bunker URI.

        ``home`` keeps nak's bunker client state. A ``client-key`` there, from an
        SKC3 card, is the client key the signer was already paired with.
        """
        env = {"NOSTR_SECRET_KEY": signer}
        if home is not None:
            env["HOME"] = str(home)
            client_key = Path(home) / "client-key"
            if client_key.exists():
                env["NOSTR_CLIENT_KEY"] = client_key.read_text().strip()
        return env

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
            env=self.signer_env(uri, home), timeout=120,
        )
        events = parse_events(output)
        if len(events) != 1 or events[0].get("content") != challenge:
            raise UserError("The signer returned an unexpected event.")
        self._call(["verify"], stdin=json.dumps(events[0]))
        pubkey = events[0].get("pubkey", "")
        if not HEX_KEY.match(pubkey):
            raise UserError("The signer returned an invalid public key.")
        return pubkey

    def sign(self, template, signer, home=None):
        """Sign an event exactly as given, without publishing it."""
        output = self._call(["event"], stdin=json.dumps(template),
                            env=self.signer_env(signer, home), timeout=120)
        events = parse_events(output)
        if len(events) != 1 or any(events[0].get(name) != value
                                   for name, value in template.items()):
            raise UserError("The signer returned an unexpected event.")
        return events[0]

    def fetch(self, pubkey, relays, kinds=(0, 10002)):
        args = ["req", "-a", pubkey, "--limit", "10"]
        for kind in kinds:
            args += ["-k", str(kind)]
        try:
            return parse_events(self._call(args + list(relays), timeout=15))
        except UserError:
            return []

    def publish(self, kind, content, tags, relays, signer, home=None):
        event = {"kind": kind, "content": content, "tags": tags}
        output = self._call(["event", *relays], stdin=json.dumps(event),
                            env=self.signer_env(signer, home), timeout=120)
        events = parse_events(output)
        if len(events) != 1 or events[0].get("kind") != kind:
            raise UserError("nak did not return the published event.")
        return events[0]


def bunker_signer(uri):
    """The remote signer's pubkey in a bunker:// URI."""
    return urllib.parse.urlsplit(uri.strip()).netloc.lower()


def log(message):
    """A line for the journal; never card contents, keys, or passwords."""
    print(f"kwak-userd: {message}", file=sys.stderr, flush=True)


def usb_ids(path):
    """The (vendor, product) of the USB device above a sysfs path, if any."""
    for parent in (path, *path.parents):
        try:
            return (int((parent / "idVendor").read_text(), 16),
                    int((parent / "idProduct").read_text(), 16))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
    return None


def card_reader_present(hidraw):
    """Whether a card reader is plugged in, without opening it."""
    try:
        entries = list(Path(hidraw).iterdir())
    except OSError:
        return False
    return any(usb_ids((entry / "device").resolve()) == CARD_READER for entry in entries)


class Sessions:
    """Sessions on seat0, through loginctl."""

    PROPERTIES = ("Id", "Name", "Class", "State", "LockedHint", "VTNr", "Seat")

    def __init__(self, run=subprocess.run, proc="/proc"):
        self.run = run
        self.proc = Path(proc)

    def _loginctl(self, *args):
        result = self.run(["loginctl", "--no-pager", *args], capture_output=True, text=True,
                          check=False)
        return result.stdout if result.returncode == 0 else ""

    def show(self, session_id):
        output = self._loginctl("show-session", session_id,
                                *(f"--property={name}" for name in self.PROPERTIES))
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if not values.get("Id"):
            return None
        return {
            "id": values["Id"], "user": values.get("Name", ""),
            "class": values.get("Class", ""), "state": values.get("State", ""),
            "locked": values.get("LockedHint") == "yes", "seat": values.get("Seat", ""),
            "vt": int(values.get("VTNr") or 0),
        }

    def all(self):
        ids = [line.split()[0] for line in self._loginctl("list-sessions", "--no-legend")
               .splitlines() if line.strip()]
        sessions = (self.show(session_id) for session_id in ids)
        return [session for session in sessions if session and session["seat"] == "seat0"
                and session["state"] in ("active", "online")]

    def active(self):
        session_id = self._loginctl("show-seat", "seat0", "--property=ActiveSession",
                                    "--value").strip()
        return self.show(session_id) if session_id else None

    def of_user(self, username):
        return next((session for session in self.all()
                     if session["user"] == username and session["class"] == "user"), None)

    def of_pid(self, pid):
        """The session of a process, from its cgroup or its nearest ancestor's.

        kitty moves the programs it runs into their own scope, so the greeter
        inside it is found through kitty, which stays in the session.
        """
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None
        for _ in range(64):
            if pid <= 1:
                return None
            try:
                cgroup = (self.proc / str(pid) / "cgroup").read_text()
                status = (self.proc / str(pid) / "status").read_text()
            except OSError:
                return None
            match = re.search(r"/session-([^/.]+)\.scope", cgroup)
            if match:
                return self.show(match.group(1))
            parent = re.search(r"^PPid:\s*(\d+)", status, re.MULTILINE)
            pid = int(parent.group(1)) if parent else 0
        return None

    def activate(self, session_id):
        self._loginctl("activate", session_id)

    def lock(self, session_id):
        self._loginctl("lock-session", session_id)

    def unlock(self, session_id):
        self._loginctl("unlock-session", session_id)

    def chvt(self, vt):
        self.run(["chvt", str(vt)], capture_output=True, text=True, check=False)


# --- The user manager --------------------------------------------------------


class UserManager:
    def __init__(self, config=None, run=subprocess.run, nak=None, clock=time.monotonic,
                 users=pwd, executable=None, sessions=None):
        self.config = config or load_config()
        self.run = run
        self.nak = nak or Nak(run)
        self.sessions = sessions or Sessions(run)
        self.clock = clock
        self.users = users
        self.executable = (
            executable or os.environ.get("KWAK_USERD") or os.path.abspath(sys.argv[0])
        )
        self.state = Path(self.config["state_dir"])
        self.tokens = {}
        # Each signed-in user's secret key, held in memory only while their
        # session lasts, to sign for kwakore.
        self.held = {}
        self.lock = threading.RLock()
        # Card swipes: the latest public summary, and its secret by opaque id.
        self.cards = threading.Condition(threading.Lock())
        self.card_seq = 0
        self.card_event = None
        self.card_at = None
        self.swipes = {}
        # Account switching: when a switch greeter was started, and the session
        # each one was started from.
        self.switch_started = None
        self.switch_from = {}

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
            return self._login(pubkey, "nsec", None, secret=secret)
        self._check_password(password)
        return self._login(pubkey, "ncryptsec", {"ncryptsec": ncryptsec_encrypt(secret, password)},
                           secret=secret)

    def login_ncryptsec(self, ncryptsec, password):
        secret = ncryptsec_decrypt(ncryptsec, password)
        pubkey = self.nak.public_key(secret)
        return self._login(pubkey, "ncryptsec", {"ncryptsec": ncryptsec.strip()}, secret=secret)

    def login_bunker(self, uri, username=None, client_key=None):
        """Confirm the bunker's pubkey; nak's client state is kept so the signer remembers us.

        ``client_key`` is the paired client key from an SKC3 card. It is kept with
        nak's client state, so later sign-ins and signing use it too.
        """
        uri = uri.strip()
        if username:
            home = self.key_dir(username) / "nak"
        else:
            home = self.state / "bunker-clients" / secrets.token_hex(8)
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        if client_key is not None:
            if not HEX_KEY.match(client_key):
                raise UserError("The card's client key is invalid.")
            write_private(home / "client-key", client_key + "\n")
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
            result = self._login(pubkey, "ncryptsec", {"ncryptsec": ncryptsec}, new=True,
                                 secret=secret)
        else:
            result = self._login(pubkey, "nsec", None, new=True, secret=secret)
        result.update(nsec=bech32_encode("nsec", bytes.fromhex(secret)), ncryptsec=ncryptsec)
        return result

    def unlock(self, username, password=None):
        """Sign in to an identity on this computer.

        A temporary nsec identity has no password and opens directly; saved ones
        need their ncryptsec password or the bunker's approval.
        """
        entry = self.entry(username)
        if entry is None:
            raise UserError("Unknown identity.")
        if entry.get("temporary"):
            return self._grant(username, True, entry.get("name") or username)
        material = json.loads((self.key_dir(username) / "key.json").read_text())
        if entry["method"] == "bunker":
            return self.login_bunker(material["bunker"], username)
        return self.login_ncryptsec(material["ncryptsec"], password or "")

    @staticmethod
    def _check_password(password):
        if len(password or "") < 4:
            raise UserError("Choose a password of at least 4 characters.")

    def _login(self, pubkey, method, material, new=False, secret=None):
        """Create or reuse the user for ``pubkey`` and issue a login token.

        ``material`` is the key to keep (an ncryptsec or bunker URI). None signs
        in without saving anything: a new user is then temporary, and an
        existing saved user keeps its stored key. ``secret`` is the decrypted
        key, held for the session once the token is redeemed.
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
        return self._grant(username, temporary, entry.get("name") or username, secret)

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

    # Card swipes --------------------------------------------------------------

    def card_swipe(self, event):
        """A swipe from kwak-cards.

        At a sign-in screen, the greeter on screen gets it. During a session, the
        session is locked and a switch greeter opens on a new VT to handle it. A
        card of an account that is already signed in (an SKC1 card's key, or an
        SKC2 or SKC3 card that signed in to it before) unlocks and switches
        straight to that account's session instead, from anywhere.

        The secret stays here under an opaque id. The greeter only gets a summary
        and signs in with ``card_login``.
        """
        active = self.sessions.active()
        if active is None:
            log("card swipe ignored: no session on screen")
            return {"delivered": False}
        summary, secret = self._card_summary(event)
        if summary.get("known"):
            # Wherever the swipe happens, an account that is already signed in is
            # unlocked and switched to.
            session = self.sessions.of_user(summary["username"])
            if session is not None:
                log(f"card swipe: switching from session {active['id']} to {session['id']}")
                if active["class"] == "user" and session["id"] != active["id"]:
                    self._lock(active)
                self._switch_to(session)
                return {"delivered": True}
        if active["class"] == "greeter":
            log(f"card swipe ({summary.get('format') or 'unreadable'}) for the sign-in "
                f"screen, session {active['id']}")
            self._store_swipe(summary, secret)
            return {"delivered": True}
        if active["class"] != "user" or "error" in summary:
            log(f"card swipe ignored in {active['class']} session {active['id']}")
            return {"delivered": False}
        log(f"card swipe: locking session {active['id']} and opening a switch greeter")
        self._store_swipe(summary, secret)
        self._lock(active)
        self._start_switch_greeter(active)
        return {"delivered": True}

    def _store_swipe(self, summary, secret):
        with self.cards:
            self.card_seq += 1
            summary["seq"] = self.card_seq
            self.swipes = {}
            if secret is not None:
                self.swipes[summary["id"]] = dict(
                    secret, expires=self.clock() + self.config["card_ttl"])
            self.card_event = summary
            self.card_at = self.clock()
            self.cards.notify_all()

    def _fresh(self, card):
        """Whether a swipe can still be used: unexpired, or a just-reported error."""
        if "id" in card:
            swipe = self.swipes.get(card["id"])
            return swipe is not None and swipe["expires"] > self.clock()
        return self.clock() - self.card_at < 5

    def _card_summary(self, event):
        card_id = secrets.token_urlsafe(16)
        try:
            if event.get("format") == "SKC1":
                secret = secret_hex(event["secret_key"])
                username = username_for(self.nak.public_key(secret))
                entry = self.entry(username)
                return ({"id": card_id, "format": "SKC1", "username": username,
                         "known": entry is not None,
                         "name": (entry or {}).get("name") or username,
                         "password": "none" if entry else "optional"},
                        {"format": "SKC1", "secret": secret})
            if event.get("format") == "SKC3":
                uri, client_key = event["bunker"].strip(), event["client_key"]
                if not uri.startswith("bunker://") or not HEX_KEY.match(client_key):
                    raise UserError("Not a bunker connection.")
                secret = {"format": "SKC3", "bunker": uri, "client_key": client_key}
                summary = {"id": card_id, "format": "SKC3", "password": "none", "signer": True}
                return self._recognise(summary, secret), secret
            if event.get("format") == "SKC2":
                ncryptsec = event["ncryptsec"].strip().lower()
                if not ncryptsec.startswith("ncryptsec1"):
                    raise UserError("Not an ncryptsec.")
                secret = {"format": "SKC2", "ncryptsec": ncryptsec}
                summary = {"id": card_id, "format": "SKC2", "password": "required"}
                return self._recognise(summary, secret), secret
        except (UserError, KeyError, AttributeError):
            pass
        return {"error": "card_format"}, None

    def _recognise(self, summary, secret):
        """Name the saved account an SKC2 or SKC3 card signed in to before.

        Their keys are encrypted or remote, so the card is matched against what
        was stored: the same ncryptsec, or the same signer and client key.
        """
        owner = self._card_owner(secret)
        if owner is not None:
            username, entry = owner
            summary.update(known=True, username=username,
                           name=entry.get("name") or username)
        return summary

    def _card_owner(self, secret):
        with self.registry() as data:
            saved = [(name, dict(entry)) for name, entry in data.items()
                     if not entry.get("temporary")]
        for username, entry in saved:
            keys = self.key_dir(username)
            try:
                material = json.loads((keys / "key.json").read_text())
                if secret["format"] == "SKC2" and entry["method"] == "ncryptsec":
                    if (bech32_decode(material["ncryptsec"])
                            == bech32_decode(secret["ncryptsec"])):
                        return username, entry
                elif secret["format"] == "SKC3" and entry["method"] == "bunker":
                    client_key = (keys / "nak" / "client-key").read_text().strip()
                    if (bunker_signer(material["bunker"]) == bunker_signer(secret["bunker"])
                            and client_key == secret["client_key"]):
                        return username, entry
            except (OSError, ValueError, KeyError, UserError):
                continue
        return None

    def wait_card(self, after=None, wait=30, pid=None):
        """Wait for a swipe newer than ``after``; also says if a reader is plugged in.

        Only the greeter on screen gets swipes. ``after=0`` also returns a swipe
        made just before the greeter started, such as one that opened it.
        """
        wait = max(0, min(float(wait), 30))
        with self.cards:
            if after is None:
                after = self.card_seq
            self.cards.wait_for(lambda: self.card_seq > after, wait)
            card = self.card_event if self.card_seq > after else None
            if card is not None and not self._fresh(card):
                card = None
            seq = self.card_seq
        on_screen = pid is None or self._on_screen(pid)
        if not on_screen:
            card = None
        elif card is not None:
            self.switch_started = None
        # A switch greeter that is no longer on screen closes itself.
        return {"reader": card_reader_present(self.config["hidraw"]), "card": card,
                "seq": seq, "on_screen": on_screen}

    def card_login(self, card, password=None):
        """Sign in with a swiped card, like a pasted nsec or ncryptsec."""
        with self.cards:
            swipe = self.swipes.get(card)
            if swipe is not None and swipe["expires"] <= self.clock():
                del self.swipes[card]
                swipe = None
        if swipe is None:
            raise UserError("The card swipe has expired. Swipe the card again.")
        if swipe["format"] == "SKC1":
            result = self.login_nsec(swipe["secret"], password)
        elif swipe["format"] == "SKC3":
            result = self.login_bunker(swipe["bunker"], client_key=swipe["client_key"])
        else:
            result = self.login_ncryptsec(swipe["ncryptsec"], password or "")
        # A wrong password keeps the swipe, so the person can try again.
        with self.cards:
            self.swipes.pop(card, None)
        return result

    # Account switching ---------------------------------------------------------

    def _on_screen(self, pid):
        """Whether the process ``pid`` belongs to the session on screen."""
        session, active = self.sessions.of_pid(pid), self.sessions.active()
        return bool(session and active and session["id"] == active["id"])

    def _lock(self, session):
        """Lock a session, except a guest's: anyone could open it from the list."""
        entry = self.entry(session["user"])
        if not (entry and entry.get("temporary")):
            self.sessions.lock(session["id"])

    def _switch_to(self, session):
        self.sessions.activate(session["id"])
        self.sessions.unlock(session["id"])

    def _start_switch_greeter(self, previous):
        with self.cards:
            # One greeter at a time: a starting one picks up the latest swipe.
            if self.switch_started is not None and self.clock() - self.switch_started < 15:
                return
            self.switch_started = self.clock()
        instance = secrets.token_hex(8)
        self.switch_from[instance] = previous["id"]
        self._command(["systemctl", "start", "--no-block",
                       f"{self.config['switch_unit']}@{instance}.service"])

    def switch_done(self, instance, pid=None):
        """Close a switch greeter and, if it is on screen, return to a session.

        That is the session it was opened from (still locked), another session, or
        the sign-in screen on VT 1.
        """
        if not re.fullmatch(r"[0-9a-f]{16}", str(instance)):
            raise UserError("Unknown switch greeter.")
        previous = self.switch_from.pop(instance, None)
        caller = self.sessions.of_pid(pid) if pid is not None else None
        if pid is None or self._on_screen(pid):
            sessions = [session for session in self.sessions.all()
                        if not caller or session["id"] != caller["id"]]
            target = (
                next((session for session in sessions if session["id"] == previous), None)
                or next((session for session in sessions if session["class"] == "user"), None)
                or next((session for session in sessions if session["class"] == "greeter"),
                        None)
            )
            if target is not None:
                self.sessions.activate(target["id"])
            else:
                self.sessions.chvt(1)
        self._command(["systemctl", "stop", "--no-block",
                       f"{self.config['switch_unit']}@{instance}.service"])
        return {"closed": True}

    def check_unlock(self, username, password=None):
        """Check an identity's own way in, to unlock its locked session."""
        entry = self.managed(username)
        if entry is None:
            raise UserError("Not a Nostr identity.")
        if entry.get("temporary"):
            return {"ok": True}
        material = json.loads((self.key_dir(username) / "key.json").read_text())
        secret = None
        if entry["method"] == "bunker":
            pubkey = self.nak.bunker_pubkey(material["bunker"], self.key_dir(username) / "nak")
        else:
            secret = ncryptsec_decrypt(material["ncryptsec"], password or "")
            pubkey = self.nak.public_key(secret)
        if pubkey != entry["pubkey"]:
            raise UserError("That key belongs to a different identity.")
        if secret is not None:
            # Unlocking also restores signing after kwak-userd restarted.
            self._hold(username, secret)
        return {"ok": True}

    def _grant(self, username, temporary, name, secret=None):
        """A login token, or, if the account is already signed in, a switch to it."""
        session = self.sessions.of_user(username)
        if session is not None:
            if secret is not None:
                self._hold(username, secret)
            self._switch_to(session)
            return {"username": username, "switched": True, "temporary": temporary,
                    "name": name}
        return {"username": username, "token": self.issue(username, secret),
                "temporary": temporary, "name": name}

    # Tokens and sessions -----------------------------------------------------

    def issue(self, username, secret=None):
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.tokens = {t: v for t, v in self.tokens.items() if v["expires"] > now}
            self.tokens[token] = {"username": username, "expires": now + self.config["token_ttl"],
                                  "secret": secret}
        return token

    def redeem(self, username, token):
        with self.lock:
            grant = self.tokens.pop(token, None)
        if grant is None or grant["username"] != username or grant["expires"] <= self.clock():
            raise UserError("Invalid or expired login token.")
        if grant["secret"] is not None:
            self._hold(username, grant["secret"])
        return {"username": username}

    def _hold(self, username, secret):
        with self.lock:
            self.held[username] = secret

    def _drop(self, username):
        with self.lock:
            self.held.pop(username, None)

    def close_session(self, username):
        """Logging out forgets the key; logging out of a temporary identity deletes it."""
        self._drop(username)
        entry = self.managed(username)
        if entry is not None and entry.get("temporary"):
            self.schedule_removal(username)
            return {"scheduled": True}
        return {"scheduled": False}

    def known(self):
        """Identities on this computer for the greeter, oldest first."""
        with self.registry() as data:
            items = sorted(data.items(), key=lambda item: item[1].get("created", 0))
        avatars = self.state / "avatars"
        return [
            {
                "username": username, "npub": npub(entry["pubkey"]),
                "name": entry.get("name") or "", "method": entry["method"],
                "temporary": bool(entry.get("temporary")),
                "avatar": str(avatars / username) if (avatars / username).exists() else "",
            }
            for username, entry in items
        ]

    def refresh_profiles(self):
        """Fetch current kind 0 profiles without holding the registry lock on the network."""
        with self.registry() as data:
            identities = [(name, entry["pubkey"]) for name, entry in data.items()]
        updated = []
        for username, pubkey in identities:
            try:
                event = latest(self.nak.fetch(pubkey, self.config["relays"], kinds=(0,)),
                               0, pubkey)
                if event is None:
                    continue
                profile = self._profile(event)
                with self.registry() as data:
                    entry = data.get(username)
                    if entry is None or entry["pubkey"] != pubkey:
                        continue
                    entry["name"] = profile["name"]
                self._save_avatar(username, profile["picture"])
                updated.append(username)
            except (UserError, OSError) as error:
                print(f"kwak-userd: profile refresh failed for {username}: {error}",
                      file=sys.stderr)
        return updated

    def account_settings(self, username):
        entry = self.managed(username)
        if entry is None:
            raise UserError("Unknown Nostr identity.")
        pubkey = entry["pubkey"]
        events = self.nak.fetch(pubkey, self.config["relays"], kinds=(0, 10002, 10063))
        relay_event = latest(events, 10002, pubkey)
        relays = relay_list(relay_event)
        if relays:
            events += self.nak.fetch(pubkey, relays, kinds=(0, 10002, 10063))
            relay_event = latest(events, 10002, pubkey)
        profile = latest(events, 0, pubkey)
        media = latest(events, 10063, pubkey)
        try:
            fields = json.loads(profile["content"]) if profile else {}
        except (ValueError, TypeError):
            fields = {}
        if not isinstance(fields, dict):
            fields = {}
        return {"npub": npub(pubkey), "method": entry["method"],
                "profile": fields,
                "relays": [tag for tag in (relay_event or {}).get("tags", [])
                           if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "r"],
                "media_servers": [tag[1] for tag in (media or {}).get("tags", [])
                                  if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "server"]}

    def publish_settings(self, username, section, value, password=None):
        entry = self.managed(username)
        if entry is None:
            raise UserError("Unknown Nostr identity.")
        current = self.account_settings(username)
        outbox = outbox_relays({"tags": current["relays"]})
        if section == "profile":
            if not isinstance(value, dict) or any(key not in ("name", "display_name", "about", "picture", "banner", "website", "nip05", "lud16") or not isinstance(item, str) or len(item) > 4096 for key, item in value.items()):
                raise UserError("Invalid profile fields.")
            profile = dict(current["profile"])
            profile.update(value)
            kind, content, tags = 0, json.dumps(profile, ensure_ascii=False), []
        elif section == "relays":
            if not isinstance(value, list) or not 1 <= len(value) <= 20 or any(not isinstance(row, list) or len(row) != 2 or not valid_url(row[0], ("wss", "ws")) or row[1] not in ("read", "write", "both") for row in value):
                raise UserError("Enter 1–20 valid relay URLs and read/write modes.")
            if not any(row[1] != "read" for row in value):
                raise UserError("Keep at least one write relay.")
            tags = [["r", url] + ([] if mode == "both" else [mode]) for url, mode in value]
            outbox = outbox_relays({"tags": tags})
            kind, content = 10002, ""
        elif section == "media_servers":
            if not isinstance(value, list) or len(value) > 20 or any(not valid_url(url, ("https",)) for url in value):
                raise UserError("Enter valid HTTPS media server URLs.")
            kind, content, tags = 10063, "", [["server", url] for url in value]
        else:
            raise UserError("Unknown account settings section.")
        relays = list(dict.fromkeys([*outbox, *self.config["relays"]]))
        if entry["method"] == "bunker":
            signer = json.loads((self.key_dir(username) / "key.json").read_text())["bunker"]
            home = self.key_dir(username) / "nak"
        elif entry["method"] == "ncryptsec":
            material = json.loads((self.key_dir(username) / "key.json").read_text())
            signer = ncryptsec_decrypt(material["ncryptsec"], password or "")
            home = None
        else:
            signer = secret_hex(password or "")
            if self.nak.public_key(signer) != entry["pubkey"]:
                raise UserError("This nsec belongs to a different identity.")
            home = None
        event = self.nak.publish(kind, content, tags, relays, signer, home)
        if event.get("pubkey") != entry["pubkey"]:
            raise UserError("The signer returned an event for a different identity.")
        return {"id": event["id"], "relays": relays}

    # Signing for the session ---------------------------------------------------

    def _signer_client(self, pid):
        """Whether the process ``pid`` is a program trusted to ask before signing."""
        try:
            exe = os.readlink(Path(self.config["proc"]) / str(int(pid)) / "exe")
        except (OSError, TypeError, ValueError):
            return False
        return exe in self.config["signer_clients"]

    def sign_for(self, username, pid, op, request):
        """Sign, encrypt or decrypt as the signed-in ``username`` for kwakore."""
        entry = self.managed(username)
        if entry is None:
            raise UserError("Not a Nostr identity.")
        if not self._signer_client(pid):
            raise UserError("This program may not sign for you.")
        pubkey, bunker = entry["pubkey"], entry["method"] == "bunker"
        with self.lock:
            secret = self.held.get(username)
        if secret is None and not bunker:
            raise UserError("No key is held for this session; unlock it to sign.")
        if op == "signer.get_public_key":
            return pubkey
        if op == "signer.sign_event":
            template = event_template(request["event"])
            if bunker:
                material = json.loads((self.key_dir(username) / "key.json").read_text())
                event = self.nak.sign(template, material["bunker"], self.key_dir(username) / "nak")
            else:
                event = sign_event(secret, template)
            if event.get("pubkey") != pubkey:
                raise UserError("The signer answered for a different identity.")
            return event
        if bunker:
            # nak takes the text on its command line, where other users could read it.
            raise UserError("Encryption through a remote signer is not supported yet.")
        cipher = {
            "signer.nip44_encrypt": (nip44_encrypt, "plaintext"),
            "signer.nip44_decrypt": (nip44_decrypt, "ciphertext"),
            "signer.nip04_encrypt": (nip04_encrypt, "plaintext"),
            "signer.nip04_decrypt": (nip04_decrypt, "ciphertext"),
        }
        function, field = cipher[op]
        return function(secret, request["pubkey"], request[field])

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
        self._drop(username)
        self.schedule_removal(username)
        return {"scheduled": True}

    def remove(self, username, wait=time.sleep):
        entry = self.managed(username)
        if entry is None:
            raise UserError(f"{username} is not a removable Nostr identity.")
        self._drop(username)
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
               "create_identity", "unlock", "wait_card", "card_login", "switch_done"}
CARD_OPS = {"card_swipe"}
ROOT_OPS = GREETER_OPS | {"redeem", "close_session", "remove", "list"}
IDENTITY_OPS = {"signout", "account_settings", "publish_settings", "check_unlock"}
SIGNER_OPS = {"signer.get_public_key", "signer.sign_event", "signer.nip44_encrypt",
              "signer.nip44_decrypt", "signer.nip04_encrypt", "signer.nip04_decrypt"}


def peer_credentials(connection):
    """The caller's (pid, uid)."""
    credentials = connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    pid, uid, _gid = struct.unpack("3i", credentials)
    return pid, uid


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
        if name == self.manager.config["card_user"]:
            return "card", name
        if self.manager.managed(name) is not None:
            return "identity", name
        return "unknown", name

    def dispatch(self, uid, request, pid=None):
        op = request.get("op")
        role, name = self.role(uid)
        allowed = (
            (role == "root" and op in ROOT_OPS)
            or (role == "greeter" and op in GREETER_OPS)
            or (role == "card" and op in CARD_OPS)
            or (role == "identity" and op in IDENTITY_OPS | SIGNER_OPS)
        )
        if not allowed:
            raise UserError("Permission denied.")
        m, r = self.manager, request
        if op in SIGNER_OPS:
            return m.sign_for(name, pid, op, r)
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
            "account_settings": lambda: m.account_settings(name),
            "publish_settings": lambda: m.publish_settings(name, r["section"], r["value"], r.get("password")),
            "wait_card": lambda: m.wait_card(r.get("after"), r.get("wait", 30), pid),
            "switch_done": lambda: m.switch_done(r["instance"], pid),
            "check_unlock": lambda: m.check_unlock(name, r.get("password")),
            "card_login": lambda: m.card_login(r["card"], r.get("password")),
            "card_swipe": lambda: m.card_swipe(r),
        }
        return handlers[op]()

    def handle(self, connection):
        with connection:
            try:
                pid, uid = peer_credentials(connection)
                stream = connection.makefile("rwb")
                # Events to sign can be long-form articles.
                line = stream.readline(1 << 20)
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError
                response = {"ok": True, "result": self.dispatch(uid, request, pid)}
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
        elif command == "pam-unlock":
            # hyprlock's PAM stack runs this as the locked session's user.
            password = sys.stdin.buffer.read(4096).split(b"\0")[0].decode()
            request("check_unlock", password=password, timeout=150)
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
        elif command == "refresh-profiles":
            print(json.dumps(UserManager().refresh_profiles()))
        else:
            print("usage: kwak-userd serve|list|remove USER|cleanup|refresh-profiles|signout",
                  file=sys.stderr)
            return 2
    except UserError as error:
        print(f"kwak-userd: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
