{ lib, ... }:
{
  imports = [
    ./hardware-configuration.nix
  ]
  ++ lib.optional (builtins.pathExists ./installer-settings.nix) ./installer-settings.nix;

  networking.hostName = lib.mkDefault "kwakos";
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # nixos-install provisions the root password used for remote deployments.
  # Set kwak's password with
  # nixos-enter --root /mnt -c 'passwd kwak' before rebooting.
}
