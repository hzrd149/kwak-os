#!/usr/bin/env python3
"""Real greetd/PAM, library, WebKit and account lifecycle in an owned test VM.

Requires the napplet-fixture prepared with --saved. This fixture identity is
public test material; never use the harness on a desktop with real accounts.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time


def wait_for(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
        except json.JSONDecodeError:
            value = None
        if value:
            return value
        time.sleep(.5)
    raise AssertionError("Disposable session did not reach expected state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--fixture", required=True, help="Guest fixture directory")
    parser.add_argument("--guest-python", required=True, help="Guest-visible Python with Textual")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", action="store_true", help="Pause after game-ready.json until output/continue")
    parser.add_argument("--checkpoint-timeout", type=int, default=600)
    parser.add_argument("--resume-fixture-session", action="store_true", help="Resume this harness's existing public fixture identity after a startup probe failure")
    parser.add_argument("--switch-operation", choices=("idle_session", "switch_account"), default="idle_session")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"UserKnownHostsFile={args.output / 'known-hosts'}", "-i", str(args.identity),
           "-p", str(args.ssh_port), "root@127.0.0.1"]

    def remote(command):
        return subprocess.check_output([*ssh, command], text=True, timeout=45).strip()

    def python(code, **fields):
        # Request fields travel over stdin, keeping the ephemeral PAM token out
        # of both command arguments and printed proof metadata.
        source = "import json,os,pathlib,pwd,socket,subprocess,sys,time\n"
        source += "data=json.loads(" + repr(json.dumps(fields)) + ")\n" + code
        result = subprocess.run([*ssh, shlex.join([args.guest_python, "-"])], input=source,
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise RuntimeError("Guest session operation failed: " + result.stderr[-2500:])
        return json.loads(result.stdout)

    socket_call = '''
def request(op, **fields):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(30)
        connection.connect('/run/kwak-userd.sock')
        connection.sendall(json.dumps(dict(op=op, **fields)).encode()+b'\\n')
        reply=json.loads(connection.makefile().readline())
        if not reply['ok']: raise RuntimeError(reply['error'])
        return reply['result']
'''
    assert remote("hostname") == "kwakos-vm", "Requires the owned disposable VM"
    assert remote("id -u") == "0"
    metadata = python("print(pathlib.Path(data['path']).read_text())", path=args.fixture + "/metadata.json")
    games = metadata["games"][:2]
    grant = python(socket_call + "print(json.dumps(request('login_nsec',nsec='11'*32,persistent=False)))")
    assert grant.get("temporary") and ("token" in grant or
           args.resume_fixture_session and grant.get("switched")), "Fixture player must be a new temporary account"
    username = grant["username"]
    uid = int(remote(shlex.join(["id", "-u", username])))
    assert 30000 <= uid <= 39999
    environment = {"XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}

    def user(*command):
        return remote(shlex.join(["runuser", "-u", username, "--", "env",
                                  *[f"{key}={value}" for key, value in environment.items()], *command]))

    def account(op):
        if op == "switch_account":
            # A runuser child still has the root SSH session's cgroup ancestry.
            # Run this probe beneath the actual user's manager, matching UWSM.
            code = "import json,socket\n" + socket_call + "print(json.dumps(request('switch_account')))"
            return json.loads(remote(shlex.join(["systemd-run", "--user", f"--machine={username}@.host",
                              "--wait", "--pipe", "--collect", "--quiet", args.guest_python, "-c", code])))
        return python(socket_call + "account=pwd.getpwnam(data['user']);os.initgroups(account.pw_name,account.pw_gid);os.setgid(account.pw_gid);os.setuid(account.pw_uid);print(json.dumps(request(data['op'])))", user=username, op=op)

    result = {"environment": "owned disposable VM; actual temporary identity, greetd PAM and WebKit games",
              "username": username, "uid": uid, "switch_operation": args.switch_operation,
              "resumed_probe_run": args.resume_fixture_session, "checks": [], "limitations": [
                  "Card injection uses the card-service UID and real backend handler; physical HID reader not tested",
                  "Virtual GPU and local TLS relay do not certify physical kiosk hardware"]}

    def record(check):
        result["checks"].append(check)
        print("PASS:", check, flush=True)
        (args.output / "session.json").write_text(json.dumps(result, indent=2) + "\n")

    if "token" in grant:
        python('''
import importlib.util,signal
module=(pathlib.Path('/run/current-system/sw/bin/kwak-greeter').resolve().parents[1]/'share/kwak-greeter/greeter.py')
sys.path.insert(0,str(module.parent))
spec=importlib.util.spec_from_file_location('real_greeter',module)
greeter=importlib.util.module_from_spec(spec);spec.loader.exec_module(greeter)
previous=[];ipc=None
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdigit():continue
    try:
        command=(proc/'cmdline').read_bytes().split(b'\\0')
        if str(module).encode() not in command or b'--switch' in command:continue
        if proc.stat().st_uid!=pwd.getpwnam('greeter').pw_uid:continue
        environ=dict(item.split(b'=',1) for item in (proc/'environ').read_bytes().split(b'\\0') if b'=' in item)
        if b'GREETD_SOCK' in environ:ipc=environ[b'GREETD_SOCK'].decode();previous.append(int(proc.name))
    except (OSError,ValueError):pass
assert ipc,'No real default greetd greeter found'
greeter.Greetd(ipc).login(data['user'],data['token'],['uwsm','start','hyprland-uwsm.desktop'])
for pid in previous:
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:pass
print(json.dumps({'started':True}))
''', user=username, token=grant["token"])
        grant.pop("token")
    wait_for(lambda: remote(f"test -S /run/user/{uid}/bus && echo ready") if remote(f"test -S /run/user/{uid}/bus; echo $?") == "0" else None)
    instances = wait_for(lambda: json.loads(user("hyprctl", "instances", "-j")))
    assert len(instances) == 1
    environment.update(HYPRLAND_INSTANCE_SIGNATURE=instances[0]["instance"], WAYLAND_DISPLAY=instances[0]["wl_socket"])
    record("real greetd IPC and PAM token create managed temporary graphical session")
    wait_for(lambda: any(window["class"] == "org.kwak.Library" for window in json.loads(user("hyprctl", "-j", "clients"))))
    addresses = {game["address"] for game in games}
    wait_for(lambda: addresses.issubset({game["address"] for game in json.loads(user("kwak", "--json", "installed"))["items"]}), timeout=120)
    library = python("print(pathlib.Path(data['path']).read_text())", path=f"/home/{username}/.local/state/kwak/library/state.json")
    assert library["loaded"] and addresses.issubset(set(library["base"]))
    record("automatic library startup fetches NIP78 list and downloads both saved games")
    def game_pids():
        return python('''
pids=[]
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdigit():continue
    try:
        if proc.stat().st_uid!=data['uid']:continue
        executable=(proc/'cmdline').read_bytes().split(b'\\0')[0].decode()
        if pathlib.Path(executable).name in ('napplet','kwaklet'):pids.append(int(proc.name))
    except OSError:pass
print(json.dumps(sorted(pids)))
''', uid=uid)

    existing = game_pids()
    if not args.resume_fixture_session or not existing:
        for game in games:
            user("kwak", "--json", "launch", game["address"])

    def telemetry():
        return python('''
home=pathlib.Path(data['home']);found={}
for filename in data['files']:
    paths=list((home/'.local/share').rglob(filename))
    if len(paths)!=1:continue
    store=json.loads(paths[0].read_text())
    if 'kiosk.fixture' in store:found[filename]=json.loads(store['kiosk.fixture'])
print(json.dumps(found))
''', home=f"/home/{username}", files=[game["storage_filename"] for game in games])

    snapshots = wait_for(lambda: (values if len(values := telemetry()) == 2 and all(value["heartbeat"] > 1 for value in values.values()) else None))
    assert all(not value["errors"] for value in snapshots.values())
    pids = game_pids()
    assert len(pids) == 2, "Expected two actual napplet window processes"
    ready = dict(user=username, uid=uid, addresses=sorted(addresses), pids=pids,
                 compositor_pid=instances[0]["pid"], environment=environment)
    (args.output / "game-ready.json").write_text(json.dumps(ready, indent=2) + "\n")
    record("two real sandboxed WebKit games report independent advancing storage heartbeats")
    if args.checkpoint:
        wait_for(lambda: (args.output / "continue").exists(), timeout=args.checkpoint_timeout)
    before = telemetry()
    assert account(args.switch_operation)["opened"], "Session switch request must open the switch greeter"
    control = ["systemctl", "--user", f"--machine={username}@.host"]
    group = remote(shlex.join([*control, "show", "kwakore.service", "-p", "ControlGroup", "--value"]))
    wait_for(lambda: "frozen 1" in remote(shlex.join(["cat", "/sys/fs/cgroup" + group + "/cgroup.events"])))
    paused = telemetry()
    cpu_before = remote(shlex.join(["cat", "/sys/fs/cgroup" + group + "/cpu.stat"]))
    time.sleep(2)
    still = telemetry()
    cpu_after = remote(shlex.join(["cat", "/sys/fs/cgroup" + group + "/cpu.stat"]))
    assert all(still[key]["heartbeat"] == value["heartbeat"] for key, value in paused.items())
    assert cpu_before == cpu_after, "Frozen runtime must consume no further CPU time"
    remote(shlex.join([*control, "is-active", "kwakore.service"]))
    record(args.switch_operation + " sign-in transition freezes actual game descendants and stops heartbeats and CPU time")
    python(socket_call + "account=pwd.getpwnam('kwak-cards');os.initgroups(account.pw_name,account.pw_gid);os.setgid(account.pw_gid);os.setuid(account.pw_uid);print(json.dumps(request('card_swipe',format='SKC1',secret_key='11'*32)))")
    resumed = wait_for(lambda: (values if all(values[key]["heartbeat"] > value["heartbeat"] for key,value in paused.items()) else None) if len(values := telemetry()) == 2 else None)
    assert all(resumed[key]["instance"] == value["instance"] for key,value in before.items())
    assert game_pids() == pids
    active_session = remote("loginctl show-seat seat0 -p ActiveSession --value")
    assert remote(shlex.join(["loginctl", "show-session", active_session, "-p", "Name", "--value"])) == username
    record("same card resumes original game processes and in-memory instances")
    account("signout")
    wait_for(lambda: remote(f"getent passwd {shlex.quote(username)} >/dev/null; echo $?") != "0", timeout=120)
    assert remote(f"test ! -e /home/{username}; echo $?") == "0"
    assert remote(f"pgrep -U {uid} >/dev/null; echo $?") != "0"
    record("explicit signout deletes temporary account, home and all UID processes")
    result["result"] = "PASS"
    (args.output / "session.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
