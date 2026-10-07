"""KwakOS target configuration preparation, shared by Calamares and tests."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def nix_string(value):
    # JSON quoting alone does not escape Nix interpolation.
    return json.dumps(str(value), ensure_ascii=False).replace("${", r"\${")


def settings(storage, variables):
    username = storage.value("username")
    if (not username or not re.fullmatch(r"[a-z_][a-z0-9_-]*", username)
            or username in {"root", "nixos", "greeter", "kwak-cards"}
            or username.startswith("nixbld")):
        raise ValueError("Choose a valid local account name before installing.")
    entries = {
        "kwak.adminUser": username,
        "networking.hostName": storage.value("hostname") or "kwakos",
        "time.timeZone": variables.get("timezone", "America/Chicago"),
        "i18n.defaultLocale": variables.get("LANG", "en_US.UTF-8"),
        "console.keyMap": variables.get("vconsole", "us"),
        "services.xserver.xkb.layout": variables.get("kblayout", "us"),
        "services.xserver.xkb.variant": variables.get("kbvariant", ""),
        f"users.users.{nix_string(username)}.description": storage.value("fullname") or username,
    }
    lines = ["{ ... }:", "{"]
    lines.extend(f"  {key} = {nix_string(value)};" for key, value in entries.items())
    for key in ("LC_ADDRESS", "LC_IDENTIFICATION", "LC_MEASUREMENT", "LC_MONETARY",
                "LC_NAME", "LC_NUMERIC", "LC_PAPER", "LC_TELEPHONE", "LC_TIME"):
        if variables.get(key):
            lines.append(f"  i18n.extraLocaleSettings.{key} = {nix_string(variables[key])};")
    # nixos-generate-config does not detect encrypted swap reliably.
    for part in storage.value("partitions") or []:
        if (part.get("claimed") and part.get("fsName") in ("luks", "luks2")
                and part.get("fs") == "linuxswap"):
            mapper, uuid = part.get("luksMapperName"), part.get("uuid")
            if not mapper or not uuid:
                raise ValueError("The encrypted swap partition has no mapper name or UUID.")
            lines.append(f"  boot.initrd.luks.devices.{nix_string(mapper)}.device = "
                         f"{nix_string('/dev/disk/by-uuid/' + uuid)};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def prepare(root, source, storage, variables):
    if storage.value("firmwareType") != "efi":
        raise ValueError("Restart and select the UEFI entry for your USB stick. KwakOS requires UEFI.")
    root = Path(root)
    target = root / "etc/nixos/kwak-os"
    configuration = settings(storage, variables)
    if target.exists():
        raise ValueError("A KwakOS configuration already exists on the target. Back it up before retrying.")
    shutil.copytree(source, target)
    for path in [target, *target.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o200)
    shutil.copyfile(root / "etc/nixos/hardware-configuration.nix",
                    target / "hosts/physical/hardware-configuration.nix")
    (target / "hosts/physical/installer-settings.nix").write_text(configuration)
    return target


def build_directory(root):
    # Calamares mounts targets below /tmp. Nix rejects build directories with a
    # world-writable ancestor. A root-owned bind mount keeps builds on the target
    # disk while giving Nix a safe path outside /tmp (and outside the live RAM store).
    destination = Path(root) / "nix/var/nix/builds"
    destination.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="kwakos-builds-", dir="/run"))
    try:
        subprocess.run(["mount", "--bind", str(destination), str(directory)], check=True)
    except Exception:
        directory.rmdir()
        raise
    return directory


def release_build_directory(directory):
    subprocess.run(["umount", str(directory)], check=True)
    Path(directory).rmdir()


def install_command(root, directory):
    return [
        "pkexec", "nixos-install", "--no-root-passwd", "--no-channel-copy",
        "--root", str(root), "--flake", str(Path(root) / "etc/nixos/kwak-os") + "#physical",
        "--no-update-lock-file",
        "--option", "build-dir", str(directory),
        "--log-format", "internal-json",
    ]
