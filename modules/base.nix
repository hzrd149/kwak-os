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

    networking.networkmanager.enable = lib.mkDefault true;
    services.openssh = {
      enable = lib.mkDefault true;
      openFirewall = lib.mkDefault true;
      settings = {
        PermitRootLogin = lib.mkDefault "yes";
        PasswordAuthentication = lib.mkDefault true;
        KbdInteractiveAuthentication = lib.mkDefault false;
      };
    };

    time.timeZone = lib.mkDefault "America/Chicago";
    i18n.defaultLocale = lib.mkDefault "en_US.UTF-8";
    # Support every language offered by the graphical installer.
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
  };
}
