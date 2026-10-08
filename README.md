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

Rebuild an ISO-installed system:

```sh
sudo nixos-rebuild switch --flake /etc/nixos/kwak-os#physical
```

To update dependency pins first:

```sh
sudo nix flake update --flake /etc/nixos/kwak-os
```

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
