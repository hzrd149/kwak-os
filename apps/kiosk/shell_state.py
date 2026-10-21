"""Per-device launcher activity: recent play, favourites and collections.

kwakore has no play history or user collections, so the launcher keeps a
small local record. It never stores game data, only addresses and times, and
is lost with temporary kiosk accounts.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

MAX_COLLECTIONS = 32
MAX_NAME = 40


def activity_path():
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return state / "kwak/kiosk/activity.json"


def clean_name(name):
    name = " ".join(str(name).split())[:MAX_NAME]
    return "".join(char for char in name if char.isprintable())


class Activity:
    def __init__(self, path=None, clock=time.time):
        """`path=False` keeps activity in memory only, for tests and previews."""
        self.path = None if path is False else Path(path) if path is not None else activity_path()
        self.clock = clock
        self.lock = threading.Lock()
        self.games = {}
        self.collections = {}
        self.load()

    def load(self):
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        games = data.get("games")
        if isinstance(games, dict):
            for address, entry in games.items():
                if isinstance(address, str) and isinstance(entry, dict):
                    self.games[address] = {
                        key: entry[key] for key in ("last_played", "launches", "first_seen")
                        if isinstance(entry.get(key), (int, float)) and not isinstance(entry.get(key), bool)
                    } | ({"favorite": True} if entry.get("favorite") is True else {})
        collections = data.get("collections")
        if isinstance(collections, dict):
            for name, addresses in list(collections.items())[:MAX_COLLECTIONS]:
                name = clean_name(name)
                if name and isinstance(addresses, list):
                    self.collections[name] = [address for address in addresses if isinstance(address, str)]

    def save(self):
        """Atomically replace the file; called from worker threads."""
        if self.path is None:
            return
        with self.lock:
            payload = json.dumps({"games": self.games, "collections": self.collections}, sort_keys=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent,
                                                 prefix=".activity-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def entry(self, address):
        return self.games.setdefault(address, {})

    def record_launch(self, address):
        entry = self.entry(address)
        entry["last_played"] = self.clock()
        entry["launches"] = int(entry.get("launches", 0)) + 1

    def touch(self, address):
        """Played now, without counting a launch (it may have been counted)."""
        self.entry(address)["last_played"] = self.clock()

    def seen(self, addresses):
        """Mark first appearance in the installed list; returns True if changed."""
        changed = False
        now = self.clock()  # One batch shares a time, keeping name order.
        for address in addresses:
            entry = self.entry(address)
            if "first_seen" not in entry:
                entry["first_seen"] = now
                changed = True
        return changed

    def last_played(self, address):
        return self.games.get(address, {}).get("last_played", 0)

    def first_seen(self, address):
        return self.games.get(address, {}).get("first_seen", 0)

    def favorite(self, address):
        return self.games.get(address, {}).get("favorite", False)

    def toggle_favorite(self, address):
        entry = self.entry(address)
        if entry.get("favorite"):
            entry.pop("favorite")
        else:
            entry["favorite"] = True
        return entry.get("favorite", False)

    def recent(self, limit=10):
        played = [(entry["last_played"], address) for address, entry in self.games.items() if entry.get("last_played")]
        return [address for _, address in sorted(played, reverse=True)[:limit]]

    def favorites(self):
        return [address for address, entry in self.games.items() if entry.get("favorite")]

    def add_to_collection(self, name, address):
        name = clean_name(name)
        if not name or (name not in self.collections and len(self.collections) >= MAX_COLLECTIONS):
            return False
        members = self.collections.setdefault(name, [])
        if address not in members:
            members.append(address)
        return True

    def remove_from_collection(self, name, address):
        members = self.collections.get(name)
        if members is None:
            return
        if address in members:
            members.remove(address)
        if not members:
            del self.collections[name]

    def collections_for(self, address):
        return [name for name, members in self.collections.items() if address in members]
