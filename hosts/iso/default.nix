{
  config,
  lib,
  modulesPath,
  pkgs,
  ...
}:
{
  imports = [ (modulesPath + "/installer/cd-dvd/installation-cd-graphical-calamares.nix") ];

  networking.hostName = "kwakos-live";
  image.baseName = lib.mkForce "kwakos-${config.system.nixos.release}-${pkgs.stdenv.hostPlatform.system}";
  isoImage.volumeID = "KWAKOS_LIVE";
  # Keep compression memory/time reasonable on hosted CI runners.
  isoImage.squashfsCompression = "zstd -Xcompression-level 6";

  # The installer profile supplies a RAM-backed root, hardware discovery,
  # BIOS/UEFI USB boot, the nixos console account, and passwordless sudo.
  # The desktop's autologin account also needs an unlocked local password.
  users.users.kwak.initialHashedPassword = "";
  # The live installer session logs straight into kwak instead of the Nostr greeter.
  kwak.nostrUsers.enable = false;
  services.openssh.enable = lib.mkForce false;
}
