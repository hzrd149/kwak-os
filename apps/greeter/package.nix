{
  lib,
  stdenvNoCC,
  python3,
  kitty,
  makeWrapper,
  makeDesktopItem,
}:
let
  python = python3.withPackages (ps: [ ps.textual ]);
  signoutItem = makeDesktopItem {
    name = "org.kwak.SignOut";
    desktopName = "Sign Out of This Computer";
    comment = "Delete this Nostr identity's account and files from this computer";
    exec = "kwak-signout";
    icon = "system-log-out";
    categories = [ "System" ];
  };
in
stdenvNoCC.mkDerivation {
  pname = "kwak-greeter";
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
    for file in greeter.py client.py kitty.conf; do
      install -Dm644 $file $out/share/kwak-greeter/$file
    done
    makeWrapper ${python}/bin/python3 $out/libexec/kwak-greeter-tui \
      --add-flags "$out/share/kwak-greeter/greeter.py"

    kitty="${lib.getExe kitty} --config $out/share/kwak-greeter/kitty.conf"
    mkdir -p $out/bin
    # greetd runs this inside cage: a full-screen kitty window.
    cat > $out/bin/kwak-greeter <<SH
    #!${stdenvNoCC.shell}
    # The greeter user has no writable home for kitty's cache.
    [ -w "\$HOME" ] || export XDG_CACHE_HOME="\$XDG_RUNTIME_DIR/kwak-greeter-cache"
    exec $kitty --class org.kwak.Greeter --start-as fullscreen -o font_size=14 \\
      $out/libexec/kwak-greeter-tui "\$@"
    SH
    # In a terminal, run there; otherwise open a small borderless kitty window.
    cat > $out/bin/kwak-signout <<SH
    #!${stdenvNoCC.shell}
    if [ -t 0 ] && [ -t 1 ]; then exec $out/libexec/kwak-greeter-tui --signout; fi
    exec $kitty --class org.kwak.SignOut --title "Sign out" \\
      -o initial_window_width=62c -o initial_window_height=14c \\
      $out/libexec/kwak-greeter-tui --signout
    SH
    chmod +x $out/bin/*

    mkdir -p $out/share/applications
    cp ${signoutItem}/share/applications/* $out/share/applications/
    runHook postInstall
  '';

  passthru = { inherit python; };

  meta = {
    description = "Terminal greetd greeter and sign-out dialog for kwakOS Nostr identities";
    mainProgram = "kwak-greeter";
    platforms = lib.platforms.linux;
  };
}
