#!/usr/bin/env python3
"""Smoke-test the packaged home screen in a disposable headless Wayland session.

Requires sway, grim, and weston-terminal; never connects to the user's desktop.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def wait_for(check):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for desktop state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    parser.add_argument("--sway", required=True)
    parser.add_argument("--grim", required=True)
    parser.add_argument("--terminal", required=True)
    args = parser.parse_args()
    processes = []
    with tempfile.TemporaryDirectory(prefix="kh-", dir="/tmp") as directory:
        temp = Path(directory)
        runtime = temp / "rt"
        runtime.mkdir(mode=0o700)
        config = temp / "sway.conf"
        config.write_text("xwayland disable\noutput HEADLESS-1 mode 1280x800\n")
        applications = temp / "data/applications"
        applications.mkdir(parents=True)
        icon = temp / "icon.svg"
        icon.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
                        '<rect width="64" height="64" fill="#b8c4b8"/></svg>')
        for index in range(18):
            (applications / f"test-{index}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=App {index:02d}\n"
                f"Exec=true\nIcon={icon}\n")
        env = os.environ.copy()
        env.update(XDG_RUNTIME_DIR=str(runtime), WLR_BACKENDS="headless",
                   WLR_RENDERER="pixman", WLR_HEADLESS_OUTPUTS="1",
                   QT_QUICK_BACKEND="software", QT_QPA_PLATFORM="wayland",
                   XDG_DATA_HOME=str(temp / "data"), XDG_DATA_DIRS=str(temp / "data"),
                   XDG_CONFIG_HOME=str(temp / "config"))
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("SWAYSOCK", None)
        swaymsg = str(Path(args.sway).with_name("swaymsg"))

        def spawn(command, name):
            log = (temp / name).open("w")
            process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
            log.close()
            processes.append(process)
            return process

        def ctl(*command):
            return subprocess.check_output([swaymsg, *command], env=env, text=True)

        def views():
            def walk(node):
                result = [node] if node.get("app_id") else []
                for child in node.get("nodes", []) + node.get("floating_nodes", []):
                    result += walk(child)
                return result
            return walk(json.loads(ctl("-t", "get_tree")))

        def capture(name):
            path = temp / name
            subprocess.run([args.grim, "-t", "ppm", str(path)], env=env, check=True)
            return path.read_bytes()

        try:
            spawn([args.sway, "-c", str(config)], "sway.log")
            socket = wait_for(lambda: next((p for p in runtime.glob("wayland-*")
                                           if not p.name.endswith(".lock")), None))
            env["WAYLAND_DISPLAY"] = socket.name
            env["SWAYSOCK"] = str(wait_for(lambda: next(runtime.glob("sway-ipc.*.sock"), None)))
            home = spawn([args.home], "home.log")
            wait_for(lambda: "Configuration Loaded" in (temp / "home.log").read_text())
            time.sleep(1)
            assert home.poll() is None
            assert not views(), "Home must not become an application window"
            desktop = capture("desktop.png")
            app = spawn([args.terminal], "terminal.log")
            wait_for(views)
            time.sleep(1)
            assert capture("app.png") != desktop, "App should cover the desktop"
            app.terminate()
            app.wait(timeout=5)
            wait_for(lambda: not views())
            time.sleep(1)
            assert capture("returned.png") == desktop, "Closing app should reveal the same grid"
            ctl("output", "HEADLESS-1", "mode", "480x320")
            time.sleep(1)
            assert home.poll() is None, "Home must survive a small output"
            ctl("create_output")
            time.sleep(1)
            assert len(json.loads(ctl("-t", "get_outputs"))) == 2
            assert home.poll() is None, "Home must survive monitor hotplug"
            log = (temp / "home.log").read_text()
            assert "ERROR" not in log and "TypeError" not in log and "ReferenceError" not in log, log
            print("PASS: loads, stays below apps, returns after close, resizes, and handles monitor hotplug")
        except Exception:
            for name in ("sway.log", "home.log", "terminal.log"):
                if (temp / name).exists():
                    print((temp / name).read_text())
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()
