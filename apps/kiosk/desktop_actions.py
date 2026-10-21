"""Compositor and verified game actions shared by launcher and menu."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata


class OverlayError(RuntimeError):
    pass


class DesktopActions:
    def __init__(self, target=None, run=subprocess.run, spawn=subprocess.Popen, runtime=None):
        self.target = target if target is not None else self.environment_target()
        self.run = run
        self.spawn = spawn
        self._runtime = runtime

    @property
    def runtime(self):
        if self._runtime is None:
            from kiosk import Runtime
            self._runtime = Runtime()
        return self._runtime

    def launch_available(self):
        """Local identity gate; daemon reads never delay opening the menu."""
        return bool(self.live_target(game_only=True) and
                    self.canonical_game(self.target.get("napplet_address")) and
                    re.fullmatch(r"(?:[0-9a-f]{32}|[1-9][0-9]{0,19})",
                                 str(self.target.get("window_id", ""))))

    @staticmethod
    def environment_target():
        try:
            target = json.loads(os.environ.get("KWAK_OVERLAY_TARGET", "{}"))
            return target if isinstance(target, dict) else {}
        except ValueError:
            return {}

    def request(self, *args, json_output=False):
        try:
            result = self.run(["hyprctl", *args], capture_output=True, text=True,
                              timeout=5, check=False)
            if result.returncode:
                raise OverlayError("Desktop action unavailable. Please retry.")
            return json.loads(result.stdout) if json_output else result.stdout
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise OverlayError("Desktop action unavailable. Please retry.") from error

    @staticmethod
    def process_identity(pid):
        if not isinstance(pid, int) or pid <= 0:
            return None
        try:
            directory = Path(f"/proc/{pid}")
            if directory.stat().st_uid != os.getuid():
                return None
            executable = (directory / "exe").resolve(strict=True)
            # Start time guards against PID reuse while the menu was open.
            start = (directory / "stat").read_text().rsplit(")", 1)[1].split()[19]
            return {"exe": str(executable), "start": start}
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def canonical_game(address):
        if not isinstance(address, str) or len(address.encode()) > 4096:
            return False
        parts = address.split(":", 2)
        return (len(parts) == 3 and parts[0] == "35129" and
                bool(re.fullmatch(r"[0-9a-f]{64}", parts[1])) and
                not any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in parts[2]))

    def runtime_managed(self):
        return self.target.get("runtime_managed", bool(re.fullmatch(r"[0-9a-f]{32}", str(self.target.get("window_id", "")))))

    def capture(self):
        try:
            window = self.request("-j", "activewindow", json_output=True)
            if not isinstance(window, dict):
                return {}
            target = {key: window.get(key) for key in ("address", "pid", "class", "title")} | {
                "process": self.process_identity(window.get("pid"))}
            if target["process"] and Path(target["process"]["exe"]).name in ("kwaklet", ".kwaklet-wrapped", "napplet", ".napplet-wrapped"):
                environment = dict(entry.split(b"=", 1) for entry in
                                   Path(f"/proc/{target['pid']}/environ").read_bytes().split(b"\0") if b"=" in entry)
                token = environment.get(b"KWAKORE_INSTANCE_ID", b"").decode("utf-8")
                address = environment.get(b"KWAKORE_NAPP_ID", b"").decode("utf-8")
                if self.canonical_game(address):
                    # Some trial renderers expose an internal serial ID rather
                    # than the daemon's opaque public stop token.
                    target.update(window_id=token, napplet_address=address,
                                  runtime_managed=bool(re.fullmatch(r"[0-9a-f]{32}", token)))
            return target
        except (OverlayError, RuntimeError, OSError, ValueError, KeyError):
            return {}

    def live_target(self, game_only=False):
        target = self.target
        if not re.fullmatch(r"0x[0-9a-fA-F]+", str(target.get("address", ""))):
            return None
        identity = self.process_identity(target.get("pid"))
        if not identity or identity != target.get("process"):
            return None
        if game_only and Path(identity["exe"]).name not in ("kwaklet", ".kwaklet-wrapped", "napplet", ".napplet-wrapped"):
            return None
        clients = self.request("-j", "clients", json_output=True)
        return next((client for client in clients if client.get("address") == target["address"]
                     and client.get("pid") == target["pid"] and client.get("class") == target.get("class")), None)

    def game_available(self):
        if not self.live_target(game_only=True):
            return False
        if not self.canonical_game(self.target.get("napplet_address")):
            return False
        if not self.runtime_managed():
            return bool(re.fullmatch(r"[1-9][0-9]{0,19}", str(self.target.get("window_id", ""))))
        token = self.target.get("window_id")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            return False
        try:
            return any(game.get("window_id") == token and game.get("address") == self.target.get("napplet_address")
                       for game in self.runtime.request("windows")["items"])
        except (RuntimeError, KeyError) as error:
            raise OverlayError("Cannot reach this game. Please retry.") from error

    def focus_window(self, address):
        self.request("eval", "hl.dispatch(hl.dsp.focus({window=" + json.dumps("address:" + address, ensure_ascii=False) + "}))")

    def resume(self):
        if self.live_target():
            self.focus_window(self.target["address"])

    def exit_game(self):
        if not self.game_available():
            raise OverlayError("That game has already closed. Return to the library to choose another.")
        if not self.runtime_managed():
            # This renderer ID cannot be matched to a public RPC stop token.
            # Recheck ownership, PID start time and compositor identity, then
            # close exactly this window; never infer an RPC token from its name.
            if not self.live_target(game_only=True):
                raise OverlayError("That game has already closed.")
            self.request("eval", "hl.dispatch(hl.dsp.window.close({window=" +
                         json.dumps("address:" + self.target["address"]) + "}))")
            return
        # Opaque runtime IDs cannot be reused by a restarted daemon.
        try:
            result = self.runtime.request("stop", self.target["window_id"])
            if not result.get("closed"):
                raise OverlayError("The game could not close. Please retry.")
        except RuntimeError as error:
            raise OverlayError("The game could not close. Please retry.") from error

    def library(self):
        self.request("eval", "hl.dispatch(hl.dsp.focus({workspace=1}))")
        clients = self.request("-j", "clients", json_output=True)
        library = next((client for client in clients if client.get("class") == "org.kwak.Library"), None)
        if library is None:
            self.open_app("kwak-kiosk")
            for _ in range(20):
                time.sleep(.1)
                clients = self.request("-j", "clients", json_output=True)
                library = next((client for client in clients if client.get("class") == "org.kwak.Library"), None)
                if library:
                    break
        if library:
            self.focus_window(library["address"])

    def overlay_window(self):
        return next((client for client in self.request("-j", "clients", json_output=True)
                     if client.get("class") == "org.kwak.Overlay"), None)

    def hide_overlay(self):
        overlay = self.overlay_window()
        if overlay:
            self.request("eval", "hl.dispatch(hl.dsp.window.move({window=" +
                         json.dumps("address:" + overlay["address"]) +
                         ',workspace="special:kwak-overlay",follow=false}))')

    def show_overlay(self):
        game = self.live_target(game_only=True)
        overlay = self.overlay_window()
        if not game or not overlay:
            raise OverlayError("That game has already closed.")
        workspace = game.get("workspace", {}).get("name")
        if not workspace:
            raise OverlayError("Game workspace unavailable.")
        self.request("eval", "hl.dispatch(hl.dsp.window.move({window=" +
                     json.dumps("address:" + overlay["address"]) + ",workspace=" +
                     json.dumps(workspace) + ",follow=false}))")
        self.focus_window(overlay["address"])

    def switch_games(self):
        self.request("eval", "hl.plugin.hyprflow.toggle()")

    def open_app(self, command):
        try:
            self.spawn([command], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            raise OverlayError("This system app is unavailable. Please retry.") from error
