"""Read-only projection of kwakore's native installed-list sync.

Kwakore owns persistence, relay publication, reconciliation and retry. This
adapter never writes a second library file or publishes a Nostr library event.
"""
import threading


class Library:
    native = True

    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = threading.Lock()
        self.current = {"addresses": [], "loaded": False, "pending": [], "error": "", "revision": 0}

    def snapshot(self):
        return {**self.current, "addresses": list(self.current["addresses"])}

    def sync(self):
        if not self.lock.acquire(blocking=False):
            return self.snapshot()
        try:
            try:
                games = self.runtime.listing("installed")
                diagnostics = self.runtime.request("diagnostics")
                status = diagnostics.get("install_sync", {})
                result = status.get("last_result")
                error = {
                    "failed": "Library sync will retry in kwakore. See Settings.",
                    "unreadable": "kwakore could not read the synced library. See Settings.",
                    "skipped": "Sign in to sync your library.",
                }.get(result, "")
                self.current = {"addresses": [game["address"] for game in games],
                                "loaded": bool(games) or not status.get("enabled") or result == "ok",
                                "pending": [], "error": error,
                                "install_sync": status, "revision": self.current["revision"] + 1}
            except RuntimeError as error:
                self.current = {**self.current, "error": str(error), "revision": self.current["revision"] + 1}
            return self.snapshot()
        finally:
            self.lock.release()

    def change(self, address, action):
        if action not in ("add", "remove"):
            raise RuntimeError("Invalid library action.")
        self.runtime.request("install", address) if action == "add" else self.runtime.request("uninstall", "--yes", address)
        return self.sync()
