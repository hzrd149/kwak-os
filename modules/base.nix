{
  config,
  lib,
  pkgs,
  ...
}:
{
  options.kwak.adminUser = lib.mkOption {
    type = lib.types.str;
    default = "kwak";
    description = "Local administrator account, selected during installation.";
  };

  config = {
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
    # Reuse the same prebuilt locale archive for every installer language choice.
    i18n.supportedLocales = [ "all" ];
    console.keyMap = lib.mkDefault "us";
    services.xserver.xkb.layout = lib.mkDefault "us";

    users.users.${config.kwak.adminUser} = {
      isNormalUser = true;
      description = lib.mkDefault config.kwak.adminUser;
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
  };
}
