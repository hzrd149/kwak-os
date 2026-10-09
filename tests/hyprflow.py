#!/usr/bin/env python3
"""Exercise the packaged plugin and its real bindings in the disposable VM."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def ctl(*args):
    return subprocess.check_output(['hyprctl', *args], text=True, timeout=20).strip()


def query(command):
    return json.loads(ctl('-j', command))


def status():
    return json.loads(ctl('repl', 'return hl.plugin.hyprflow.status()'))


def wait_for(check, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.08)
    raise AssertionError('Timed out waiting for Hyprflow state')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--grim', required=True)
    parser.add_argument('--plugin', required=True)
    args = parser.parse_args()
    assert os.uname().nodename == 'kwakos-vm', 'Refusing the daily desktop'
    assert os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') and os.environ.get('WAYLAND_DISPLAY')
    assert not query('clients'), 'Requires an otherwise empty test desktop'
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'checks': [], 'plugin': args.plugin}
    processes, caches = [], []
    sequence = 0
    submap = ctl('repl', 'return hl.get_current_submap()')

    def key(name, super_key=False):
        nonlocal sequence
        sequence += 1
        request = args.output / 'key-request.json'
        temporary = request.with_suffix('.tmp')
        temporary.write_text(json.dumps({'id': sequence, 'key': name, 'super': super_key}))
        temporary.replace(request)

        def acknowledged():
            reply = args.output / 'key-reply.json'
            if not reply.exists():
                return False
            response = json.loads(reply.read_text())
            if response['id'] != sequence:
                return False
            assert 'error' not in response, response
            return True

        wait_for(acknowledged)

    def record(name):
        result['checks'].append(name)
        print('PASS:', name, flush=True)

    def settled(selected):
        def ready():
            value = status()
            return value.get('open') and not value.get('closing') and value['selected'] == selected and value['openness'] > .999
        wait_for(ready)

    def closed(workspace):
        wait_for(lambda: not status()['open'])
        assert query('activeworkspace')['id'] == workspace
        assert ctl('repl', 'return hl.get_current_submap()') == submap

    def capture(name):
        subprocess.run([args.grim, str(args.output / name)], check=True, timeout=20)

    def input_data(index):
        return (args.output / f'input-{index}.bin').read_bytes()

    try:
        plugins = json.loads(ctl('-j', 'plugin', 'list'))
        assert any(plugin['name'] == 'hyprflow' for plugin in plugins), plugins
        result['startup_plugins'] = plugins
        assert not any(error.strip() for error in query('configerrors'))
        record('automatic plugin load and clean configuration')
        for index, color in enumerate(('#183037', '#302139', '#293219'), 1):
            ctl('dispatch', f'hl.dsp.focus({{workspace={index}}})')
            cache = tempfile.TemporaryDirectory(dir=args.output)
            caches.append(cache)
            env = dict(os.environ, XDG_CACHE_HOME=cache.name)
            log = args.output / f'input-{index}.bin'
            log.write_bytes(b'')
            code = ('import os,tty; from pathlib import Path; '
                    f'print("\\n  WORKSPACE {index}\\n\\n  Hyprflow navigation proof",flush=True); '
                    f'f=open({str(log)!r},"ab",buffering=0); tty.setraw(0); '
                    f'Path({str(log)+".ready"!r}).touch(); '
                    'exec("while True:\\n f.write(os.read(0,64))")')
            process = subprocess.Popen(['kitty', '--class', 'kwak-hyprflow-test', '--title', f'FLOW {index}',
                                        '-o', f'background={color}', '-o', 'font_size=22',
                                        '-o', 'remember_window_size=no', '-o', 'confirm_os_window_close=0',
                                        sys.executable, '-c', code], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            window = wait_for(lambda: next((c for c in query('clients') if c['title'] == f'FLOW {index}'), None))
            ctl('dispatch', f'hl.dsp.window.move({{window="address:{window["address"]}",workspace={index}}})')
            wait_for(lambda: log.with_name(log.name+'.ready').exists())
        ctl('dispatch', 'hl.dsp.focus({workspace=1})')
        key('z')
        wait_for(lambda: input_data(1) == b'z')
        record('native keyboard input reaches the fixture before opening')
        key('Tab', True)
        settled(1)
        assert ctl('repl', 'return hl.get_current_submap()') == 'hyprflow'
        key('Right')
        settled(2)
        assert query('activeworkspace')['id'] == 1
        key('3')
        settled(3)
        assert query('activeworkspace')['id'] == 1
        key('a')
        assert input_data(1) == b'z' and input_data(2) == input_data(3) == b''
        capture('overview.png')
        result['overview'] = status()
        record('Super+Tab, arrows and numeric jump select without activating; unrelated input is consumed')
        key('Return')
        closed(3)
        capture('accepted.png')
        record('Enter activates the selected workspace and restores the previous submap')
        key('Tab', True)
        settled(3)
        key('Left')
        settled(2)
        key('Escape')
        closed(3)
        key('x')
        wait_for(lambda: input_data(3) == b'x')
        record('Escape cancels selection and restores client keyboard focus')
        key('Tab', True)
        settled(3)
        key('Tab', True)
        closed(3)
        record('Super+Tab also closes the overview')
        ctl('reload', 'config-only')
        wait_for(lambda: not status()['open'])
        key('Tab', True)
        settled(3)
        key('Escape')
        closed(3)
        record('config reload retains the plugin options and bindings')
        for _ in range(3):
            key('Tab', True)
            settled(3)
            assert ctl('plugin', 'unload', args.plugin) == 'ok'
            assert ctl('repl', 'return hl.get_current_submap()') == submap
            assert query('activeworkspace')['id'] == 3
            assert ctl('plugin', 'load', args.plugin) == 'ok'
            ctl('reload', 'config-only')
            wait_for(lambda: not status()['open'])
        record('three unload/reload cycles while open restore input and keep the compositor alive')
        assert not any(error.strip() for error in query('configerrors'))
        result['version'] = query('version')
        result['passed'] = True
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        for cache in caches:
            cache.cleanup()
        (args.output / 'runtime.json').write_text(json.dumps(result, indent=2)+'\n')
        (args.output / 'keyboard-done').touch()


if __name__ == '__main__':
    main()
