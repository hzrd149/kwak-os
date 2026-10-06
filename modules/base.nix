{ lib, pkgs, ... }:
{
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];

  networking.networkmanager.enable = true;
  services.openssh = {
    enable = true;
    openFirewall = true;
    settings = {
      PermitRootLogin = "yes";
      PasswordAuthentication = true;
      KbdInteractiveAuthentication = false;
    };
  };

  time.timeZone = lib.mkDefault "America/Chicago";
  i18n.defaultLocale = lib.mkDefault "en_US.UTF-8";
  console.keyMap = lib.mkDefault "us";
  services.xserver.xkb.layout = lib.mkDefault "us";

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
