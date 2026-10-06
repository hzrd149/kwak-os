# kwakOS

A small NixOS 26.05 configuration for x86_64 Linux, with Hyprland **0.56.2**,
a Nostr sign-in screen, and a UWSM-managed desktop session. Hyprlax renders its six-layer
pixel-city demo behind a minimal black Wofi launcher. No Home Manager layer
is required.

## Layout

- `flake.nix` and `flake.lock`: system outputs and pinned Nixpkgs revision.
- `modules/base.nix`: network, locale, timezone, administrator account, and Nix.
- `modules/desktop.nix`: Hyprland, audio, and basic desktop applications.
- `modules/users.nix`: Nostr sign-in: greetd, the `kwak-userd` user manager, and PAM.
- `config/`: Hyprland/Hyprflow Lua defaults and Wofi configuration/style.
- `apps/`: kwakOS's own Python apps, one folder each (`settings`, `userd`,
  `greeter`) with its own `package.nix`, `flake.nix`, and tests.
- `packages/`: pinned desktop packages, Hyprflow plugin, and Wofi grid patch.
- `hosts/vm`: local QEMU VM with a separate test password.
- `hosts/physical`: UEFI installation with a hardware configuration template.
- `hosts/iso`: bootable live desktop and manual installation media.

The default user is `kwak`. Change it in `modules/base.nix` and the VM password
setting together if needed. The timezone defaults to `America/Chicago`.

## Run the VM

Install Nix on a Linux host, with flakes enabled. From this repository:

```sh
nix run .#vm
```

Sign in with a Nostr key, or choose **Local account…** and log in as **kwak**,
password **nixos**. See [Nostr users](docs/users.md). The VM has 4 cores, 4 GiB RAM, and a persistent 20 GiB
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

Desktop defaults are installed at `/etc/xdg/hypr/hyprland.lua`. A user's
`~/.config/hypr/hyprland.lua` overrides them without being overwritten on rebuild.
Older generated `.conf` files are preserved; port personal settings to Lua to
override the system Lua configuration.

The default **Kwassik** mode opens each main window on an empty workspace and
fills the available area. Empty workspaces are reused; floating dialogs remain
with the app. Open **Settings** from Wofi or press **Super+,** to choose Kwassik,
Master, Dwindle, or Scrolling. Apply changes the current desktop and saves the
choice for later sessions. [Mode behavior and visual proofs](docs/settings.md).

| Control | Action |
| --- | --- |
| Super+R / Super+Space | Open Wofi |
| Super+Tab | Open / close Hyprflow workspace overview |
| Super+, | Open Settings |
| Four-finger swipe up | Open Wofi |
| Super+Q / Super+E | Kitty / Dolphin |
| Super+C / Super+F | Close / fullscreen window |
| Super+Left / Super+Right | Previous / next workspace in Kwassik; focus windows in other modes |
| Super+Up / Super+Down | Focus windows in tiled modes |
| Super+1…0 | Switch workspace |
| Three-finger horizontal swipe | Switch workspace |
| Super+Shift+M | End the UWSM session |

Hyprflow provides Cover Flow workspace navigation. While it is open, use
Left/Right or 1–9 to select a workspace, Enter to activate it, or Escape to
return to the original workspace. The default shows nine numeric cards plus
existing workspaces on the focused monitor. [Configuration and verification](docs/hyprflow.md).

Wofi shows **four columns × three rows**, with centered 64-pixel icons and
16-pixel labels in 160-pixel square cells. Selection outlines sit 8 pixels
inside each cell. More apps
continue horizontally, ordered top-to-bottom within each column. Type to search,
use arrows or Tab/Shift+Tab to select, PageUp/PageDown to move four columns,
Enter to launch, and Escape to close. Scroll sideways with two fingers or drag
on a touchscreen; a mouse wheel also moves horizontally. The launcher uses GTK's
kinetic scrolling. The grid has a fixed 640-pixel logical width.

