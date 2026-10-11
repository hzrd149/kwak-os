{
  lib,
  stdenvNoCC,
  python3,
  kitty,
  grim,
  util-linux,
  makeWrapper,
}:
let
  python = python3.withPackages (ps: [ ps.textual ]);
in
stdenvNoCC.mkDerivation {
  pname = "kwak-kiosk";
  version = "0.1.0";
  src = ./.;
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python ];
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    export PYTHONPATH=${../settings}
    python3 -m unittest discover -s tests
  '';
  installPhase = ''
    mkdir -p $out/share/kwak/kiosk $out/bin
    cp kiosk.py library.py overlay.py screenshots.py $out/share/kwak/kiosk/
    cp ${../settings/settings.py} $out/share/kwak/kiosk/settings.py
    cp ${./kitty.conf} $out/share/kwak/kiosk/kitty.conf
    makeWrapper ${python}/bin/python3 $out/libexec/kwak-kiosk-tui \
      --add-flags "$out/share/kwak/kiosk/kiosk.py"
    makeWrapper ${python}/bin/python3 $out/libexec/kwak-overlay-tui \
      --add-flags "$out/share/kwak/kiosk/overlay.py" \
      --prefix PATH : ${lib.makeBinPath [ grim ]}
    cat > $out/bin/kwak-kiosk <<SH
    #!${stdenvNoCC.shell}
    [ -n "\$XDG_RUNTIME_DIR" ] || exit 1
    exec ${lib.getExe' util-linux "flock"} --nonblock "\$XDG_RUNTIME_DIR/kwak-library.lock" \\
      ${lib.getExe kitty} --config $out/share/kwak/kiosk/kitty.conf \\
      --class org.kwak.Library --title 'My Games' $out/libexec/kwak-kiosk-tui "\$@"
    SH
    cat > $out/bin/kwak-overlay <<SH
    #!${stdenvNoCC.shell}
    [ -n "\$XDG_RUNTIME_DIR" ] || exit 1
    exec ${lib.getExe' util-linux "flock"} --nonblock "\$XDG_RUNTIME_DIR/kwak-overlay.lock" \\
      $out/libexec/kwak-overlay-tui --launch \\
      ${lib.getExe kitty} $out/share/kwak/kiosk/kitty.conf
    SH
    chmod +x $out/bin/*
  '';
  passthru = { inherit python; };
  meta = {
    description = "Green terminal game library and system menu";
    mainProgram = "kwak-kiosk";
    platforms = lib.platforms.linux;
  };
}
