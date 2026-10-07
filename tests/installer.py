"""Installer configuration boundary tests; no disks are touched."""

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "kwak_installer", Path(__file__).resolve().parents[1] / "packages/installer/kwak_installer.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class Storage:
    def __init__(self, **values):
        self.values = dict(username="alice", hostname="alice-pc", fullname="Alice",
                           firmwareType="efi", partitions=[])
        self.values.update(values)

    def value(self, key):
        return self.values.get(key)


class InstallerTests(unittest.TestCase):
    def test_preflight_rejects_legacy_and_missing_esp(self):
        spec = importlib.util.spec_from_file_location(
            "preflight", Path(__file__).resolve().parents[1] / "packages/installer/preflight.py"
        )
        module = importlib.util.module_from_spec(spec)
        storage = Storage()
        with patch.dict("sys.modules", {"libcalamares": SimpleNamespace(globalstorage=storage),
                                       "kwak_installer": installer}):
            spec.loader.exec_module(module)
        with patch.object(module.os.path, "isdir", return_value=False):
            self.assertIn("UEFI", module.run()[0])
        with patch.object(module.os.path, "isdir", return_value=True):
            self.assertIn("EFI system partition", module.run()[0])
            storage.values["partitions"] = [{"mountPoint": "/boot", "fs": "fat32"}]
            self.assertIsNone(module.run())
            storage.values["username"] = "root"
            self.assertIn("local account", module.run()[0])
            storage.values["username"] = "alice"
            storage.values["partitions"][0].update(claimed=True, device="/dev/sda1")
            with patch.object(module.os.path, "ismount", return_value=True), \
                    patch.object(module.subprocess, "check_output", side_effect=[
                        "/dev/sda1\n", "/dev/sda disk\n", "/dev/sda disk\n"]):
                self.assertIn("different destination", module.run()[0])

    def test_choices_and_nix_interpolation(self):
        text = installer.settings(Storage(fullname='Zoë "${builtins.abort "oops"}"'), {
            "timezone": "Europe/Berlin", "LANG": "de_DE.UTF-8",
            "kblayout": "de", "kbvariant": "nodeadkeys", "vconsole": "de-latin1",
            "LC_TIME": "de_DE.UTF-8",
        })
        self.assertIn('kwak.adminUser = "alice";', text)
        self.assertIn('services.xserver.xkb.layout = "de";', text)
        self.assertIn('services.xserver.xkb.variant = "nodeadkeys";', text)
        self.assertIn('console.keyMap = "de-latin1";', text)
        self.assertIn('i18n.extraLocaleSettings.LC_TIME = "de_DE.UTF-8";', text)
        self.assertIn(r'\${builtins.abort', text)
        self.assertNotIn("autoLogin", text)

    def test_invalid_user(self):
        for name in (None, "", "alice;", "${oops}", "Alice", "root", "greeter", "nixbld1"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                installer.settings(Storage(username=name), {})

    def test_encrypted_swap_is_preserved(self):
        text = installer.settings(Storage(partitions=[{
            "claimed": True, "fsName": "luks2", "fs": "linuxswap",
            "luksMapperName": "swap", "uuid": "1234",
        }]), {})
        self.assertIn('boot.initrd.luks.devices."swap".device = "/dev/disk/by-uuid/1234";', text)

    def test_prepare_target_and_reject_legacy_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, root = base / "source", base / "target"
            (source / "hosts/physical").mkdir(parents=True)
            (root / "etc/nixos").mkdir(parents=True)
            (source / "flake.nix").write_text("source")
            (root / "etc/nixos/hardware-configuration.nix").write_text("detected hardware")
            manifest = base / "inputs.json"
            manifest.write_text(json.dumps({"nixpkgs": {"path": "/nix/store/example-source", "metadata": {}}}))
            with self.assertRaisesRegex(ValueError, "UEFI"):
                installer.prepare(root, source, manifest, Storage(firmwareType="bios"), {})
            self.assertFalse((root / "etc/nixos/kwak-os").exists())
            target = installer.prepare(root, source, manifest, Storage(), {})
            self.assertEqual((target / "hosts/physical/hardware-configuration.nix").read_text(),
                             "detected hardware")
            self.assertIn('kwak.adminUser = "alice"',
                          (target / "hosts/physical/installer-settings.nix").read_text())
            self.assertIn("offline-flake.nix", (root / "etc/nixos/system.nix").read_text())
            with self.assertRaisesRegex(ValueError, "already exists"):
                installer.prepare(root, source, manifest, Storage(), {})

    def test_install_uses_local_inputs_and_target_build_directory(self):
        command = installer.install_command("/tmp/calamares-target", "/run/kwakos-builds-test")
        self.assertIn("--no-channel-copy", command)
        self.assertNotIn("--flake", command)
        self.assertIn("/run/kwakos-builds-test", command)
        index = command.index("substituters")
        self.assertEqual(command[index + 1], "")


if __name__ == "__main__":
    unittest.main()
