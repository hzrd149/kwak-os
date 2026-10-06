#!/usr/bin/env python3
"""Exercise Settings and capture all layouts in the disposable kwakOS VM.

Requires an otherwise empty desktop and explicit HYPRLAND_INSTANCE_SIGNATURE
and WAYLAND_DISPLAY. Selections and Apply go through the real GTK UI using
Hyprland keyboard events. The successful run leaves Scrolling saved for a
separate full VM reboot check; it closes only its own fixture processes.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


MODES = ("kwassik", "master", "dwindle", "scrolling")
LAYOUTS = dict(zip(MODES, ("monocle", "master", "dwindle", "scrolling")))
SETTINGS_CLASS = "org.kwak.Settings"
FIXTURE_CLASS = "kwak-settings-test"


def ctl(*args):
    return subprocess.check_output(["hyprctl", *args], text=True, timeout=20).strip()


def clients():
    return json.loads(ctl("-j", "clients"))


def wait_for(description, check, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {description}")


def current_mode():
    mode = ctl("repl", "return kwak_settings.mode")
    assert mode in MODES, f"Invalid desktop mode: {mode!r}"
    return mode


def shortcut(window, key, mods=""):
    ctl("dispatch", "hl.dsp.send_shortcut({"
        f"mods={json.dumps(mods)},key={json.dumps(key)},"
        f"window={json.dumps('address:' + window['address'])}" + "})")
    time.sleep(0.15)


def focus_window(window):
    ctl("dispatch", "hl.dsp.focus({window="
        + json.dumps("address:" + window["address"]) + "})")
    wait_for("fixture focus", lambda: json.loads(ctl("-j", "activewindow"))
             .get("address") == window["address"])


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def geometry(window):
    return {key: window[key] for key in
            ("address", "title", "workspace", "monitor", "at", "size", "floating")}


def assert_disjoint(windows):
    for index, first in enumerate(windows):
        x, y = first["at"]
        width, height = first["size"]
        for second in windows[index + 1:]:
            sx, sy = second["at"]
            sw, sh = second["size"]
            overlap_x = min(x + width, sx + sw) - max(x, sx)
            overlap_y = min(y + height, sy + sh) - max(y, sy)
            assert overlap_x <= 3 or overlap_y <= 3, (first, second)


def resolution(value):
    try:
        width, height = map(int, value.lower().split("x"))
        if width < 1 or height < 1:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Expected a positive WIDTHxHEIGHT") from None
    return width, height


def set_capture_resolution(size):
    if size is None:
        return
    width, height = size
    monitor = json.loads(ctl("-j", "monitors"))[0]
    ctl("eval", "hl.monitor({"
        f"output={json.dumps(monitor['name'])},mode={json.dumps(f'{width}x{height}@60')},"
        'position="auto",scale=1})')
    wait_for("capture resolution", lambda: any(
        m["name"] == monitor["name"] and m["width"] == width and m["height"] == height
        for m in json.loads(ctl("-j", "monitors"))))


def workspace_layout(workspace):
    return ctl("repl", "for _, workspace in ipairs(hl.get_workspaces()) do "
               f"if workspace.id == {workspace} then return workspace.tiled_layout end end")


def monitor_for(window):
    monitor = next(m for m in json.loads(ctl("-j", "monitors")) if m["id"] == window["monitor"])
    return monitor, monitor["width"] / monitor["scale"], monitor["height"] / monitor["scale"]


def verify_tiled(mode, fixtures, capture):
    windows = fixtures()
    assert len(windows) == 4 and not any(w["floating"] for w in windows), windows
    assert len({w["workspace"]["id"] for w in windows}) == 1, windows
    assert workspace_layout(windows[0]["workspace"]["id"]) == LAYOUTS[mode]
    monitor, width, height = monitor_for(windows[0])
    state = {"geometry": [geometry(w) for w in windows]}
    assert_disjoint(windows)
    if mode == "scrolling":
        assert all(w["size"][1] >= height * 0.9 for w in windows), windows
        assert all(width * 0.5 <= w["size"][0] <= width * 0.7 for w in windows), windows
        span = max(w["at"][0] + w["size"][0] for w in windows) - min(w["at"][0] for w in windows)
        assert span > width * 1.8, windows
        focus_window(windows[0])
        first_view = fixtures()
        first = first_view[0]
        assert monitor["x"] <= first["at"][0] + 3
        assert first["at"][0] + first["size"][0] <= monitor["x"] + width + 3
        capture("layout-scrolling-first.png")
        focus_window(windows[-1])
        last_view = fixtures()
        last = last_view[-1]
        assert monitor["x"] <= last["at"][0] + 3
        assert last["at"][0] + last["size"][0] <= monitor["x"] + width + 3
        assert abs(last_view[0]["at"][0] - first_view[0]["at"][0]) > width * 0.5
        state.update(horizontal_span=span, first_view=[geometry(w) for w in first_view],
                     last_view=[geometry(w) for w in last_view], focus_pans_viewport=True)
    else:
        for window in windows:
            x, y = window["at"]
            w, h = window["size"]
            assert x >= monitor["x"] - 3 and y >= monitor["y"] - 3, window
            assert x + w <= monitor["x"] + width + 3, window
            assert y + h <= monitor["y"] + height + 3, window
        assert sum(w["size"][0] * w["size"][1] for w in windows) > width * height * 0.8
        if mode == "master":
            main = [w for w in windows if w["size"][1] > height * 0.9]
            stack = [w for w in windows if w not in main]
            assert len(main) == 1 and len(stack) == 3, windows
            assert max(w["at"][0] for w in stack) - min(w["at"][0] for w in stack) <= 3
            assert all(w["size"][1] < height * 0.4 for w in stack), stack
            state["master_and_stack"] = True
        else:
            assert len({w["at"][0] for w in windows}) > 1
            assert len({w["at"][1] for w in windows}) > 1
            assert any(w["size"][1] < height * 0.9 for w in windows)
            state["nonoverlapping_splits"] = True
    capture(f"layout-{mode}.png")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--grim", required=True)
    parser.add_argument("--resolution", type=resolution,
                        help="Optional WIDTHxHEIGHT reapplied after reload; default: VM preferred mode")
    args = parser.parse_args()
    if os.uname().nodename != "kwakos-vm":
        raise SystemExit("Requires the disposable kwakos-vm; refusing another host")
    if not all(os.environ.get(key) for key in
               ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY")):
        raise SystemExit("Explicit HYPRLAND_INSTANCE_SIGNATURE and WAYLAND_DISPLAY required")
    existing = clients()
    if any(window["class"] != SETTINGS_CLASS for window in existing):
        raise SystemExit("Close existing windows before running this destructive fixture test")
    for window in existing:
        shortcut(window, "Escape")
    wait_for("empty disposable desktop", lambda: not clients())

    args.output.mkdir(parents=True, exist_ok=True)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    preference = config_home / "kwak" / "tiling-mode"
    processes = []
    fixture_processes = []
    fixture_caches = []
    addresses = []
    result = {"status": "RUNNING", "hyprland": ctl("version"), "modes": {}, "proofs": []}

    def spawn(command, log_name, env=None):
        with (args.output / log_name).open("a") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, env=env)
            log.write(f"\nStarted pid {process.pid}: {command[0]}\n")
        processes.append(process)
        return process

    def capture(name):
        path = args.output / name
        subprocess.run([args.grim, str(path)], check=True, timeout=20)
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", path
        result["proofs"].append(name)

    def verify_mode(mode):
        wait_for(f"{mode} live mode", lambda: current_mode() == mode)
        wait_for(f"{mode} saved preference", lambda: preference.exists()
                 and preference.read_text().strip() == mode)
        workspace = json.loads(ctl("-j", "activeworkspace"))["id"]
        layout = workspace_layout(workspace)
        assert layout == LAYOUTS[mode], {"mode": mode, "workspace": workspace, "layout": layout}
        return {"mode": mode, "layout": layout, "workspace": workspace,
                "saved_preference": preference.read_text().strip()}

    def choose_mode(mode, screenshot=True):
        before = current_mode()
        process = spawn(["kwak-settings"], "settings.log")
        window = wait_for("Settings window", lambda: next(
            (w for w in clients() if w["class"] == SETTINGS_CLASS), None))
        assert window["floating"], window
        focus_window(window)
        # Loading uses one bounded five-second IPC call before focusing the
        # active radio. Wait out that bound before sending selection events.
        time.sleep(5.2)
        offset = MODES.index(mode) - MODES.index(before)
        for _ in range(abs(offset)):
            shortcut(window, "Down" if offset > 0 else "Up")
        if offset:
            shortcut(window, "a", "ALT")
        state = verify_mode(mode)
        time.sleep(0.4)  # Let the idle callback paint the saved-state message.
        if screenshot:
            capture(f"settings-{mode}.png")
        shortcut(window, "Escape")
        wait_for("Settings closed", lambda: not any(
            w["class"] == SETTINGS_CLASS for w in clients()))
        process.wait(timeout=5)
        ctl("reload", "config-only")
        set_capture_resolution(args.resolution)
        state["reload"] = verify_mode(mode)
        errors = json.loads(ctl("-j", "configerrors"))
        assert not any(error.strip() for error in errors), errors
        return state

    def fixtures():
        windows = [w for w in clients() if w["address"] in addresses]
        return sorted(windows, key=lambda w: w["title"])

    def create_fixtures(mode):
        assert not clients(), clients()
        workspace = json.loads(ctl("-j", "activeworkspace"))["id"]
        for index, color in enumerate(("#192a2b", "#292238", "#30291b", "#1d2d20"), 1):
            title = f"WINDOW {index}"
            cache = tempfile.TemporaryDirectory(prefix="kitty-cache-", dir=args.output)
            fixture_caches.append(cache)
            process = spawn([
                "kitty", "--class", FIXTURE_CLASS, "--title", title,
                "-o", f"background={color}", "-o", "foreground=#e1e5db",
                "-o", "font_size=22", "-o", "window_padding_width=24",
                "-o", "remember_window_size=no",
                "-o", "confirm_os_window_close=0", "sh", "-c",
                f"stty -echo; printf '\\n  {title}\\n\\n  Layout verification\\n'; exec sleep 600",
            ], f"window-{index}.log", env=dict(os.environ, XDG_CACHE_HOME=cache.name))
            fixture_processes.append(process)
            window = wait_for(title, lambda: next(
                (w for w in clients() if w["title"] == title and w["class"] == FIXTURE_CLASS), None))
            addresses.append(window["address"])
            assert window["workspace"]["id"] == workspace, window
        wait_for("four fixture windows", lambda: len(fixtures()) == 4)
        time.sleep(0.5)
        return workspace

    def remove_fixtures():
        for process in reversed(fixture_processes):
            stop(process)
        wait_for("fixture cleanup", lambda: not fixtures())
        fixture_processes.clear()
        addresses.clear()

    try:
        set_capture_resolution(args.resolution)
        # Always make a real change before the first proof, even after a prior
        # successful run left a mode saved on this disposable VM.
        if current_mode() == "master":
            choose_mode("kwassik", screenshot=False)
        for mode in ("master", "dwindle", "scrolling"):
            result["modes"][mode] = choose_mode(mode)
            create_fixtures(mode)
            result["modes"][mode].update(verify_tiled(mode, fixtures, capture))
            if mode != "scrolling":
                remove_fixtures()

        result["modes"]["kwassik"] = choose_mode("kwassik")
        windows = fixtures()
        assert len(windows) == 4 and len({w["workspace"]["id"] for w in windows}) == 4, windows
        assert not any(w["floating"] for w in windows), windows
        kwassik_geometry = []
        for index, window in enumerate(windows, 1):
            focus_window(window)
            active = next(w for w in fixtures() if w["address"] == window["address"])
            _, width, height = monitor_for(active)
            assert active["size"][0] >= width * 0.95 and active["size"][1] >= height * 0.95, active
            assert workspace_layout(active["workspace"]["id"]) == "monocle"
            kwassik_geometry.append(geometry(active))
            capture("layout-kwassik.png" if index == 1 else f"layout-kwassik-{index}.png")
        result["modes"]["kwassik"].update(geometry=kwassik_geometry, existing_windows_separated=True)
        remove_fixtures()
        result["final"] = choose_mode("scrolling", screenshot=False)
        result["reboot_check"] = "Scrolling is saved; a full VM reboot must be verified separately"
        result["status"] = "PASS"
    except Exception as error:
        result["status"] = "FAIL"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        result["last_clients"] = [geometry(window) for window in clients()]
        for process in reversed(processes):
            stop(process)
        for cache in fixture_caches:
            cache.cleanup()
        (args.output / "settings-modes.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
