{
  lib,
  stdenvNoCC,
  python3,
  gtk3,
  gobject-introspection,
  wrapGAppsHook3,
  makeWrapper,
  makeDesktopItem,
  hyprland,
}:
let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
  desktopItem = makeDesktopItem {
    name = "org.kwak.Settings";
    desktopName = "Settings";
    comment = "Choose the window tiling mode";
    exec = "kwak-settings";
    icon = "preferences-system";
    categories = [
      "Settings"
      "DesktopSettings"
    ];
    startupNotify = true;
  };
in
stdenvNoCC.mkDerivation {
  pname = "kwak-settings";
  version = "0.1.0";
  src = ../apps;

  nativeBuildInputs = [
    makeWrapper
    wrapGAppsHook3
    gobject-introspection
  ];
  buildInputs = [ gtk3 ];
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    install -Dm644 settings.py $out/share/kwak/settings.py
    install -Dm644 settings.css $out/share/kwak/settings.css
    mkdir -p $out/bin $out/share/applications
    cp ${desktopItem}/share/applications/* $out/share/applications/
    makeWrapper ${python}/bin/python3 $out/bin/kwak-settings \
      --add-flags "$out/share/kwak/settings.py" \
      --prefix PATH : ${lib.makeBinPath [ hyprland ]}
    runHook postInstall
  '';

  meta = {
    description = "Window mode settings for kwakOS";
    mainProgram = "kwak-settings";
    platforms = lib.platforms.linux;
  };
}
