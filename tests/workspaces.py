#!/usr/bin/env python3
"""Check the one-main-window policy inside the disposable kwakOS VM."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def ctl(*args):
    return subprocess.check_output(['hyprctl', *args], text=True, timeout=20)


def clients():
    return json.loads(ctl('-j', 'clients'))


def wait_for(check):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError('Timed out waiting for compositor state')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--grim', required=True)
    parser.add_argument('--zenity', required=True)
    args = parser.parse_args()
    if os.uname().nodename != 'kwakos-vm' or clients():
        raise SystemExit('Requires the disposable kwakos-vm with no open windows')
    if ctl('repl', 'return kwak_settings.mode').strip() != 'kwassik':
        raise SystemExit('Select Kwassik in Settings before testing its workspace policy')
    args.output.mkdir(parents=True, exist_ok=True)
    processes = []
    result = {}

    def app(label):
        process = subprocess.Popen([
            'kitty', '--class', 'kwak-workspace-test', '--title', label,
            '-o', 'confirm_os_window_close=0', 'sh', '-c',
            f'printf "\\n  {label}\\n\\n  One main window per workspace.\\n"; exec sleep 600',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(process)
        window = wait_for(lambda: next((c for c in clients() if c['title'] == label), None))
        wait_for(lambda: json.loads(ctl('-j', 'activeworkspace'))['id'] == window['workspace']['id'])
        return window, process

    def capture(name):
        subprocess.run([args.grim, str(args.output / name)], check=True)

    try:
        windows = [app(f'WORKSPACE {label}') for label in ('A', 'B', 'C')]
        state = clients()
        ids = [c['workspace']['id'] for c in state]
        assert len(state) == 3 and len(set(ids)) == 3, state
        assert not any(c['floating'] for c in state), state
        result['separate_workspaces'] = ids
        for window, _ in windows:
            workspace = window['workspace']['id']
            ctl('dispatch', f'hl.dsp.focus({{workspace={workspace}}})')
            active = wait_for(lambda: next((c for c in clients() if c['address'] == window['address'] and c['visible']), None))
            monitor = next(m for m in json.loads(ctl('-j', 'monitors')) if m['id'] == active['monitor'])
            assert active['size'][0] >= monitor['width'] / monitor['scale'] * 0.95, active
            assert active['size'][1] >= monitor['height'] / monitor['scale'] * 0.95, active
            capture(f'workspace-{window["title"][-1].lower()}.png')
        result['monocle_geometry'] = 'PASS: each main window fills its own workspace'

        freed = windows[1][0]['workspace']['id']
        windows[1][1].terminate()
        windows[1][1].wait(timeout=5)
        wait_for(lambda: len(clients()) == 2)
        replacement, _ = app('WORKSPACE D')
        assert replacement['workspace']['id'] == freed, replacement
        result['reuse_empty_workspace'] = 'PASS'

        dialog = subprocess.Popen([args.zenity, '--info', '--title=Workspace dialog',
                                   '--text=Floating dialogs remain on the current app workspace.'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(dialog)
        dialog_window = wait_for(lambda: next((c for c in clients() if c['title'] == 'Workspace dialog'), None))
        assert dialog_window['floating'], dialog_window
        assert dialog_window['workspace']['id'] == replacement['workspace']['id'], dialog_window
        result['floating_dialog'] = 'PASS: dialog stays on the active app workspace'
        dialog.terminate()
        dialog.wait(timeout=5)
        wait_for(lambda: len(clients()) == 3)

        launcher = subprocess.Popen(['kwak-launcher'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(launcher)
        wait_for(lambda: any(layer['namespace'] == 'wofi' and layer['alpha'] == 1 and layer['w'] > 0
                             for monitor in json.loads(ctl('-j', 'layers')).values()
                             for level in monitor['levels'].values() for layer in level))
        time.sleep(1)  # Allow GTK's first content frame after layer configuration.
        assert len({c['workspace']['id'] for c in clients()}) == 3
        capture('workspace-launcher.png')
        result['launcher_overlay'] = 'PASS: Wofi overlays the app without changing workspace ownership'
        assert all(not error.strip() for error in json.loads(ctl('-j', 'configerrors')))
        result['config_errors'] = []
        result['status'] = 'PASS'
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        (args.output / 'workspaces.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
