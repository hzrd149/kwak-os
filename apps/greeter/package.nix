{
  lib,
  stdenvNoCC,
  python3,
  gtk3,
  gobject-introspection,
  wrapGAppsHook3,
  makeWrapper,
  makeDesktopItem,
}:
let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
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

  nativeBuildInputs = [
    makeWrapper
    wrapGAppsHook3
    gobject-introspection
  ];
  buildInputs = [ gtk3 ];
  nativeCheckInputs = [ python3 ];
  dontBuild = true;
  doCheck = true;
  # The wrappers are made in postFixup, once gappsWrapperArgs is known.
  dontWrapGApps = true;

  checkPhase = ''
    runHook preCheck
    python3 -m unittest discover -s tests
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    for file in greeter.py client.py greeter.css; do
      install -Dm644 $file $out/share/kwak/$file
    done
    mkdir -p $out/share/applications
    cp ${signoutItem}/share/applications/* $out/share/applications/
    runHook postInstall
  '';

  postFixup = ''
    makeWrapper ${python}/bin/python3 $out/bin/kwak-greeter \
      "''${gappsWrapperArgs[@]}" \
      --add-flags "$out/share/kwak/greeter.py"
    makeWrapper ${python}/bin/python3 $out/bin/kwak-signout \
      "''${gappsWrapperArgs[@]}" \
      --add-flags "$out/share/kwak/greeter.py --signout"
  '';

  passthru = { inherit python; };

  meta = {
    description = "greetd greeter and sign-out dialog for kwakOS Nostr identities";
    mainProgram = "kwak-greeter";
    platforms = lib.platforms.linux;
  };
}
