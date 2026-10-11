"""Per-user cached library and durable, idempotent sync intents shared by TUIs."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import socket
import tempfile
import uuid


def account_request(op, **fields):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(180)
            connection.connect("/run/kwak-userd.sock")
            stream = connection.makefile("rwb")
            stream.write(json.dumps({"op": op, **fields}).encode() + b"\n")
            stream.flush()
            response = json.loads(stream.readline(1 << 22))
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "Account service unavailable.")
        return response["result"]
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError("Cannot reach your account. Library changes will retry.") from error


class Library:
    def __init__(self, root=None, request=account_request):
        self.root = Path(root or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kwak/library")
        self.request = request

    @contextlib.contextmanager
    def state(self):
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        with open(self.root / "state.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / "state.json"
            try:
                state = json.loads(path.read_text())
                if not isinstance(state, dict) or not isinstance(state.get("base"), list) or not isinstance(state.get("pending"), list):
                    raise ValueError()
            except FileNotFoundError:
                state = {"base": [], "pending": [], "loaded": False, "name": "", "error": ""}
            except (ValueError, OSError) as error:
                raise RuntimeError("Cannot read your cached library; it was not overwritten.") from error
            before = json.dumps(state, sort_keys=True)
            yield state
            if before != json.dumps(state, sort_keys=True):
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", dir=self.root, delete=False) as stream:
                        temporary = Path(stream.name)
                        json.dump(state, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)

    @staticmethod
    def desired(state):
        addresses = set(state["base"])
        for item in state["pending"]:
            if item["action"] == "add":
                addresses.add(item["address"])
            else:
                addresses.discard(item["address"])
        return sorted(addresses)

    def snapshot(self):
        with self.state() as state:
            return {**state, "addresses": self.desired(state)}

    def change(self, address, action):
        if action not in ("add", "remove"):
            raise RuntimeError("Invalid library action.")
        with self.state() as state:
            if len(state["pending"]) >= 500:
                raise RuntimeError("Too many changes waiting to sync. Retry sync first.")
            state["pending"].append({"id": uuid.uuid4().hex, "address": address, "action": action})
        return self.snapshot()

    def sync(self):
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        with open(self.root / "sync.lock", "a") as lock:
            # Another overlay/library already owns the retry; never pile up waiters.
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.snapshot()
            try:
                remote = self.request("napplet_library")
                with self.state() as state:
                    state.update(base=remote["addresses"], loaded=True, name=remote.get("name", ""), error="")
                # Bound each pass. New user actions are picked up by the next pass.
                for intent in self.snapshot()["pending"]:
                    remote = self.request("update_napplet_library", address=intent["address"], action=intent["action"])
                    with self.state() as state:
                        state["base"] = remote["addresses"]
                        state["pending"] = [item for item in state["pending"] if item["id"] != intent["id"]]
                        state["error"] = ""
            except RuntimeError as error:
                with self.state() as state:
                    state["error"] = str(error)
            return self.snapshot()
