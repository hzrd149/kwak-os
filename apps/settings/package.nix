{
  lib,
  stdenvNoCC,
  python3,
  kitty,
  makeWrapper,
  makeDesktopItem,
  # null uses the hyprctl of the running desktop from PATH.
  hyprland ? null,
}:
let
  python = python3.withPackages (ps: [ ps.textual ]);
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
  };
in
stdenvNoCC.mkDerivation {
  pname = "kwak-settings";
  version = "0.1.0";
  src = ./.;

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python ];
  dontBuild = true;
  doCheck = true;

  checkPhase = ''
    runHook preCheck
    python3 -m unittest discover -s tests
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 settings.py $out/share/kwak/settings.py
    install -Dm644 kitty.conf $out/share/kwak/settings-kitty.conf
    makeWrapper ${python}/bin/python3 $out/libexec/kwak-settings-tui \
      --add-flags "$out/share/kwak/settings.py" \
      ${lib.optionalString (hyprland != null) "--prefix PATH : ${lib.makeBinPath [ hyprland ]}"}
    # In a terminal, run there; otherwise open a borderless kitty window.
    mkdir -p $out/bin
    cat > $out/bin/kwak-settings <<SH
    #!${stdenvNoCC.shell}
    if [ -t 0 ] && [ -t 1 ]; then exec $out/libexec/kwak-settings-tui "\$@"; fi
    exec ${lib.getExe kitty} --config $out/share/kwak/settings-kitty.conf \\
      --class org.kwak.Settings --title Settings $out/libexec/kwak-settings-tui "\$@"
    SH
    chmod +x $out/bin/kwak-settings
    mkdir -p $out/share/applications
    cp ${desktopItem}/share/applications/* $out/share/applications/
    runHook postInstall
  '';

  passthru = { inherit python; };

  meta = {
    description = "Window mode settings for kwakOS";
    mainProgram = "kwak-settings";
    platforms = lib.platforms.linux;
  };
}
