#!/usr/bin/env python3
"""Exercise Wofi on an explicitly supplied isolated Hyprland 0.56.2 session.

Keyboard events go through Hyprland. Scroll/touch events are synthetic GDK
input into the real GTK handlers; this does not certify physical hardware.
Requires cc, pkg-config, GTK3 headers, hyprctl and grim; never uses /dev/input.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
APPS = [
    ("Audio", "audio-volume-high"), ("Browser", "web-browser"),
    ("Calculator", "accessories-calculator"), ("Calendar", "x-office-calendar"),
    ("Camera", "camera-photo"), ("Files", "system-file-manager"),
    ("Help", "help-browser"), ("Mail", "mail-unread-symbolic"),
    ("Maps", "mark-location"), ("Monitor", "computer-symbolic"),
    ("Music", "multimedia-player"), ("Network", "network-workgroup"),
    ("Notes", "accessories-text-editor"), ("Photos", "image-x-generic"),
    ("Print", "printer"), ("Search", "system-search"),
    ("Settings", "preferences-system"), ("Software", "system-software-install"),
    ("Storage", "drive-harddisk"), ("Terminal", "utilities-terminal"),
    ("Text Editor", "accessories-text-editor"), ("Videos", "video-x-generic"),
    ("Weather", "weather-clear"), ("Workspace", "user-desktop"),
]


def eventually(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.08)
    raise AssertionError(f"condition timed out: {check}")


def resize_regression(env, state):
    monitors = json.loads(subprocess.check_output(["hyprctl", "-j", "monitors"], env=env, text=True))
    monitor = monitors[0]
    original = f'{monitor["width"]}x{monitor["height"]}@{monitor["refreshRate"]}'

    def resize(mode):
        code = f'hl.monitor({{output={json.dumps(monitor["name"])},mode={json.dumps(mode)},position="auto",scale=1}})'
        subprocess.run(["hyprctl", "eval", code], env=env, check=True, stdout=subprocess.DEVNULL)
        time.sleep(0.6)
        assert state()["page"] == 640
        assert {tile["width"] for tile in state()["tiles"]} == {160}
        assert len({tile["y"] for tile in state()["tiles"]}) == 3

    try:
        resize("1024x768@60")
        actual = json.loads(subprocess.check_output(["hyprctl", "-j", "monitors"], env=env, text=True))[0]
        assert actual["width"] == 1024 and actual["height"] == 768
    finally:
        resize(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-json", type=Path, required=True)
    parser.add_argument("--wofi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--icons", type=Path, default=Path("/usr/share/icons"))
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--fonts", type=Path)
    args = parser.parse_args()
    env = json.loads(args.env_json.read_text())
    runtime = Path(env["XDG_RUNTIME_DIR"])
    if str(runtime) == os.environ.get("XDG_RUNTIME_DIR") or str(runtime).startswith("/run/user/"):
        raise SystemExit("Refusing the daily desktop; provide an isolated compositor environment")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "gtk+-3.0"], text=True))
    probe = out / "input.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tests/wofi-input.c"), "-o", str(probe), *flags, "-ldl"], check=True)
    data = out / "share"
    apps = data / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    icons = data / "icons"
    if not icons.exists():
        icons.symlink_to(args.icons.resolve())
    mime = data / "mime"
    if not mime.exists():
        mime.symlink_to(Path("/usr/share/mime"))
    glycin = data / "glycin-loaders"
    if Path("/usr/share/glycin-loaders").exists() and not glycin.exists():
        glycin.symlink_to(Path("/usr/share/glycin-loaders"))
    launcher = out / "launch-fixture"
    launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "$KWAK_LAUNCH_MARKER"\n')
    launcher.chmod(0o755)
    for index, (name, icon) in enumerate(APPS):
        (apps / f"kwak-{index:02d}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\nIcon={icon}\n"
            f'Exec="{launcher}" {index}\nTerminal=false\n')
    env.update(XDG_DATA_HOME=str(data), XDG_DATA_DIRS=str(data), LD_PRELOAD=str(probe),
               KWAK_WOFI_TEST_DIR=str(out), KWAK_LAUNCH_MARKER=str(out / "launched"))
    control_env = {k: v for k, v in env.items() if k != "LD_PRELOAD"}
    if args.fonts:
        fontconfig = out / "fonts.conf"
        import xml.sax.saxutils
        fonts_path = xml.sax.saxutils.escape(str(args.fonts.resolve()))
        cache_path = xml.sax.saxutils.escape(str(out / "font-cache"))
        fontconfig.write_text(f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">'
                              f'<fontconfig><dir>{fonts_path}</dir><cachedir>{cache_path}</cachedir></fontconfig>')
        env["FONTCONFIG_FILE"] = str(fontconfig)
    command_id = 0
    results = {}
    processes = []

    def state():
        return json.loads((out / "state.json").read_text())

    def selected():
        return next((t["index"] for t in state()["tiles"] if t["selected"]), None)

    def key(name, mods=""):
        code = f"hl.dsp.send_shortcut({{mods={json.dumps(mods)},key={json.dumps(name)}}})"
        subprocess.run(["hyprctl", "dispatch", code], env=control_env, check=True, stdout=subprocess.DEVNULL)
        time.sleep(0.12)

    def event(command, wait=True):
        nonlocal command_id
        command_id += 1
        temp = out / "command.tmp"
        temp.write_text(f"{command_id} {command}\n")
        temp.replace(out / "command")
        if wait:
            eventually(lambda: state()["serial"] == command_id)
            time.sleep(0.08)

    def screenshot(name):
        subprocess.run(["grim", str(out / name)], env=control_env, check=True)

    def launch():
        nonlocal command_id
        command_id = 0
        for name in ("state.json", "command", "launched"):
            (out / name).unlink(missing_ok=True)
        log = (out / "wofi.log").open("a")
        process = subprocess.Popen([str(args.wofi.resolve()), "--conf", str(ROOT / "config/wofi/config"),
                                    "--style", str(ROOT / "config/wofi/style.css"),
                                    "--cache-file", str(out / "cache")], env=env, stdout=log, stderr=log, start_new_session=True)
        log.close()
        processes.append(process)
        eventually(lambda: len(state()["tiles"]) == len(APPS))
        time.sleep(0.5)
        return process

    try:
        proc = launch()
        initial = state()
        tiles = initial["tiles"]
        assert initial["page"] == 640
        assert {t["width"] for t in tiles} == {160}
        assert len({t["y"] for t in tiles}) == 3
        assert len([t for t in tiles if t["x"] < initial["page"]]) == 12
        assert initial["upper"] > initial["page"]
        assert initial["vertical_upper"] == initial["vertical_page"]
        results["geometry"] = "PASS: four columns, three rows, twelve visible tiles; horizontal overflow only"
        resize_regression(control_env, state)
        results["resize"] = "PASS: live output resize and restore preserve 160px tiles and the four-by-three grid"
        results["measurements"] = {"tile_width": tiles[0]["width"], "tile_height": tiles[0]["height"],
                                   "viewport_width": initial["page"], "content_width": initial["upper"]}
        screenshot("launcher-first.png")
        key("Right")
        eventually(lambda: selected() == 3)
        key("Down")
        eventually(lambda: selected() == 4)
        key("Left")
        eventually(lambda: selected() == 1)
        key("Up")
        eventually(lambda: selected() == 0)
        results["arrows"] = "PASS: Right 0->3, Down 3->4, Left 4->1, Up 1->0"
        key("Tab")
        eventually(lambda: selected() == 3)
        key("Tab", "SHIFT")
        eventually(lambda: selected() == 0)
        results["tab"] = "PASS: Tab advances and Shift+Tab returns to the previous tile"
        key("Next")
        eventually(lambda: selected() == 12 and state()["horizontal"] > 0)
        screenshot("launcher-page-next.png")
        key("Prior")
        eventually(lambda: selected() == 0 and state()["horizontal"] == 0)
        results["paging"] = "PASS: PageDown advances four columns and scrolls; PageUp returns"
        event("WHEEL 0 0")
        eventually(lambda: state()["horizontal"] > 0)
        results["wheel"] = "PASS: synthetic GDK wheel down scrolls horizontally"
        results["measurements"]["wheel_scroll_x"] = state()["horizontal"]
        before = state()["horizontal"]
        event("SMOOTH 2 0")
        eventually(lambda: state()["horizontal"] > before)
        results["trackpad"] = "PASS: synthetic GDK smooth horizontal delta scrolls"
        results["measurements"]["smooth_scroll_x"] = {"before": before, "after": state()["horizontal"]}
        before = state()["horizontal"]
        event("TOUCH_BEGIN 500 160")
        for x in (475, 440, 400, 350, 300, 240):
            event(f"TOUCH_UPDATE {x} 160")
        event("TOUCH_END 220 160")
        eventually(lambda: state()["horizontal"] > before)
        results["touch"] = "PASS: synthetic GDK touch sequence invokes GTK kinetic horizontal pan"
        results["measurements"]["touch_scroll_x"] = {"before": before, "after": state()["horizontal"]}
        key("Escape")
        proc.wait(timeout=5)
        results["escape"] = "PASS: launcher closes"
        time.sleep(0.3)
        screenshot("desktop.png")
        proc = launch()
        for char in "zznomatch":
            key(char)
        eventually(lambda: not any(t["visible"] for t in state()["tiles"]))
        key("a", "CTRL")
        key("BackSpace")
        eventually(lambda: sum(t["visible"] for t in state()["tiles"]) == len(APPS))
        results["empty_search"] = "PASS: zero matches, then Ctrl+A/Backspace restores the full catalog"
        for char in "terminal":
            key(char)
        eventually(lambda: len([t for t in state()["tiles"] if t["visible"]]) == 1)
        screenshot("launcher-search.png")
        results["search"] = "PASS: typed keyboard search leaves one matching application"
        key("Return")
        proc.wait(timeout=5)
        eventually(lambda: (out / "launched").exists())
        assert (out / "launched").read_text().strip() == "19"
        results["launch"] = "PASS: Enter launches only the fixture Terminal desktop entry"
        proc = launch()
        event("TOUCH_BEGIN 80 60")
        event("TOUCH_END 80 60", wait=False)
        proc.wait(timeout=5)
        eventually(lambda: (out / "launched").exists())
        assert (out / "launched").read_text().strip() == "0"
        results["tap"] = "PASS: synthetic GDK touch tap launches Audio; the earlier pan did not launch"
        if args.keep_open:
            proc = launch()
            results["fixture_pid"] = proc.pid
    finally:
        (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        for process in processes:
            if process.poll() is None and not (args.keep_open and "launch" in results):
                process.terminate()
                process.wait(timeout=5)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
