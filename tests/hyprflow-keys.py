#!/usr/bin/env python3
"""Host-side QMP keyboard bridge for tests/hyprflow.py in an owned kwakOS VM."""
import argparse
import json
from pathlib import Path
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qmp', required=True)
    parser.add_argument('--shared-output', type=Path, required=True)
    args = parser.parse_args()
    args.shared_output.mkdir(parents=True, exist_ok=True)
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(5)
    connection.connect(args.qmp)
    stream = connection.makefile('rwb', buffering=0)

    def response():
        while True:
            line = stream.readline()
            if not line:
                raise RuntimeError('QMP disconnected')
            value = json.loads(line)
            if 'event' not in value:
                return value

    def command(name, arguments=None):
        stream.write((json.dumps({'execute': name, 'arguments': arguments or {}})+'\n').encode())
        result = response()
        assert 'error' not in result, result
        return result['return']

    response()
    command('qmp_capabilities')
    assert command('query-name')['name'] == 'kwakos-vm', 'Refusing another VM'
    names = {'Tab': 'tab', 'Left': 'left', 'Right': 'right', 'Return': 'ret', 'Escape': 'esc',
             '3': '3', 'a': 'a', 'x': 'x', 'z': 'z'}
    previous = 0
    deadline = time.monotonic() + 300
    while not (args.shared_output / 'keyboard-done').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('Guest test did not finish within five minutes')
        request = args.shared_output / 'key-request.json'
        if request.exists():
            value = json.loads(request.read_text())
            if value['id'] > previous:
                keys = [{'type': 'qcode', 'data': 'meta_l'}] if value['super'] else []
                keys.append({'type': 'qcode', 'data': names[value['key']]})
                command('send-key', {'keys': keys, 'hold-time': 80})
                time.sleep(.2)  # QMP acknowledges before the scheduled key release.
                previous = value['id']
                reply = args.shared_output / 'key-reply.json'
                temporary = reply.with_suffix('.tmp')
                temporary.write_text(json.dumps({'id': previous}))
                temporary.replace(reply)
        time.sleep(.03)
    connection.close()
    print(f'PASS: {previous} keyboard actions delivered to the kwakOS VM')


if __name__ == '__main__':
    main()
