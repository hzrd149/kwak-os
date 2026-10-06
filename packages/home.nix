{ pkgs }:
let
  launch = pkgs.writeShellApplication {
    name = "kwak-home-launch";
    runtimeInputs = [
      pkgs.uwsm
      pkgs.gtk3
    ];
    text = ''
      # gtk-launch handles desktop-entry field codes, terminal apps, and D-Bus activation.
      exec uwsm app -- gtk-launch "$1"
    '';
  };
in
pkgs.writeShellApplication {
  name = "kwak-home";
  runtimeInputs = [
    pkgs.quickshell
    launch
  ];
  text = ''
    export QT_QUICK_CONTROLS_STYLE=Basic
    export QS_ICON_THEME=Adwaita
    exec quickshell --no-duplicate --path ${../config/quickshell/home} "$@"
  '';
}
