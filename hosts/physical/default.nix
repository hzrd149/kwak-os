{ ... }:
{
  imports = [ ./hardware-configuration.nix ];

  networking.hostName = "kwakos";
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # nixos-install provisions the root password used for remote deployments.
  # Set kwak's password with
  # nixos-enter --root /mnt -c 'passwd kwak' before rebooting.
}
