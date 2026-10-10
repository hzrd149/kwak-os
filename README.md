# kwakOS

NixOS 26.05 for x86_64 Linux: Hyprland 0.56.2, Nostr sign-in, Wofi launcher,
and an animated pixel-city wallpaper. No Home Manager required.

## Try the VM

With Nix and flakes enabled, run from this repo in a graphical Linux session:

```sh
nix run .#vm
```

Sign in with Nostr or choose **Sign in with another account → Linux user**:
username `kwak`, password `nixos`.
The VM has 4 cores, 4 GiB RAM, and a persistent 20 GiB disk.
To reset it, shut it down before deleting `kwakos-vm.qcow2`.

## Install from ISO

Download the **kwakos-iso-…** artifact from the
[ISO workflow](https://github.com/hzrd149/kwak-os/actions/workflows/iso.yml),
or build it:

```sh
nix build .#iso --out-link result-iso
ls result-iso/iso/*.iso
```

1. Flash the extracted `.iso` to USB with Etcher or Rufus (DD mode).
2. Boot its **UEFI** entry with Secure Boot disabled.
3. Connect to the internet and complete **Install KwakOS**.
4. Restart, remove the USB, and sign in with your chosen account or Nostr.

Requires x86_64, UEFI, at least 3 GiB RAM, and 25 GiB disk space.
**Back up first: “Erase disk” deletes the selected disk's data.**
The live session has passwordless sudo and SSH disabled; the installed system
enables SSH and saves its detected hardware and configuration in `/etc/nixos/kwak-os`.

## Install or update with one command

On an installed **x86_64 NixOS or kwakOS** machine, run:

```sh
curl -fsSL https://raw.githubusercontent.com/hzrd149/kwak-os/master/install.sh | sudo bash
```

Run the same command again to update. Internet access is required. The script
asks for confirmation, backs up `/etc/nixos` under `/var/backups/kwakos.…`,
and builds the new system for the **next boot**, then offers to reboot.
It does not partition disks or change your account password.

As with any downloaded root script, inspect it first if you prefer:

```sh
curl -fsSL https://raw.githubusercontent.com/hzrd149/kwak-os/master/install.sh -o install.sh
less install.sh
sudo bash install.sh
```

For a conventional NixOS installation, the script creates `/etc/nixos/flake.nix`
and `kwakos-local.nix`, retaining `configuration.nix`, hardware, bootloader,
networking, credentials, and `system.stateVersion`. The sudo caller becomes
`kwak.adminUser`; use `--admin-user USER` when running directly as root.
The local overrides disable standard competing display managers, autologin,
and PulseAudio in favor of greetd and PipeWire. Review unusual desktop or
Home Manager configurations yourself; arbitrary custom configurations are not
automatically rewritten. Conversion enables the shared module's SSH defaults;
keep or add your own SSH restrictions in `configuration.nix`.

For ISO-installed systems, the script reuses the hardware and installer choices
in `/etc/nixos/kwak-os/hosts/physical`, preserving the evaluated state version.
The old source copy is retained, but the new `/etc/nixos#kwakos` flake imports
the current upstream module. After migration, use the script or this new target,
not the old `/etc/nixos/kwak-os#physical` target.

Subsequent runs update only the `kwakOS` input, using upstream's pinned
dependencies; machine-local files are not overwritten. Add `--switch` to
activate immediately (this may end the desktop session), `--reboot` to reboot
into the new system without asking, or `--yes` to skip confirmation:

```sh
sudo bash install.sh --switch
```

If a build fails, the script reports the backup and leaves the configuration
available for inspection and retry. It does not attempt automatic restoration.
The previous NixOS generation remains available from the boot menu; you can
also restore the backed-up configuration before rebuilding.

**Status:** full install/reboot/login validation is still pending.
See [ISO details and validation](docs/iso.md).

## Use on existing NixOS

Import `nixosModules.default` into your machine's flake. For an existing
`/etc/nixos/configuration.nix`, add this `/etc/nixos/flake.nix`:

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    kwakOS.url = "github:hzrd149/kwak-os";
    kwakOS.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { nixpkgs, kwakOS, ... }: {
    nixosConfigurations.my-machine = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [ kwakOS.nixosModules.default ./configuration.nix ];
    };
  };
}
```

In your existing configuration:
- Keep hardware imports, bootloader, networking, credentials, and the original `system.stateVersion`.
- Set `kwak.adminUser = "your-username";` (default: `kwak`).
- Disable conflicting display managers and PulseAudio; kwakOS uses greetd and PipeWire.
- If using Git, stage new configuration files before rebuilding.

Apply:

```sh
sudo nixos-rebuild switch --flake /etc/nixos#my-machine
```

Update kwakOS, then run the rebuild again:

```sh
cd /etc/nixos
sudo nix flake update kwakOS
```

Alternatively, use the script with your existing flake target:

```sh
sudo bash install.sh --flake /etc/nixos#my-machine
```

The flake must have an input named `kwakOS` and already import its module.
The script refuses to overwrite a custom `/etc/nixos/flake.nix`. With `--flake`,
it only backs up, updates that input, and rebuilds the selected host; it does
not add desktop overrides or change other top-level dependency pins.

## Remote deployment

Keep a local copy of the **target machine's flake**, hardware configuration,
and lock file. From this repo:

```sh
nix develop
nixos-rebuild switch --flake /path/to/machine-config#my-machine --target-host root@REMOTE_IP
```

Root SSH must already work. Defaults enable port 22 and root password login;
machine-local settings can override them. Prefer authorized SSH keys and disable
password authentication after verifying key login. Never commit secrets.
The repo's `.#physical` uses a hardware template—do not deploy it unchanged.

## Desktop

| Shortcut | Action |
| --- | --- |
| Super+Space / Super+R | Launcher |
| Super+Tab | Workspace overview |
| Super+, | Settings and layout modes |
| Super+Q / Super+E | Terminal / files |
| Super+C / Super+F | Close / fullscreen |
| Super+1…0 | Switch workspace |
| Super+Shift+M | End session |

Override desktop defaults with `~/.config/hypr/hyprland.lua`.
Details: [Nostr users](docs/users.md), [layouts](docs/settings.md),
[Hyprflow](docs/hyprflow.md), [desktop validation](docs/desktop-validation.md).
Wallpaper artwork: [CraftPix](https://craftpix.net/freebies/) via
[hyprlax](https://github.com/sandwichfarm/hyprlax/tree/v2.2.7/examples/pixel-city).

## Development

`modules/`: shared OS configuration · `hosts/`: VM, physical, ISO ·
`apps/`: Python apps · `packages/`: desktop packages · `config/`: desktop defaults.

```sh
nix develop
nix flake check --no-build  # Evaluate configurations
nix flake check             # Build and run checks
nix fmt
```