`kwak-launcher` uses the checked-in Wofi defaults; `kwak-wallpaper` starts hyprlax
2.2.7 with its upstream pixel-city demo. The package corrects a stray `VV` suffix
in the upstream demo TOML; all six images and other settings are unchanged.
The demo artwork is attributed to [CraftPix](https://craftpix.net/freebies/)
by [hyprlax](https://github.com/sandwichfarm/hyprlax/tree/v2.2.7/examples/pixel-city).

See [desktop validation and visual proofs](docs/desktop-validation.md).

## Build or download a live ISO

```sh
nix build .#iso --out-link result-iso
ls result-iso/iso/*.iso
```

The x86_64 image supports BIOS and UEFI boot from optical media or USB. It opens
the kwakOS desktop as `kwak`; the live accounts have empty passwords and
passwordless sudo. SSH is disabled. Changes disappear on reboot. The graphical
kwakOS installer opens automatically and is also available from the application
launcher. It walks through locale, keyboard, account, hostname, and disk setup;
the final confirmation is destructive when an erase-disk layout is selected.

The installer copies this flake to `/etc/nixos/kwak-os`, replaces the physical
hardware template with the detected target hardware, and installs
`#physical`. The selected account password and root password are applied by the
installer, and the installed system boots with SSH enabled for deployments.

The [ISO workflow](.github/workflows/iso.yml) builds branch pushes and uploads the
image plus checksums to Actions artifacts and GitHub Packages as an OCI artifact.
See [ISO building, downloading, and validation](docs/iso.md) for exact commands,
package access, and the distinction between live media and an installed system.

## Install on a physical machine

This target assumes **x86_64, UEFI boot, and an EFI partition mounted at
`/mnt/boot`**. For legacy BIOS or different architectures, adjust the boot loader
and platform before installing. Hardware-specific GPU configuration, particularly
NVIDIA, belongs in `hosts/physical` after identifying the machine.

1. Boot the kwakOS live ISO or official NixOS installer in UEFI mode and connect to the network.
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
   sudo nixos-install --flake .#physical
   sudo nixos-enter --root /mnt -c 'passwd kwak'
   sudo reboot
   ```

   The physical target has no preset password. The installer prompts for the
   root password used by remote deployments; `kwak` can also administer the
   system with `sudo`.

6. Keep a clone of this repo on the installed machine, including its generated
   hardware file. Apply later changes from that clone:

   ```sh
   sudo nixos-rebuild switch --flake .#physical
   ```

SSH and the firewall rule for TCP/22 are enabled by the shared base module.
After installation, deploy directly as root with `nixos-rebuild --target-host`
or migrate root to an authorized SSH key. Keep the physical hardware
configuration in version control, but keep private keys and plaintext passwords
out of the repo.

## Check and update

Nix only includes Git-tracked files when resolving `.` as a flake. Stage new
configuration files before evaluating or building them:

```sh
git add flake.nix flake.lock modules hosts config packages tests .gitignore README.md
nix flake check --no-build
nix fmt
```

`nix flake check --no-build` evaluates all system configurations; it does not
boot them. `nix flake check` builds the VM and physical system closures and checks the desktop
Lua configuration and pixel-city assets. After editing the
hardware configuration, run the evaluation check again before deployment.

Update the pin deliberately, then check and rebuild:

```sh
nix flake update nixpkgs
nix flake check --no-build
```

Keep `system.stateVersion` at its installation value when updating packages.
The lock file pins Nixpkgs and the separate Hyprland v0.56.2 release input.
Hyprland retains its upstream dependency pins and matching portal. Updating
Nixpkgs alone does not change the compositor release.

References: [Hyprland on NixOS](https://wiki.hypr.land/Nix/Hyprland-on-NixOS/)
and the [NixOS installation manual](https://nixos.org/manual/nixos/stable/#sec-installation).
