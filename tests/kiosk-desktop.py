#!/usr/bin/env python3
"""Check kiosk bindings through QMP in an explicitly supplied disposable VM.

The guest must autologin as kwak and expose SSH using a test-only identity.
Never point this harness at a desktop that contains user applications.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time
from PIL import Image


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.15)
    raise AssertionError("Desktop did not reach the expected state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qmp", required=True)
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", default="kwak-settings", help="Optional updated guest-visible Settings executable")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"UserKnownHostsFile={args.output / 'known-hosts'}", "-o", "ConnectTimeout=5",
           "-i", str(args.identity), "-p", str(args.ssh_port), "root@127.0.0.1"]

    def remote(command, binary=False):
        return subprocess.check_output([*ssh, command], text=not binary, timeout=30)

    assert remote("hostname").strip() == "kwakos-vm", "Requires disposable kwakOS VM"
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(10)
    connection.connect(args.qmp)
    stream = connection.makefile("rwb", buffering=0)

    def response():
        while True:
            value = json.loads(stream.readline())
            if "event" not in value:
                return value

    def qmp(name, **arguments):
        stream.write((json.dumps({"execute": name, "arguments": arguments}) + "\n").encode())
        result = response()
        assert "error" not in result, result
        return result["return"]

    response()
    qmp("qmp_capabilities")
    assert qmp("query-name")["name"] == "kwakos-vm"
    runtime = "/run/user/" + remote("id -u kwak").strip()
    instances = json.loads(remote(shlex.join(["runuser", "-u", "kwak", "--", "env", "XDG_RUNTIME_DIR=" + runtime, "hyprctl", "instances", "-j"])))
    assert len(instances) == 1, instances
    environment = dict(XDG_RUNTIME_DIR=runtime,
                       HYPRLAND_INSTANCE_SIGNATURE=instances[0]["instance"],
                       WAYLAND_DISPLAY=instances[0]["wl_socket"])

    def user(*command):
        return remote(shlex.join(["runuser", "-u", "kwak", "--", "env",
                      *[f"{key}={value}" for key, value in environment.items()], *command])).strip()

    def ctl(*command):
        return user("hyprctl", *command)

    def query(command):
        return json.loads(ctl("-j", command))

    def key(*names):
        qmp("send-key", keys=[{"type": "qcode", "data": name} for name in names], **{"hold-time": 80})
        time.sleep(.3)

    def launch(command):
        ctl("eval", "hl.dispatch(hl.dsp.exec_cmd(" + json.dumps(command) + "))")

    def capture(name):
        user("grim", "/tmp/kwak-kiosk-proof.png")
        (args.output / name).write_bytes(remote("cat /tmp/kwak-kiosk-proof.png", binary=True))

    result = {"date": datetime.now(timezone.utc).date().isoformat(), "checks": [],
              "environment": "disposable admin session; real packaged applications",
              "system": remote("readlink -f /run/current-system").strip(),
              "overlay": remote("readlink -f /run/current-system/sw/bin/kwak-overlay").strip(),
              "settings": args.settings,
              "config_sha256": remote("sha256sum /etc/xdg/hypr/hyprland.lua").split()[0],
              "limitations": ["Game window is a terminal fixture, not a WebKit game",
                              "Admin session has no Nostr library; account error in overlay is expected",
                              "Virtual GPU and keyboard input do not certify physical hardware"]}

    def record(name):
        result["checks"].append(name)
        print("PASS:", name, flush=True)

    assert not query("clients"), "Requires an otherwise empty disposable desktop"
    assert not any(error.strip() for error in query("configerrors"))
    assert any(plugin["name"] == "hyprflow" for plugin in json.loads(ctl("-j", "plugin", "list")))
    record("clean configuration and automatic Hyprflow load")
    launch("kitty --class kwak-kiosk-proof --title KIOSK-PROOF -o background='#203040' -o confirm_os_window_close=0 sh -c 'printf \"Game window fixture\\n\"; sleep 600'")
    fixture = wait_for(lambda: next((window for window in query("clients") if window["class"] == "kwak-kiosk-proof"), None))
    key("meta_l")
    overlay = wait_for(lambda: next((window for window in query("clients") if window["class"] == "org.kwak.Overlay"), None))
    assert overlay["floating"] and overlay["workspace"]["id"] == fixture["workspace"]["id"]
    assert query("activewindow")["address"] == overlay["address"]
    time.sleep(1)
    capture("overlay.png")
    record("lone Windows key opens floating overlay on the game's workspace")
    pixel = Image.open(args.output / "overlay.png").convert("RGB").getpixel((overlay["at"][0] + 10, overlay["at"][1] + 10))
    assert all(0 < value < original for value, original in zip(pixel, (32, 48, 64))), pixel
    record("overlay background blends with the underlying game window")
    key("esc")
    wait_for(lambda: not any(window["class"] == "org.kwak.Overlay" for window in query("clients")))
    assert query("activewindow")["address"] == fixture["address"]
    record("Escape dismisses overlay and restores game focus")
    key("meta_l", "q")
    terminal = wait_for(lambda: next((window for window in query("clients") if window["class"] == "kitty"), None))
    assert not any(window["class"] == "org.kwak.Overlay" for window in query("clients"))
    record("Super+Q opens terminal without triggering overlay on release")
    key("meta_l", "tab")
    wait_for(lambda: json.loads(ctl("repl", "return hl.plugin.hyprflow.status()"))["open"])
    key("left")
    capture("hyprflow.png")
    key("ret")
    wait_for(lambda: not json.loads(ctl("repl", "return hl.plugin.hyprflow.status()"))["open"])
    assert query("activeworkspace")["id"] == fixture["workspace"]["id"]
    record("Super+Tab, Left and Enter switch back to the game through Hyprflow")
    launch(shlex.quote(args.settings))
    settings = wait_for(lambda: next((window for window in query("clients") if window["class"] == "org.kwak.Settings"), None))
    time.sleep(1)
    capture("settings.png")
    key("shift", "tab")
    key("left")
    capture("network.png")
    key("left")
    capture("sound.png")
    key("esc")
    wait_for(lambda: not any(window["class"] == "org.kwak.Settings" for window in query("clients")))
    record("packaged Settings opens and closes with keyboard")
    for window in (fixture, terminal):
        user("kill", "-TERM", str(window["pid"]))
    wait_for(lambda: not query("clients"))
    (args.output / "runtime.json").write_text(json.dumps(result, indent=2) + "\n")
    connection.close()


if __name__ == "__main__":
    main()
