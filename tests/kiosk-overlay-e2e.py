#!/usr/bin/env python3
"""Exercise the real packaged game menu in an explicitly owned disposable VM.

Uses QMP for all menu input, a real trial napplet, compositor screenshots and
the local fixture relay/Blossom. Never closes the checkpoint's saved games.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time

from PIL import Image, ImageChops


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.2)
    raise AssertionError("Game menu did not reach the expected state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qmp", required=True)
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--kiosk-store", required=True)
    parser.add_argument("--fixture-directory", default="/root/kwak-fixture")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint = json.loads((args.checkpoint_dir / "game-ready.json").read_text())
    username, uid = checkpoint["user"], checkpoint["uid"]
    saved_pids = set(checkpoint["pids"])
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"UserKnownHostsFile={args.output / 'known-hosts'}", "-o", "ConnectTimeout=5",
           "-i", str(args.identity), "-p", str(args.ssh_port), "root@127.0.0.1"]

    def remote(command, binary=False):
        return subprocess.check_output([*ssh, command], text=not binary, timeout=40)

    assert remote("hostname").strip() == "kwakos-vm", "Requires disposable kwakOS VM"
    assert remote(shlex.join(["id", "-u", username])).strip() == str(uid)
    environment = dict(checkpoint["environment"])

    def user(*command):
        return remote(shlex.join(["runuser", "-u", username, "--", "env",
                                  *[f"{key}={value}" for key, value in environment.items()],
                                  *command])).strip()

    def ctl(*command):
        return user("hyprctl", *command)

    def query(kind):
        return json.loads(ctl("-j", kind))

    def focus(address):
        return ctl("eval", "hl.dispatch(hl.dsp.focus({window=" + json.dumps("address:" + address) + "}))")

    def process_start(pid):
        return user("python3", "-c",
                    "from pathlib import Path;import sys;print(Path('/proc/'+sys.argv[1]+'/stat').read_text().rsplit(')',1)[1].split()[19])",
                    str(pid))

    def close_exact(client, started):
        assert client["pid"] not in saved_pids
        live = next((window for window in query("clients") if window["pid"] == client["pid"]
                     and window["address"] == client["address"]), None)
        if live and process_start(client["pid"]) == started:
            ctl("eval", "hl.dispatch(hl.dsp.window.close({window=" + json.dumps("address:" + client["address"]) + "}))")
            wait_for(lambda: not any(window["address"] == client["address"] for window in query("clients")))

    def current_overlay():
        return next((client for client in query("clients") if client["class"] == "org.kwak.Overlay"), None)

    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(10)
    connection.connect(args.qmp)
    stream = connection.makefile("rwb", buffering=0)

    def response():
        while True:
            result = json.loads(stream.readline())
            if "event" not in result:
                return result

    def qmp(name, **arguments):
        stream.write((json.dumps({"execute": name, "arguments": arguments}) + "\n").encode())
        result = response()
        assert "error" not in result, result
        return result["return"]

    response()
    qmp("qmp_capabilities")
    assert qmp("query-name")["name"] == "kwakos-vm"

    def key(*names):
        qmp("send-key", keys=[{"type": "qcode", "data": name} for name in names], **{"hold-time": 80})
        time.sleep(.3)

    def screenshot(name, client=None):
        guest = f"/tmp/kwak-overlay-proof-{name}"
        command = ["grim"]
        if client:
            x, y = client["at"]
            width, height = client["size"]
            command.extend(["-g", f"{x},{y} {width}x{height}"])
        command.append(guest)
        user(*command)
        path = args.output / name
        path.write_bytes(remote(shlex.join(["cat", guest]), binary=True))
        return path

    def protected_games_alive():
        assert saved_pids <= {client["pid"] for client in query("clients")}, "Saved game was closed"
        for pid in saved_pids:
            remote(shlex.join(["kill", "-0", str(pid)]))

    def open_menu(client):
        focus(client["address"])
        key("meta_l")
        overlay = wait_for(current_overlay)
        wait_for(lambda: query("activewindow").get("address") == overlay["address"])
        command_line = remote(shlex.join(["python3", "-c",
            "from pathlib import Path;import sys;print(Path('/proc/'+sys.argv[1]+'/cmdline').read_bytes().replace(b'\\0',b' ').decode())",
            str(overlay["pid"])]))
        assert args.kiosk_store in command_line, "Windows key launched an older kiosk package"
        target = json.loads(user("python3", "-c",
            "from pathlib import Path;import sys;values=dict(v.split(b'=',1) for v in Path('/proc/'+sys.argv[1]+'/environ').read_bytes().split(b'\\0') if b'=' in v);print(values.get(b'KWAK_OVERLAY_TARGET',b'{}').decode())",
            str(overlay["pid"])))
        assert target.get("napplet_address") == trial_address, f"Overlay failed to identify its game: {target}"
        assert target.get("address") == client["address"] and target.get("pid") == client["pid"]
        result["target"] = target
        time.sleep(.8)
        return overlay

    def button(index):
        # Every freshly opened menu focuses Resume. These indices reflect the
        # visible dedicated menu, after a screenshot enables Publish.
        for _ in range(index):
            key("tab")
        key("ret")

    def notes():
        events = json.loads(remote(shlex.join(["cat", args.fixture_directory + "/events.json"])))
        return [event for event in events if event["kind"] == 1]

    result = {"date": datetime.now(timezone.utc).isoformat(), "checks": [],
              "kiosk_store": args.kiosk_store, "user": username,
              "saved_game_pids": sorted(saved_pids),
              "limitations": ["Virtual GPU/audio; no physical kiosk hardware certification",
                              "Publishing uses only the supplied local fixture relay and Blossom"]}

    def record(name):
        result["checks"].append(name)
        print("PASS:", name, flush=True)
        (args.output / "runtime.json").write_text(json.dumps(result, indent=2) + "\n")

    protected_games_alive()
    library = next(client for client in query("clients") if client["class"] == "org.kwak.Library")
    assert library["workspace"]["id"] == 1
    metadata = json.loads(remote(shlex.join(["cat", args.fixture_directory + "/metadata.json"])))
    trial_address = next(game["address"] for game in metadata["games"] if game["identifier"] == "kiosk-trial")
    baseline_installed = json.loads(user("kwak", "--json", "installed"))["items"]
    baseline_pids = {client["pid"] for client in query("clients")}
    baseline_notes = {note["id"] for note in notes()}
    assert trial_address not in {game["address"] for game in baseline_installed}
    trial = None
    trial_start = None
    token = None
    try:
        opened = json.loads(user("kwak", "--json", "try", trial_address))
        token = opened["window_id"]
        result["trial_window_id"] = token
        def find_trial():
            for client in query("clients"):
                if client["pid"] in baseline_pids or client["class"].startswith("org.kwak."):
                    continue
                identity = user("python3", "-c",
                    "from pathlib import Path;import sys;values=dict(v.split(b'=',1) for v in Path('/proc/'+sys.argv[1]+'/environ').read_bytes().split(b'\\0') if b'=' in v);print(values.get(b'KWAKORE_INSTANCE_ID',b'').decode())",
                    str(client["pid"]))
                if identity == token:
                    return client
            return None
        trial = wait_for(find_trial)
        trial_start = process_start(trial["pid"])
        result["trial_pid"] = trial["pid"]
        focus(trial["address"])
        record("Play Now opens a real sandboxed trial without adding an installed record")
        ctl("eval", 'hl.dispatch(hl.dsp.window.fullscreen({mode="fullscreen"}))')
        trial = wait_for(lambda: next(client for client in query("clients") if client["pid"] == trial["pid"] and client.get("fullscreen")))
        time.sleep(.8)
        screenshot("trial-before-overlay.png", trial)
        open_menu(trial)
        screenshot("game-menu-fullscreen.png")
        record("Windows key opens the dedicated menu over a fullscreen WebKit game")
        latest = str(Path("/home") / username / ".local/share/kwak/screenshots/latest.json")
        def read_latest():
            return json.loads(user("python3", "-c",
                "from pathlib import Path;import sys;path=Path(sys.argv[1]);print(path.read_text() if path.is_file() else '{}')", latest))
        previous_filename = read_latest().get("filename")
        button(2)  # Resume -> Exit Game -> Take Screenshot.
        def fresh_snapshot():
            snapshot = read_latest()
            return snapshot if snapshot and snapshot.get("filename") != previous_filename else None
        snapshot = wait_for(fresh_snapshot)
        image_path = args.output / "captured-game.png"
        image_path.write_bytes(remote(shlex.join(["cat", snapshot["path"]]), binary=True))
        before = Image.open(args.output / "trial-before-overlay.png").convert("RGB")
        after = Image.open(image_path).convert("RGB")
        assert before.size == after.size, (before.size, after.size)
        changed = ImageChops.difference(before, after).convert("L").point(lambda value: 255 if value > 12 else 0)
        fraction = sum(changed.histogram()[128:]) / (after.width * after.height)
        assert fraction < .15, f"Capture includes unexpected overlay pixels: {fraction:.2%} changed"
        result["screenshot_changed_fraction"] = fraction
        result["snapshot"] = snapshot
        record("Screenshot contains the game image and excludes the visible overlay")
        key("esc")
        wait_for(lambda: current_overlay() is None)
        open_menu(trial)
        button(3)  # Publish Screenshot; cancel is the modal's initial focus.
        screenshot("publish-confirmation.png")
        key("ret")
        assert {note["id"] for note in notes()} == baseline_notes
        record("Canceling publication sends no screenshot note")
        key("esc")
        wait_for(lambda: current_overlay() is None)
        open_menu(trial)
        button(3)
        key("tab")
        key("ret")
        published = wait_for(lambda: next((note for note in notes() if note["id"] not in baseline_notes), None), timeout=40)
        assert ["a", trial_address] in published["tags"]
        assert any(tag[0] == "imeta" for tag in published["tags"])
        result["published_event_id"] = published["id"]
        screenshot("published.png")
        record("Confirmed UI publication uploads to local Blossom and publishes the signed screenshot note")
        key("esc")
        wait_for(lambda: current_overlay() is None)
        assert query("activewindow")["address"] == trial["address"]
        record("Resume restores the exact trial game's focus")
        open_menu(trial)
        button(4)
        wait_for(lambda: current_overlay() is None)
        assert query("activeworkspace")["id"] == 1
        assert query("activewindow")["class"] == "org.kwak.Library"
        record("Back to Library focuses My Games on workspace 1")
        open_menu(trial)
        button(5)
        wait_for(lambda: json.loads(ctl("repl", "return hl.plugin.hyprflow.status()"))["open"])
        screenshot("switch-games.png")
        key("esc")
        wait_for(lambda: not json.loads(ctl("repl", "return hl.plugin.hyprflow.status()"))["open"])
        record("Switch Games opens the real Hyprflow chooser")
        open_menu(trial)
        button(1)
        wait_for(lambda: current_overlay() is None)
        wait_for(lambda: not any(client["pid"] == trial["pid"] for client in query("clients")))
        protected_games_alive()
        installed = json.loads(user("kwak", "--json", "installed"))["items"]
        assert {game["address"] for game in installed} == {game["address"] for game in baseline_installed}
        library_event = max((event for event in json.loads(remote(shlex.join(["cat", args.fixture_directory + "/events.json"])))
                             if event["kind"] == 30078), key=lambda event: (event["created_at"], event["id"]))
        assert set(json.loads(library_event["content"])["napplets"]) == set(checkpoint["addresses"])
        record("Exit Game closes only the trial and preserves the two saved running games and library")
    except Exception:
        screenshot("failure.png")
        raise
    finally:
        if current_overlay():
            key("esc")
            wait_for(lambda: current_overlay() is None)
        if json.loads(ctl("repl", "return hl.plugin.hyprflow.status()"))["open"]:
            key("esc")
        if token:
            windows = json.loads(user("kwak", "--json", "windows"))["items"]
            if any(window["window_id"] == token for window in windows):
                user("kwak", "--json", "stop", token)
            elif trial is not None and trial_start is not None:
                close_exact(trial, trial_start)
        protected_games_alive()
        connection.close()


if __name__ == "__main__":
    main()
