# Exercise the actual installer backend with no external network, then boot the
# installed disk using UEFI. Test instrumentation is added only to this target.
{
  pkgs,
  source,
  installerInputs,
  storePaths,
  expectedPortal,
  expectedHyprland,
}:
let
  common = {
    virtualisation.diskImage = "./kwakos-installed.qcow2";
    virtualisation.diskSize = 32 * 1024;
    virtualisation.memorySize = 4096;
    virtualisation.cores = 4;
  };
  prepare = pkgs.writeText "prepare-kwakos.py" ''
    import importlib.util
    import json
    import subprocess
    spec = importlib.util.spec_from_file_location("installer", "${source}/packages/installer/kwak_installer.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    class Storage:
        def value(self, key):
            return {"username": "alice", "fullname": "Alice Example", "hostname": "kwakos-offline",
                    "firmwareType": "efi", "partitions": []}.get(key)
    target = installer.prepare("/mnt", "${source}", "${installerInputs}", Storage(), {
        "timezone": "Europe/Berlin", "LANG": "de_DE.UTF-8", "kblayout": "de",
        "kbvariant": "nodeadkeys", "vconsole": "de-latin1",
    })
    settings = target / "hosts/physical/installer-settings.nix"
    text = settings.read_text().replace("{ ... }:", "{ modulesPath, ... }:")
    text = text.replace("{\n", '{\n  imports = [ (modulesPath + "/testing/test-instrumentation.nix") ];\n', 1)
    settings.write_text(text)
    installer.retain_inputs("/mnt", "${installerInputs}")
    # Source metadata AND store-reference contexts must preserve binary
    # identities; otherwise an offline install tries to rebuild the desktop.
    for attribute, expected in [
        ("config.programs.hyprland.portalPackage.drvPath", "${expectedPortal}"),
        ("pkgs.hyprland.drvPath", "${expectedHyprland}"),
    ]:
        actual = subprocess.check_output([
            "nix-instantiate", "--eval", "--strict", "--json",
            "/mnt/etc/nixos/system.nix", "-A", attribute,
        ], text=True)
        assert json.loads(actual) == expected, (attribute, actual, expected)
    directory = installer.build_directory("/mnt")
    try:
        command = installer.install_command("/mnt", directory)
        # The GUI consumes JSON progress over a pipe. Printing every byte-copy
        # progress update over the test VM's serial console makes the test stall.
        command[-1] = "raw"
        with open("/tmp/kwakos-install.log", "w+") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                log.seek(0)
                print(log.read())
                result.check_returncode()
    finally:
        installer.release_build_directory(directory)
  '';
in
pkgs.testers.runNixOSTest {
  name = "kwak-installer-offline";
  node.pkgsReadOnly = false;
  nodes = {
    installer = { lib, modulesPath, ... }: {
      imports = [
        common
        (modulesPath + "/profiles/installation-device.nix")
      ];
      virtualisation.emptyDiskImages = [ 4096 ];
      virtualisation.rootDevice = "/dev/vdb";
      virtualisation.useEFIBoot = true;
      virtualisation.fileSystems."/".autoFormat = true;
      networking.useDHCP = false;
      nix.settings = {
        experimental-features = [
          "nix-command"
          "flakes"
        ];
        substituters = lib.mkForce [ ];
        hashed-mirrors = null;
        connect-timeout = 1;
      };
      system.extraDependencies = storePaths;
      environment.systemPackages = [
        pkgs.python3
        pkgs.parted
        pkgs.dosfstools
        pkgs.e2fsprogs
        pkgs.pamtester
      ];
    };
    target = { ... }: {
      imports = [ common ];
      virtualisation.useBootLoader = true;
      virtualisation.useEFIBoot = true;
      virtualisation.useDefaultFilesystems = false;
      virtualisation.efi.keepVariables = false;
      virtualisation.fileSystems."/" = {
        device = "/dev/disk/by-label/not-used";
        fsType = "ext4";
      };
    };
  };
  testScript = ''
    installer.start()
    installer.wait_for_unit("multi-user.target")
    installer.succeed("test -d /sys/firmware/efi")
    # The test backdoor uses virtio, so all IP networking can be disabled.
    installer.succeed("nmcli networking off; ip link set eth1 down; ip link set eth0 down")
    installer.succeed("parted -s /dev/vda mklabel gpt mkpart ESP fat32 1MiB 1025MiB set 1 esp on mkpart root ext4 1025MiB 100%")
    installer.succeed("udevadm settle; mkfs.vfat -F32 /dev/vda1; mkfs.ext4 -F /dev/vda2")
    installer.succeed("mount /dev/vda2 /mnt; mkdir /mnt/boot; mount /dev/vda1 /mnt/boot")
    installer.succeed("nixos-generate-config --root /mnt")
    installer.succeed("python3 ${prepare}", timeout=1800)
    installer.succeed("nixos-enter --root /mnt -c 'echo alice:installer-test-password | chpasswd'")
    installer.succeed("nix copy --offline --no-check-sigs --to /mnt ${pkgs.pamtester}")
    installer.succeed("test -e /mnt/boot/EFI/systemd/systemd-bootx64.efi")
    installer.succeed("umount -R /mnt")
    installer.shutdown()

    target.start()
    target.wait_for_unit("multi-user.target")
    target.succeed("test $(hostname) = kwakos-offline")
    target.succeed("id alice | grep wheel")
    target.fail("id kwak")
    target.succeed("test $(readlink /etc/localtime) = ${pkgs.tzdata}/share/zoneinfo/Europe/Berlin")
    target.succeed("grep 'layout = \"de\"' /etc/xdg/hypr/hyprland.lua")
    target.succeed("grep 'variant = \"nodeadkeys\"' /etc/xdg/hypr/hyprland.lua")
    target.succeed("systemctl is-active greetd")
    target.succeed("passwd -S alice | grep ' P '")
    target.succeed("echo installer-test-password | ${pkgs.pamtester}/bin/pamtester greetd alice authenticate acct_mgmt")
    target.succeed("test -f /etc/nixos/system.nix")
    target.succeed("test -d /nix/var/nix/gcroots/kwakos-inputs")
  '';
}
