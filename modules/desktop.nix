{ pkgs, ... }:
{
  # Use the matching compositor and portal from the same pinned Nixpkgs.
  programs.hyprland.enable = true;
  programs.hyprland.withUWSM = true;

  services.displayManager.sddm.enable = true;
  services.displayManager.sddm.wayland.enable = true;
  services.displayManager.defaultSession = "hyprland-uwsm";

  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };
  security.rtkit.enable = true;

  # Applications used by Hyprland's upstream generated default configuration.
  environment.systemPackages = with pkgs; [
    kitty
    wofi
    kdePackages.dolphin
    firefox
  ];
  xdg.portal.extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
}
