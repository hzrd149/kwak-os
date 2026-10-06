{
  pkgs,
  hyprland,
  ...
}:
let
  kwak-home = import ../packages/home.nix { inherit pkgs; };
  kwak-launcher = pkgs.writeShellApplication {
    name = "kwak-launcher";
    runtimeInputs = [ pkgs.wofi ];
    text = ''
      exec wofi --conf /etc/xdg/wofi/config --style /etc/xdg/wofi/style.css --show drun "$@"
    '';
  };
  kwak-wallpaper = pkgs.writeShellApplication {
    name = "kwak-wallpaper";
    runtimeInputs = [ pkgs.hyprlax ];
    text = ''
      exec hyprlax --config ${pkgs.hyprlax}/share/hyprlax/pixel-city/parallax.toml "$@"
    '';
  };
in
{
  # The release flake supplies the compositor and its matching portal together.
  programs.hyprland.enable = true;
  programs.hyprland.withUWSM = true;
  programs.hyprland.package = pkgs.hyprland;
  programs.hyprland.portalPackage =
    hyprland.packages.${pkgs.stdenv.hostPlatform.system}.xdg-desktop-portal-hyprland;

  systemd.user.services.kwak-home = {
    description = "kwakOS desktop application grid";
    wantedBy = [ "graphical-session.target" ];
    partOf = [ "graphical-session.target" ];
    after = [ "graphical-session-pre.target" ];
    unitConfig.ConditionEnvironment = "WAYLAND_DISPLAY";
    serviceConfig = {
      ExecStart = "${kwak-home}/bin/kwak-home";
      Restart = "on-failure";
      RestartSec = 2;
    };
  };

  # XDG system defaults leave each user's ~/.config overrides intact.
  environment.etc = {
    "xdg/hypr/hyprland.lua".source = ../config/hypr/hyprland.lua;
    "xdg/wofi/config".source = ../config/wofi/config;
    "xdg/wofi/style.css".source = ../config/wofi/style.css;
  };

  services.displayManager.sddm.enable = true;
  services.displayManager.sddm.wayland.enable = true;
  services.displayManager.defaultSession = "hyprland-uwsm";
  services.displayManager.autoLogin = {
    enable = true;
    user = "kwak";
  };

  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };
  security.rtkit.enable = true;

  fonts.packages = [ pkgs.dejavu_fonts ];

  # Default desktop applications and pinned wallpaper/launcher entry points.
  environment.systemPackages = with pkgs; [
    kitty
    wofi
    hyprlax
    kwak-launcher
    kwak-wallpaper
    kwak-home
    adwaita-icon-theme
    kdePackages.dolphin
    firefox
  ];
  xdg.portal.extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
}
