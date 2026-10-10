"""Install a new account's napplets once, retrying failed installs at next login."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def install_defaults(home, cli, state_home=None):
    home = Path(home)
    manifest = home / ".config/kwak/default-napplets.json"
    addresses = json.loads(manifest.read_text())
    if not isinstance(addresses, list) or not all(isinstance(a, str) for a in addresses):
        raise ValueError("Invalid default napplet manifest")

    state = Path(state_home or home / ".local/state") / "kwak/default-napplets"
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    failed = False
    for address in addresses:
        marker = state / hashlib.sha256(address.encode()).hexdigest()
        if marker.exists():
            continue
        result = subprocess.run([cli, "install", address], check=False)
        if result.returncode:
            failed = True
            continue
        marker.touch(mode=0o600)
    return not failed


if __name__ == "__main__":
    sys.exit(0 if install_defaults(
        Path.home(), sys.argv[1], os.environ.get("XDG_STATE_HOME")
    ) else 1)
