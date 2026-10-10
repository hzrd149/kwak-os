"""Exercise the Bash entry point with fake Nix commands; never touch the host."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "etc/nixos"
        self.config.mkdir(parents=True)
        (self.root / "etc/NIXOS").touch()
        self.original = '{ imports = [ ./hardware-configuration.nix ]; system.stateVersion = "24.11"; }\n'
        (self.config / "configuration.nix").write_text(self.original)
        (self.config / "hardware-configuration.nix").write_text("hardware\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        SUDO_USER="alice", TEST_LOG=str(self.log), TEST_ROOT=str(self.root))
        self.script = self.root / "install.sh"
        # Redirect the fixed system paths and privilege check in a test-only copy.
        text = (ROOT / "install.sh").read_text()
        text = text.replace("/etc/nixos", str(self.config))
        text = text.replace("/etc/NIXOS", str(self.root / "etc/NIXOS"))
        text = text.replace("/var/backups", str(self.root / "backups"))
        text = text.replace("$EUID == 0", "1 == 1")
        self.script.write_text(text)
        self.command("uname", 'echo "${TEST_ARCH:-x86_64}"')
        self.command("getent", 'echo "alice:x:1000:100:Alice:/home/alice:/bin/bash"')
        self.command("nix", '''
printf 'nix %s\n' "$*" >> "$TEST_LOG"
if [[ "$*" == *system.stateVersion* ]]; then printf '26.05'; fi
if [[ "$*" == *" build "*.git ]]; then
  mkdir -p "$TEST_ROOT/git/bin"
  printf '#!/bin/sh\n' > "$TEST_ROOT/git/bin/git"
  chmod +x "$TEST_ROOT/git/bin/git"
  printf '%s' "$TEST_ROOT/git"
fi
if [[ "$*" == *'flake update'* ]]; then
  [[ "${TEST_FAIL_UPDATE:-}" != 1 ]] || exit 1
fi
''')
        self.command("systemd-inhibit", '''
printf 'inhibit %s\n' "$*" >> "$TEST_LOG"
while [[ "$1" == --* ]]; do shift; done
exec "$@"
''')
        self.command("systemctl", 'printf "systemctl %s\\n" "$*" >> "$TEST_LOG"')
        self.command("nixos-rebuild", '''
printf 'rebuild %s\n' "$*" >> "$TEST_LOG"
printf 'rebuild git %s\n' "$(command -v git || echo missing)" >> "$TEST_LOG"
[[ "${TEST_FAIL_REBUILD:-}" != 1 ]]
''')

    def command(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{shutil.which('bash')}\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, *args):
        return subprocess.run(["bash", str(self.script), "--yes", *args],
                              env=self.env, capture_output=True, text=True)

    def test_conversion_and_repeat_update_preserve_local_files(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        flake = (self.config / "flake.nix").read_text()
        self.assertIn("./configuration.nix", flake)
        local = self.config / "kwakos-local.nix"
        self.assertIn('kwak.adminUser = "alice";', local.read_text())
        self.assertIn("lib.mkForce false", local.read_text())
        self.assertEqual((self.config / "configuration.nix").read_text(), self.original)
        self.assertEqual((self.config / "hardware-configuration.nix").read_text(), "hardware\n")
        backups = list((self.root / "backups").glob("*/nixos/configuration.nix"))
        self.assertEqual(backups[0].read_text(), self.original)
        local.write_text(local.read_text() + "# user edit\n")
        result = self.run_script("--switch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.config / "flake.nix").read_text(), flake)
        self.assertIn("# user edit", local.read_text())
        log = self.log.read_text()
        self.assertIn("flake update kwakOS", log)
        self.assertIn("rebuild boot --flake path:", log)
        self.assertIn("rebuild switch --flake path:", log)
        self.assertIn("--no-update-lock-file", log)
        self.assertIn("inhibit --what=sleep:idle", log)
        self.assertNotIn("systemctl reboot", log)
        self.assertIn("nixosConfigurations = { ${system.config.networking.hostName} = system; }", flake)

    def test_reboot_flag_reboots_after_boot_build(self):
        result = self.run_script("--reboot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.log.read_text().endswith("systemctl reboot\n"))
        self.assertNotEqual(self.run_script("--reboot", "--switch").returncode, 0)

    def test_old_managed_flake_gains_hostname_configuration(self):
        old = """# Managed by kwakOS install.sh
{
  inputs.kwakOS.url = "github:hzrd149/kwak-os";
  outputs = { kwakOS, ... }: {
    nixosConfigurations.kwakos = kwakOS.inputs.nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        kwakOS.nixosModules.default
        ./kwak-os/hosts/physical { system.stateVersion = "25.11"; }
        ./kwakos-local.nix
      ];
    };
  };
}
"""
        (self.config / "flake.nix").write_text(old)
        (self.config / "kwakos-local.nix").write_text("{ }\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        flake = (self.config / "flake.nix").read_text()
        self.assertIn("system.config.networking.hostName", flake)
        self.assertIn('          ./kwak-os/hosts/physical { system.stateVersion = "25.11"; }\n', flake)
        self.assertEqual((self.config / "kwakos-local.nix").read_text(), "{ }\n")

    def test_conversion_hostname_defaults_to_kwakos_or_keeps_current(self):
        self.env["HOSTNAME"] = "nixos"
        self.assertEqual(self.run_script().returncode, 0)
        local = self.config / "kwakos-local.nix"
        self.assertIn('networking.hostName = lib.mkForce "kwakos";', local.read_text())
        self.assertNotEqual(self.run_script("--hostname", "other").returncode, 0)
        for path in [local, self.config / "flake.nix"]:
            path.unlink()
        self.assertNotEqual(self.run_script("--hostname", "bad.name").returncode, 0)
        self.assertEqual(self.run_script("--hostname", "nixos").returncode, 0)
        self.assertNotIn("networking.hostName", local.read_text())

    def test_iso_migration_uses_local_hardware_and_original_version(self):
        source = self.config / "kwak-os"
        (source / "hosts/physical").mkdir(parents=True)
        (source / "hosts/physical/installer-settings.nix").write_text("installer account")
        (source / "flake.nix").write_text("old source")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        flake = (self.config / "flake.nix").read_text()
        self.assertIn('./kwak-os/hosts/physical { system.stateVersion = "26.05"; }', flake)
        self.assertNotIn("kwak.adminUser", (self.config / "kwakos-local.nix").read_text())
        self.assertEqual((source / "flake.nix").read_text(), "old source")

    def test_missing_git_is_provided_from_pinned_nixpkgs(self):
        # Hide host git by exposing only the tools the script and fakes need.
        tools = self.root / "tools"
        tools.mkdir()
        for name in ["bash", "cp", "mktemp", "grep", "mkdir", "cat", "chmod", "sed"]:
            (tools / name).symlink_to(shutil.which(name))
        self.env["PATH"] = f"{self.bin}:{tools}"
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertIn("inputs.nixpkgs.legacyPackages.x86_64-linux.git", log)
        self.assertIn(f"rebuild git {self.root}/git/bin/git", log)

    def test_custom_flake_requires_explicit_target(self):
        flake = self.config / "flake.nix"
        flake.write_text("custom flake")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        result = self.run_script("--flake", f"{self.config}#my-machine")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(flake.read_text(), "custom flake")
        self.assertFalse((self.config / "kwakos-local.nix").exists())
        self.assertIn("#my-machine", self.log.read_text())

    def test_update_failure_does_not_rebuild_and_keeps_backup(self):
        self.env["TEST_FAIL_UPDATE"] = "1"
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("rebuild", self.log.read_text())
        self.assertIn("Configuration backup:", result.stderr)
        self.assertTrue(list((self.root / "backups").glob("*/nixos/configuration.nix")))

    def test_rebuild_failure_reports_backup(self):
        self.env["TEST_FAIL_REBUILD"] = "1"
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configuration backup:", result.stderr)

    def test_preflight_rejects_unsupported_host_and_invalid_user(self):
        self.env["TEST_ARCH"] = "aarch64"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.env["TEST_ARCH"] = "x86_64"
        self.assertNotEqual(self.run_script("--admin-user", '${oops}').returncode, 0)
        self.assertFalse((self.config / "flake.nix").exists())
        self.assertFalse(self.log.exists())

    def test_existing_local_module_is_not_overwritten(self):
        local = self.config / "kwakos-local.nix"
        local.write_text("user work")
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(local.read_text(), "user work")
        self.assertFalse((self.config / "flake.nix").exists())


if __name__ == "__main__":
    unittest.main()
