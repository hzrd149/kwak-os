{ lib, pkgs, ... }:
{
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];

  networking.networkmanager.enable = true;
  time.timeZone = lib.mkDefault "America/Chicago";
  i18n.defaultLocale = "en_US.UTF-8";
  console.keyMap = "us";
  services.xserver.xkb.layout = "us";

  users.users.kwak = {
    isNormalUser = true;
    description = "kwak";
    extraGroups = [
      "wheel"
      "networkmanager"
    ];
  };

  environment.systemPackages = with pkgs; [
    git
    vim
  ];

  # Installation compatibility version; do not bump during routine upgrades.
  system.stateVersion = "26.05";
}
