"""Capture real Hyprlax frames and measure workspace movement in isolated Sway.

Requires Pillow, grim, swaymsg, and a running headless Sway session with
XDG_RUNTIME_DIR=/tmp/kwak-wallpaper-runtime. Never run on a daily desktop.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import socket
import socketserver
import struct
import tempfile
import threading
import time

from PIL import Image, ImageChops, ImageStat


def run(*command):
    return subprocess.check_output(command, text=True).strip()


def compact_sway_proxy(path):
    """Forward real Sway IPC; normalize only JSON whitespace for Hyprlax 2.2.7.

    Its Sway adapter searches for the literal '"change":"focus"', while
    Sway 1.9 emits spaces. No events, fields, or values are fabricated.
    """
    upstream_path = os.environ["SWAYSOCK"]

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            with socket.socket(socket.AF_UNIX) as upstream:
                upstream.connect(upstream_path)

                def requests():
                    try:
                        while data := self.request.recv(65536):
                            upstream.sendall(data)
                    except OSError:
                        pass
                    finally:
                        upstream.shutdown(socket.SHUT_WR)

                threading.Thread(target=requests, daemon=True).start()
                stream = upstream.makefile("rb")
                try:
                    while header := stream.read(14):
                        magic, length, kind = struct.unpack("<6sII", header)
                        assert magic == b"i3-ipc"
                        payload = json.dumps(json.loads(stream.read(length)), separators=(",", ":")).encode()
                        self.request.sendall(struct.pack("<6sII", magic, len(payload), kind) + payload)
                except (OSError, ValueError):
                    pass
                finally:
                    stream.close()

    server = socketserver.ThreadingUnixStreamServer(path, Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hyprlax", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("XDG_RUNTIME_DIR") == "/tmp/kwak-wallpaper-runtime"
    outputs = json.loads(run("swaymsg", "-t", "get_outputs", "-r"))
    assert len(outputs) == 1 and outputs[0]["name"] == "HEADLESS-1"
    args.output.mkdir(parents=True, exist_ok=True)

    def ctl(*command):
        return run(args.hyprlax, "ctl", *map(str, command))

    def workspace(number):
        run("swaymsg", "workspace", str(number))

    def capture(name):
        path = args.output / name
        run("grim", "-o", "HEADLESS-1", str(path))
        return path

    workspace(1)
    with tempfile.TemporaryDirectory(dir=os.environ["XDG_RUNTIME_DIR"]) as proxy_dir, (args.output / "hyprlax.log").open("w") as log:
        proxy_path = str(Path(proxy_dir) / "sway.sock")
        proxy = compact_sway_proxy(proxy_path)
        daemon = subprocess.Popen(
            [args.hyprlax, "--config", str(args.config), "--debug"],
            stdout=log, stderr=log,
            env={**os.environ, "SWAYSOCK": proxy_path},
        )
        try:
            for _ in range(100):
                assert daemon.poll() is None, "Hyprlax exited; inspect hyprlax.log"
                try:
                    layers = json.loads(ctl("list", "--json"))
                    if len(layers) == 5:
                        break
                except (subprocess.CalledProcessError, json.JSONDecodeError):
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("Five layers did not load")
            time.sleep(5)
            capture("wallpaper.png")
            status = json.loads(ctl("status", "--json"))

            # Actual compositor frames, including both four-second transitions.
            frames = args.output / "frames"
            frames.mkdir(exist_ok=True)
            timestamps = []
            start = time.monotonic()
            for index in range(60):
                time.sleep(max(0, start + index * 0.2 - time.monotonic()))
                if index == 5:
                    workspace(2)
                if index == 35:
                    workspace(1)
                path = capture(f"frames/{index:03d}.png")
                timestamps.append((path.name, time.monotonic() - start))
            (frames / "timing.json").write_text(json.dumps(timestamps, indent=2) + "\n")

            # Isolate each actual layer using IPC visibility, preserving its speed.
            measured = {}
            for layer in layers:
                for other in layers:
                    ctl("modify", other["id"], "visible", str(other["id"] == layer["id"]).lower())
                name = Path(layer["path"]).name
                workspace(1)
                time.sleep(4.5)
                before = capture(f"layer-{name}-ws1.png")
                workspace(2)
                time.sleep(4.5)
                after = capture(f"layer-{name}-ws2.png")
                a, b = Image.open(before).convert("RGB"), Image.open(after).convert("RGB")
                width, height = a.size
                # Compare interior pixels over candidate translations, excluding wrap edges.
                margin = width // 8
                reference = a.crop((margin, 0, width - margin, height))
                errors = {}
                for dx in range(-width // 10, width // 10 + 1):
                    shifted = b.crop((margin + dx, 0, width - margin + dx, height))
                    errors[dx] = sum(ImageStat.Stat(ImageChops.difference(reference, shifted)).mean)
                shift = min(errors, key=errors.get)
                identical = a.tobytes() == b.tobytes()
                if name in ("0.png", "4.png"):
                    assert identical and shift == 0, f"Static layer moved: {name}"
                else:
                    assert not identical and abs(shift) > 0, f"City layer did not move: {name}"
                measured[name] = {
                    "measured_shift_px": shift,
                    "identical_frames": identical,
                    "registration_error_rgb": errors[shift],
                    "before_sha256": hashlib.sha256(before.read_bytes()).hexdigest(),
                    "after_sha256": hashlib.sha256(after.read_bytes()).hexdigest(),
                }
            assert abs(measured["1.png"]["measured_shift_px"]) > abs(measured["2.png"]["measured_shift_px"]) > abs(measured["3.png"]["measured_shift_px"]) > 0
            result = {
                "runtime": "Unmodified hyprlax v2.2.7 source, CI=1 host build; isolated headless Sway with software rendering",
                "ipc_adapter": "Real Sway IPC forwarded with JSON whitespace compacted for Hyprlax's literal string parser; no event values changed",
                "compositor_version": run("sway", "--version"),
                "hyprlax_version": run(args.hyprlax, "--version"),
                "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                "binary_sha256": hashlib.sha256(Path(args.hyprlax).read_bytes()).hexdigest(),
                "status": status,
                "layers": layers,
                "measurements": measured,
                "result": "PASS: foreground/sky identical; city movement 1 > 2 > 3 > 0",
            }
            (args.output / "wallpaper-results.json").write_text(json.dumps(result, indent=2) + "\n")
            print(result["result"])
        finally:
            daemon.terminate()
            daemon.wait(timeout=10)
            proxy.shutdown()
            proxy.server_close()


if __name__ == "__main__":
    main()
