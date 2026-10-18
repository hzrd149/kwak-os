{
  doCheck ? false,
  lib,
  stdenvNoCC,
  python3,
  kitty,
  grim,
  wireplumber,
  networkmanager,
  util-linux,
  makeWrapper,
}:
let
  python = python3.withPackages (ps: [ ps.textual ps.textual-image ps.pillow ps.evdev ]);
in
stdenvNoCC.mkDerivation {
  pname = "kwak-kiosk";
  version = "0.1.0";
  src = ./.;
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python ];
  dontBuild = true;
  inherit doCheck;
  checkPhase = ''
    export PYTHONPATH=${../settings}
    python3 -m unittest discover -s tests
  '';
  installPhase = ''
    mkdir -p $out/share/kwak/kiosk $out/bin
    cp kiosk.py library.py overlay.py screenshots.py images.py discovery.py game_details.py \
      gamepad.py navigation.py osk.py hints.py shell_state.py icons.py browse.py shelves.py game_menu.py overlay_control.py terminal_font.py desktop_actions.py $out/share/kwak/kiosk/
    cp ${../settings/settings.py} $out/share/kwak/kiosk/settings.py
    cp ${./kitty.conf} $out/share/kwak/kiosk/kitty.conf
    makeWrapper ${python}/bin/python3 $out/libexec/kwak-kiosk-tui \
      --add-flags "$out/share/kwak/kiosk/kiosk.py" \
      --prefix PATH : ${lib.makeBinPath [ wireplumber networkmanager ]}
    makeWrapper ${python}/bin/python3 $out/libexec/kwak-overlay-tui \
      --add-flags "$out/share/kwak/kiosk/overlay.py" \
      --prefix PATH : ${lib.makeBinPath [ grim ]}
    cat > $out/bin/kwak-kiosk <<SH
    #!${stdenvNoCC.shell}
    # Local development uses the caller's terminal and host graphics stack.
    if [ "\$1" = "--local" ] && [ -t 0 ] && [ -t 1 ]; then
      exec $out/libexec/kwak-kiosk-tui "\$@"
    fi
    [ -n "\$XDG_RUNTIME_DIR" ] || exit 1
    export KWAK_TERMINAL_ROLE=library
    exec ${lib.getExe' util-linux "flock"} --nonblock "\$XDG_RUNTIME_DIR/kwak-library.lock" \\
      ${lib.getExe kitty} --config $out/share/kwak/kiosk/kitty.conf \\
      --watcher $out/share/kwak/kiosk/terminal_font.py \\
      --class org.kwak.Library --title 'My Games' $out/libexec/kwak-kiosk-tui "\$@"
    SH
    cat > $out/bin/kwak-overlay <<SH
    #!${stdenvNoCC.shell}
    [ -n "\$XDG_RUNTIME_DIR" ] || exit 1
    export PATH=${lib.makeBinPath [ grim ]}:\$PATH
    exec ${python}/bin/python3 $out/share/kwak/kiosk/overlay_control.py \\
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
