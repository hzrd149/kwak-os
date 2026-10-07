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
  isoImage.prependToMenuLabel = "Start KwakOS — ";
  # Keep compression memory/time reasonable on hosted CI runners.
  isoImage.squashfsCompression = "zstd -Xcompression-level 6";

  # The installer profile supplies a RAM-backed root, hardware discovery,
  # BIOS/UEFI USB boot, the nixos console account, and passwordless sudo.
  # The desktop's autologin account also needs an unlocked local password.
  users.users.kwak.initialHashedPassword = "";
  # The live installer session logs straight into kwak instead of the Nostr greeter.
  kwak.nostrUsers.enable = false;
  services.openssh.enable = lib.mkForce false;
  # The standard Calamares desktop entry is also installed in XDG autostart.
  # UWSM starts xdg-desktop-autostart.target for the live session.
  environment.etc."kwak/installation-help.txt".text = ''
    Welcome to KwakOS

    The installer opens automatically. To reopen it, press Super+Space and
    search for Install KwakOS. Installation works without an internet connection.
    Choose your language, keyboard, local account, and destination disk.
    An erase-disk installation removes the files on the selected disk.

    After installation, restart and remove the USB stick as the computer restarts.
    On the sign-in screen, choose Sign in with another account, then Linux user,
    and enter the username and password you chose in the installer.
  '';
}
