# kwakOS

A small, stock NixOS 26.05 configuration for x86_64 Linux, using Hyprland
as the Wayland desktop and SDDM as the graphical login manager. The desktop
uses the upstream Hyprland configuration generated on first launch; no theme
or Home Manager layer is added. UWSM manages the desktop session.

## Layout

- `flake.nix` and `flake.lock`: system outputs and pinned Nixpkgs revision.
- `modules/base.nix`: network, locale, timezone, administrator account, and Nix.
- `modules/desktop.nix`: Hyprland, SDDM, audio, and basic desktop applications.
- `hosts/vm`: local QEMU VM with a separate test password.
- `hosts/physical`: UEFI installation with a hardware configuration template.

The default user is `kwak`. Change it in `modules/base.nix` and the VM password
setting together if needed. The timezone defaults to `America/Chicago`.

## Run the VM

Install Nix on a Linux host, with flakes enabled. From this repository:

```sh
nix run .#vm
```

Log in through SDDM as **kwak**, password **nixos**, and select the Hyprland
UWSM session if needed. The VM has 4 cores, 4 GiB RAM, and a persistent 20 GiB
virtual disk created in the current directory. Shut it down before deleting
`kwakos-vm.qcow2` to reset it. The password is applied when the account is first
created; an existing VM disk retains later password changes.

The VM uses virtio graphics with OpenGL through a bundled Mesa software renderer
(llvmpipe). This works on non-NixOS hosts, including Ubuntu with proprietary
NVIDIA drivers, without relying on their host OpenGL libraries. Run it from a
graphical Linux session. KVM access (`/dev/kvm`) improves CPU performance; desktop
rendering runs on the CPU and will be slower than hardware acceleration.
The GTK `canberra-gtk-module` messages are harmless missing sound-module notices. This target is a local QEMU runner;
it is not an installer ISO or a disk image for arbitrary hypervisors.

To build without launching:

```sh
nix build .#vm
./result/bin/run-kwakos-vm-vm
```

Hyprland's generated config is in `~/.config/hypr/hyprland.conf`. Its usual
bindings include Super+Q for Kitty, Super+R for the launcher, Super+E for Dolphin,
and Super+C to close a window. Consult that generated file for the exact bindings
of the pinned release. Firefox is also installed.

## Install on a physical machine

This target assumes **x86_64, UEFI boot, and an EFI partition mounted at
`/mnt/boot`**. For legacy BIOS or different architectures, adjust the boot loader
and platform before installing. Hardware-specific GPU configuration, particularly
NVIDIA, belongs in `hosts/physical` after identifying the machine.

1. Boot the official NixOS installer in UEFI mode and connect to the network.
2. Partition and format the intended disk following the NixOS installation
   manual, then mount the root filesystem at `/mnt` and EFI partition at
   `/mnt/boot`. Disk formatting erases data; choose the actual disk yourself.
3. Obtain this repo in the installer (replace the URL with your repository URL):

   ```sh
   nix-shell -p git
   git clone <repository-url> kwakOS
   cd kwakOS
   ```

4. Replace the hardware template with settings generated from the mounted system:

   ```sh
   sudo nixos-generate-config --root /mnt
   sudo cp /mnt/etc/nixos/hardware-configuration.nix hosts/physical/hardware-configuration.nix
   git add hosts/physical/hardware-configuration.nix
   ```

   Review the generated file, including filesystem UUIDs, swap, kernel modules,
   and CPU settings. The checked-in template uses `nixos` and `ESP` labels only
   to make the configuration evaluable; replace it before deployment.

5. Install and set the administrator's password before rebooting:

   ```sh
   sudo nixos-install --flake .#physical --no-root-passwd
   sudo nixos-enter --root /mnt -c 'passwd kwak'
   sudo reboot
   ```

   The physical target has no preset password. Root stays locked when using
   `--no-root-passwd`; `kwak` can administer the system with `sudo`.

6. Keep a clone of this repo on the installed machine, including its generated
   hardware file. Apply later changes from that clone:

   ```sh
   sudo nixos-rebuild switch --flake .#physical
   ```

SSH is disabled by default. For remote deployment, explicitly enable
`services.openssh.enable`, configure the user's authorized SSH keys, and provision
the machine before using `nixos-rebuild` with `--target-host` and
`--sudo`. Keep the physical hardware configuration in version control, but keep
private keys and plaintext passwords out of the repo.

## Check and update

Nix only includes Git-tracked files when resolving `.` as a flake. Stage new
configuration files before evaluating or building them:

```sh
git add flake.nix flake.lock modules hosts .gitignore README.md
nix flake check --no-build
nix fmt
```

`nix flake check --no-build` evaluates both system configurations; it does not
boot them. `nix flake check` builds both system closures. After editing the
hardware configuration, run the evaluation check again before deployment.

Update the pin deliberately, then check and rebuild:

```sh
nix flake update nixpkgs
nix flake check --no-build
```

Keep `system.stateVersion` at its installation value when updating packages.
The lock file pins Nixpkgs, Hyprland, and its portal together.

References: [Hyprland on NixOS](https://wiki.hypr.land/Nix/Hyprland-on-NixOS/)
and the [NixOS installation manual](https://nixos.org/manual/nixos/stable/#sec-installation).
