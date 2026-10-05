{ ... }:
{
  imports = [ ./hardware-configuration.nix ];

  networking.hostName = "kwakos";
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # Set kwak's password with nixos-install --no-root-passwd, then
  # nixos-enter --root /mnt -c 'passwd kwak' before rebooting.
  # No VM test password or remote access is enabled here.
}
